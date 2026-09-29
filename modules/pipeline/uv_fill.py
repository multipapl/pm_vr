"""Fill the empty UV space of a baked atlas with its islands' own colours.

Outside the UV islands and their bake margin an atlas is black. Mipmaps
average that black into island edges once a texel spans more than the
margin, which the export's downscale makes happen early. Every pixel
outside the islands gets a smooth continuation of the nearby island
colours instead (pull-push: coarser and coarser averages of island pixels
only, pushed back down), computed in linear light from the final 8-bit
pixels, so no colour management touches it. Island pixels stay as baked."""

import os
import uuid

import numpy

import bpy

from ..lightmap_baker.images import replace_file
from .bake_files import _linear_to_srgb, _srgb_to_linear, write_encoded
from .constants import BAKE_UV_NAME

# The bake margin holds colour; below this 8-bit code a margin pixel is
# background (it reads 0 or 1 after the view transform and denoise).
BACKGROUND_CODE = 2


def island_triangles(meshes, width, height):
    """SimpleBake triangles of the meshes in pixel units, rows from the
    bottom as Blender stores them."""
    parts = []
    for mesh in meshes:
        uv_layer = mesh.uv_layers.get(BAKE_UV_NAME)
        if not uv_layer:
            continue
        mesh.calc_loop_triangles()
        uvs = numpy.empty(len(uv_layer.data) * 2, dtype=numpy.float64)
        uv_layer.data.foreach_get("uv", uvs)
        loops = numpy.empty(len(mesh.loop_triangles) * 3, dtype=numpy.int64)
        mesh.loop_triangles.foreach_get("loops", loops)
        triangles = uvs.reshape(-1, 2)[loops.reshape(-1, 3)]
        parts.append(triangles * (width, height))
    return numpy.concatenate(parts) if parts else numpy.zeros((0, 3, 2))


def _inside(triangles, px, py):
    """Pixel centres inside each triangle, edges included."""
    ax, ay = triangles[:, 0, 0, None, None], triangles[:, 0, 1, None, None]
    bx, by = triangles[:, 1, 0, None, None], triangles[:, 1, 1, None, None]
    cx, cy = triangles[:, 2, 0, None, None], triangles[:, 2, 1, None, None]
    area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    with numpy.errstate(divide='ignore', invalid='ignore'):
        first = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / area
        second = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / area
    epsilon = 1e-6
    return (
        (numpy.abs(area) > 1e-12)
        & (first >= -epsilon) & (second >= -epsilon) & (first + second <= 1 + epsilon)
    )


def rasterize(triangles, width, height):
    """Pixels whose centre lies in a triangle. Triangles are grouped by the
    size of their bounding box and tested together."""
    mask = numpy.zeros((height, width), dtype=bool)
    if not len(triangles):
        return mask
    low = numpy.floor(triangles.min(axis=1) - 0.5).astype(numpy.int64)
    high = numpy.ceil(triangles.max(axis=1) - 0.5).astype(numpy.int64)
    low = numpy.maximum(low, 0)
    high = numpy.minimum(high, (width - 1, height - 1))
    span = (high - low + 1).max(axis=1)
    visible = (high >= low).all(axis=1)
    done = ~visible
    for tile in (2, 4, 8, 16, 32, 64, 128):
        chosen = numpy.nonzero(~done & (span <= tile))[0]
        done[chosen] = True
        step = max(1, 4_000_000 // (tile * tile))
        offsets = numpy.arange(tile)
        for start in range(0, len(chosen), step):
            batch = chosen[start:start + step]
            px = low[batch, 0, None, None] + offsets[None, None, :]
            py = low[batch, 1, None, None] + offsets[None, :, None]
            inside = _inside(triangles[batch], px + 0.5, py + 0.5)
            inside &= (px <= high[batch, 0, None, None]) & (py <= high[batch, 1, None, None])
            index = numpy.nonzero(inside)
            mask[py[index[0], index[1], 0], px[index[0], 0, index[2]]] = True
    for number in numpy.nonzero(~done)[0]:
        (x0, y0), (x1, y1) = low[number], high[number]
        px, py = numpy.meshgrid(numpy.arange(x0, x1 + 1), numpy.arange(y0, y1 + 1))
        inside = _inside(triangles[number:number + 1], px[None] + 0.5, py[None] + 0.5)[0]
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
    return mask


def _dilate(mask, steps):
    for _step in range(steps):
        grown = mask.copy()
        grown[1:] |= mask[:-1]
        grown[:-1] |= mask[1:]
        grown[:, 1:] |= mask[:, :-1]
        grown[:, :-1] |= mask[:, 1:]
        mask = grown
    return mask


def _upsample(values, height, width):
    """Twice the size by bilinear interpolation of pixel centres, cropped."""
    for axis in (0, 1):
        count = values.shape[axis]
        previous = numpy.take(values, numpy.r_[0, 0:count - 1], axis=axis)
        following = numpy.take(values, numpy.r_[1:count, count - 1], axis=axis)
        stacked = numpy.stack([0.75 * values + 0.25 * previous, 0.75 * values + 0.25 * following], axis=axis + 1)
        shape = list(values.shape)
        shape[axis] *= 2
        values = stacked.reshape(shape)
    return values[:height, :width]


def pull_push(linear, weight):
    """Keep pixels of weight 1; fill the rest from averages of weighted
    pixels at coarser levels, pushed back up bilinearly."""
    levels = []
    color, weight = linear, weight.astype(numpy.float32)
    while color.shape[0] > 1 or color.shape[1] > 1:
        levels.append((color, weight))
        height, width = color.shape[:2]
        half_h, half_w = (height + 1) // 2, (width + 1) // 2
        padded = numpy.zeros((half_h * 2, half_w * 2, 3), dtype=numpy.float32)
        padded[:height, :width] = color * weight[..., None]
        padded_weight = numpy.zeros((half_h * 2, half_w * 2), dtype=numpy.float32)
        padded_weight[:height, :width] = weight
        sums = padded.reshape(half_h, 2, half_w, 2, 3).sum(axis=(1, 3))
        weights = padded_weight.reshape(half_h, 2, half_w, 2).sum(axis=(1, 3))
        color = numpy.where(
            weights[..., None] > 0, sums / numpy.maximum(weights, 1e-12)[..., None], 0.0
        ).astype(numpy.float32)
        weight = numpy.minimum(weights, 1.0)
    filled = color
    for level_color, level_weight in reversed(levels):
        height, width = level_color.shape[:2]
        filled = (
            level_weight[..., None] * level_color
            + (1.0 - level_weight[..., None]) * _upsample(filled, height, width)
        )
    return filled


def _load_codes(path):
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return numpy.rint(pixels.reshape(height, width, 4)[..., :3] * 255.0).astype(numpy.uint8)


def fill_png(path, meshes, margin):
    """Fill the atlas at path outside the SimpleBake islands of meshes and
    the colour their bake margin (pixels) left around them. Island pixels
    are written back unchanged. Returns the share of the atlas filled."""
    codes = _load_codes(path)
    height, width = codes.shape[:2]
    islands = rasterize(island_triangles(meshes, width, height), width, height)
    ring = _dilate(islands, max(1, int(margin))) & ~islands
    keep = islands | (ring & (codes.max(axis=-1) > BACKGROUND_CODE))
    empty = float(1.0 - keep.mean())
    if not keep.any() or empty == 0.0:
        return 0.0
    table = _srgb_to_linear(numpy.arange(256, dtype=numpy.float64) / 255.0).astype(numpy.float32)
    filled = pull_push(table[codes], keep)
    encoded = numpy.where(
        keep[..., None], codes / 255.0, numpy.rint(_linear_to_srgb(filled) * 255.0) / 255.0
    )
    # A new file, then a replace: the staged PNG may be open in another
    # process for a moment (the texture cache reads new atlases).
    filled_path = f"{os.path.splitext(path)[0]}.fill_{uuid.uuid4().hex[:8]}.png"
    try:
        write_encoded(filled_path, encoded)
        replace_file(filled_path, path)
    finally:
        if os.path.exists(filled_path):
            os.remove(filled_path)
    return empty
