"""Temporary EXR I/O used by the scene compositor."""

from contextlib import contextmanager
import os
import tempfile

import bpy

from .images import save_linear_exr, set_scene_linear_colorspace


def copy_pixels(source, target, chunk_size=262_144):
    if tuple(source.size) == (0, 0) or not source.has_data:
        source.reload()
        try:
            source.pixels[0]
        except (IndexError, RuntimeError):
            pass
    if tuple(source.size) != tuple(target.size):
        raise RuntimeError(
            f"denoise result size {tuple(source.size)} does not match "
            f"target size {tuple(target.size)} "
            f"(source={source.source}, type={source.type}, "
            f"has_data={source.has_data})"
        )
    total = len(target.pixels)
    if len(source.pixels) != total:
        raise RuntimeError("denoise result channel count does not match target")

    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        target.pixels[start:end] = source.pixels[start:end]
    target.update()


def load_compositor_exr(filepath, folder):
    image = bpy.data.images.load(filepath, check_existing=False)
    if tuple(image.size) != (0, 0):
        return image

    bpy.data.images.remove(image)
    try:
        import OpenImageIO as oiio
    except ImportError as exc:
        raise RuntimeError(
            "Blender could not load the compositor EXR"
        ) from exc

    source = oiio.ImageBuf(filepath)
    spec = source.spec()
    if spec.width < 1 or spec.height < 1 or spec.nchannels < 3:
        raise RuntimeError("compositor EXR contains no readable image layer")

    channel_order = tuple(range(min(4, spec.nchannels)))
    channel_names = ("R", "G", "B", "A")[:len(channel_order)]
    flattened = oiio.ImageBufAlgo.channels(
        source,
        channel_order,
        channel_names,
    )
    flat_path = os.path.join(folder, "denoised_flat.exr")
    if not flattened.write(flat_path):
        raise RuntimeError(flattened.geterror() or "could not flatten EXR")

    image = bpy.data.images.load(flat_path, check_existing=False)
    image.reload()
    try:
        image.pixels[0]
    except (IndexError, RuntimeError):
        pass
    if tuple(image.size) == (0, 0):
        bpy.data.images.remove(image)
        raise RuntimeError("flattened compositor EXR could not be loaded")
    return image


def stage_input_image(source, folder, name):
    filepath = os.path.join(folder, f"{name}.exr")
    save_linear_exr(source, filepath)
    image = bpy.data.images.load(filepath, check_existing=False)
    set_scene_linear_colorspace(image)
    return image


@contextmanager
def temporary_compositor_inputs(image, albedo_guide, normal_guide):
    with tempfile.TemporaryDirectory(prefix="pm_lightmap_denoise_") as folder:
        images = []
        try:
            for source, name in (
                (image, "lightmap"),
                (albedo_guide, "albedo"),
                (normal_guide, "normal"),
            ):
                images.append(stage_input_image(source, folder, name))
            yield folder, images
        finally:
            for input_image in images:
                try:
                    bpy.data.images.remove(input_image)
                except ReferenceError:
                    pass
