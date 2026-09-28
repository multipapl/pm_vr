"""External Beauty/Lightmap files, staged beside their final path until commit."""

import os
import struct
import uuid

import bpy
import numpy

from ..lightmap_baker.images import StagedExport, save_linear_exr
from .bake_scene import PipelineBakeError
from .identity import find_layer, safe_stem


def _output_directory(value, label):
    if value.startswith("//") and not bpy.data.filepath:
        raise PipelineBakeError(f"Save the .blend file before using a relative {label} directory")
    directory = bpy.path.abspath(value)
    os.makedirs(directory, exist_ok=True)
    return directory


def _staging_path(final_path):
    directory, filename = os.path.split(final_path)
    stem, extension = os.path.splitext(filename)
    return os.path.join(directory, f".{stem}.pmvr_tmp_{uuid.uuid4().hex[:12]}{extension}")


def _blend_relative(filepath):
    """Path relative to the .blend when possible. A folder on another drive
    has no relative path, so it stays absolute."""
    if not bpy.data.filepath:
        return filepath
    try:
        return bpy.path.relpath(filepath)
    except ValueError:
        return filepath


def _point_image_at_file(image, filepath, file_format):
    image.filepath = _blend_relative(filepath)
    image.filepath_raw = image.filepath
    image.source = 'FILE'
    image.file_format = file_format
    image.reload()


def _beauty_final_path(context, unit, state, variant=""):
    project = context.scene.pm_vr_project
    directory = _output_directory(project.beauty_output_directory, "Beauty")
    suffix = ("" if not variant else f"_{safe_stem(variant)}") + ("" if state == 'DAY' else "_Evening")
    layer = find_layer(project, unit.render_layer_id)
    layer_name = layer.display_name if layer else "Layer"
    stem = safe_stem(f"{layer_name}_{unit.display_name}")
    collision = next((
        other for other in project.bake_units
        if other.unit_id != unit.unit_id
        and safe_stem(
            f"{(find_layer(project, other.render_layer_id).display_name if find_layer(project, other.render_layer_id) else 'Layer')}_"
            f"{other.display_name}"
        ).casefold() == stem.casefold()
    ), None)
    if collision:
        # Windows and macOS file names ignore case; both units get their
        # stable key so neither overwrites the other's atlas.
        stem = f"{stem}_{unit.artifact_key[:8]}"
    return os.path.join(directory, f"{stem}{suffix}_Beauty.png")


def stage_beauty_image(context, unit, state, image, variant=""):
    """Write the Beauty PNG beside its final path; the final file is untouched."""
    staged = StagedExport(
        _beauty_final_path(context, unit, state, variant),
        "",
    )
    staged.staging_path = _staging_path(staged.final_path)
    export_scene = bpy.data.scenes.new(
        f"__PMVR_BEAUTY_EXPORT_{uuid.uuid4().hex}"
    )
    try:
        settings = export_scene.render.image_settings
        settings.file_format = 'PNG'
        settings.color_mode = 'RGB'
        settings.color_depth = '8'
        source_view = context.scene.view_settings
        target_view = export_scene.view_settings
        for attribute in (
            "view_transform", "look", "exposure", "gamma",
        ):
            try:
                setattr(target_view, attribute, getattr(source_view, attribute))
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            export_scene.display_settings.display_device = (
                context.scene.display_settings.display_device
            )
            export_scene.sequencer_colorspace_settings.name = (
                context.scene.sequencer_colorspace_settings.name
            )
        except (AttributeError, TypeError, ValueError):
            pass
        image.save_render(staged.staging_path, scene=export_scene)
        # Later stages (denoise, material preview) read the staged pixels.
        _point_image_at_file(image, staged.staging_path, 'PNG')
    except Exception:
        staged.cleanup()
        raise
    finally:
        bpy.data.scenes.remove(export_scene)
    return staged


def png_size(path):
    """(width, height) from a PNG header, without loading the image."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def _srgb_to_linear(values):
    return numpy.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(values):
    values = numpy.clip(values, 0.0, None)
    return numpy.where(values <= 0.0031308, values * 12.92, 1.055 * values ** (1.0 / 2.4) - 0.055)


def _area_average(values, size, axis):
    """Shrink one axis to size: each output pixel averages the source pixels
    it covers, weighted by covered area. The mean is kept exactly, for any
    ratio (3072 to 2048 as well as 4096 to 1024)."""
    count = values.shape[axis]
    if count == size:
        return values
    integral = numpy.cumsum(values, axis=axis, dtype=numpy.float64)
    integral = numpy.insert(integral, 0, 0.0, axis=axis)
    edges = numpy.arange(size + 1, dtype=numpy.float64) * (count / size)
    lower = numpy.minimum(numpy.floor(edges), count - 1).astype(numpy.int64)
    shape = [1] * values.ndim
    shape[axis] = size + 1
    fraction = (edges - lower).reshape(shape)
    base = numpy.take(integral, lower, axis=axis)
    at_edges = base + fraction * (numpy.take(integral, lower + 1, axis=axis) - base)
    return numpy.diff(at_edges, axis=axis) * (size / count)


def load_linear_channels(path):
    """The R, G and B planes of an image file in linear light (rows from the
    bottom, as Blender stores them)."""
    source = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = source.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        source.pixels.foreach_get(pixels)
        is_float = source.is_float
    finally:
        bpy.data.images.remove(source)
    rgb = pixels.reshape(height, width, 4)[..., :3]
    if is_float:
        # A float file is already linear.
        return [rgb[..., channel].copy() for channel in range(3)]
    # An 8-bit file gives its encoded values; a table converts all 256.
    table = _srgb_to_linear(numpy.arange(256, dtype=numpy.float64) / 255.0)
    codes = numpy.rint(rgb * 255.0).astype(numpy.uint8)
    return [table[codes[..., channel]] for channel in range(3)]


def write_encoded(path, encoded, file_format='PNG', quality=90):
    """Write sRGB-encoded (h, w, 3) values 0-1 as an 8-bit file, byte for
    byte: no view transform."""
    height, width, _channels = encoded.shape
    pixels = numpy.ones((height, width, 4), dtype=numpy.float32)
    pixels[..., :3] = numpy.clip(encoded, 0.0, 1.0)
    image = bpy.data.images.new(f"__PMVR_WRITE_{uuid.uuid4().hex}", width, height, alpha=False)
    try:
        image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw = path
        image.file_format = file_format
        if file_format == 'JPEG':
            try:
                image.save(quality=quality)
            except TypeError:
                image.save()
        else:
            image.save()
    finally:
        bpy.data.images.remove(image)


def scaled_linear(channels, width, height=None):
    """Average planes down to width x height (square without a height) by
    covered area; linear light keeps the colour."""
    height = height or width
    return [_area_average(_area_average(plane, width, 1), height, 0) for plane in channels]


def scale_atlas(source_path, target_path, size):
    """Write the atlas at source_path as a size x size 8-bit PNG at
    target_path; the source file is not touched.

    Pixels are averaged in linear light by covered area, as the headset's
    own texture filtering does, so colour and brightness stay as baked;
    averaging the sRGB-encoded values would darken fine contrast."""
    linear = scaled_linear(load_linear_channels(source_path), size)
    write_encoded(target_path, numpy.stack([_linear_to_srgb(plane) for plane in linear], axis=-1))


def write_swatch(atlas_path, target_path, size=256, view=1024):
    """A size x size JPEG cut from the atlas shown at view px: of a grid of
    candidate squares, the one with the least empty (black) space, nearest
    the centre on a tie."""
    linear = scaled_linear(load_linear_channels(atlas_path), view)
    encoded = numpy.stack([_linear_to_srgb(plane) for plane in linear], axis=-1)
    empty = encoded.max(axis=2) < 2.0 / 255.0
    best = None
    step = size // 2
    centre = (view - size) / 2
    for y in range(0, view - size + 1, step):
        for x in range(0, view - size + 1, step):
            score = (float(empty[y:y + size, x:x + size].mean()), abs(x - centre) + abs(y - centre))
            if best is None or score < best[0]:
                best = (score, x, y)
    _score, x, y = best
    write_encoded(target_path, encoded[y:y + size, x:x + size], file_format='JPEG')


def beauty_image_name(layer, unit, state):
    return (
        f"PMVR_{safe_stem(layer.display_name)}_"
        f"{safe_stem(unit.display_name)}_{state}_Beauty"
    )


def stage_lightmap_image(context, unit, state, image):
    project = context.scene.pm_vr_project
    directory = _output_directory(project.lightmap_output_directory, "Lightmap")
    suffix = "" if state == 'DAY' else "_Evening"
    final_path = os.path.join(
        directory,
        f"{safe_stem(unit.display_name)}_{unit.artifact_key[:8]}{suffix}_LM.exr",
    )
    staged = StagedExport(final_path, _staging_path(final_path))
    try:
        save_linear_exr(image, staged.staging_path)
    except Exception:
        staged.cleanup()
        raise
    return staged


def commit_staged_file(staged, image, file_format):
    """Atomically publish a staged file; the previous file stays as backup."""
    staged.commit()
    _point_image_at_file(image, staged.final_path, file_format)
