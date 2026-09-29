"""Temporary EXR I/O used by the scene compositor."""

from contextlib import contextmanager
import os
import tempfile

import bpy
import numpy

from .images import save_linear_exr, set_scene_linear_colorspace


def _linear_to_srgb(values):
    return numpy.where(values <= 0.0031308, values * 12.92, 1.055 * numpy.power(numpy.maximum(values, 0.0), 1.0 / 2.4) - 0.055)


def copy_pixels(source, target):
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
    # One buffer copy; slicing pixels built Python lists and took ~27 s at 4K.
    buffer = numpy.empty(total, dtype=numpy.float32)
    source.pixels.foreach_get(buffer)
    if target.colorspace_settings.name == 'sRGB':
        # The result is linear. A float image tagged sRGB (the Beauty bake)
        # holds sRGB-encoded values, as Cycles writes them there.
        rgba = buffer.reshape(-1, 4)
        rgba[:, :3] = _linear_to_srgb(rgba[:, :3])
    target.pixels.foreach_set(buffer)
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
def temporary_compositor_inputs(image, albedo_guide=None, normal_guide=None):
    with tempfile.TemporaryDirectory(prefix="pm_lightmap_denoise_") as folder:
        images = []
        try:
            sources = [(image, "lightmap")]
            if albedo_guide is not None:
                sources.append((albedo_guide, "albedo"))
            if normal_guide is not None:
                sources.append((normal_guide, "normal"))
            for source, name in sources:
                images.append(stage_input_image(source, folder, name))
            yield folder, images
        finally:
            for input_image in images:
                try:
                    bpy.data.images.remove(input_image)
                except ReferenceError:
                    pass
