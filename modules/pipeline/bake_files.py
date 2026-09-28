"""External Beauty/Lightmap files, staged beside their final path until commit."""

import os
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


def _beauty_final_path(context, unit, state):
    project = context.scene.pm_vr_project
    directory = _output_directory(project.beauty_output_directory, "Beauty")
    suffix = "" if state == 'DAY' else "_Evening"
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


def stage_beauty_image(context, unit, state, image):
    """Write the Beauty PNG beside its final path; the final file is untouched."""
    staged = StagedExport(
        _beauty_final_path(context, unit, state),
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


def _png_scene(name, context_scene):
    scene = bpy.data.scenes.new(name)
    settings = scene.render.image_settings
    settings.file_format = 'PNG'
    settings.color_mode = 'RGB'
    settings.color_depth = '8'
    scene.view_settings.view_transform = 'Standard'
    try:
        scene.view_settings.look = 'None'
    except (TypeError, ValueError):
        pass
    try:
        scene.display_settings.display_device = context_scene.display_settings.display_device
    except (AttributeError, TypeError, ValueError):
        pass
    return scene


def downscale_staged_beauty(context, image, path, size):
    """Shrink a staged Beauty PNG (baked larger, already denoised) to size.

    Blocks are averaged in linear light, like more samples per pixel would;
    averaging the sRGB-encoded values would darken fine contrast."""
    # A fresh load of the 8-bit PNG gives its sRGB-encoded values, whatever
    # buffer the bake image itself uses.
    source = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = source.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        source.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(source)
    factor = width // size
    if factor <= 1:
        return
    rgb = pixels.reshape(height, width, 4)[..., :3]
    linear = numpy.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    linear = linear.reshape(size, factor, size, factor, 3).mean(axis=(1, 3))
    encoded = numpy.where(
        linear <= 0.0031308,
        linear * 12.92,
        1.055 * numpy.power(numpy.clip(linear, 0.0, None), 1.0 / 2.4) - 0.055,
    )
    result = numpy.ones((size, size, 4), dtype=numpy.float32)
    result[..., :3] = numpy.clip(encoded, 0.0, 1.0)
    small = bpy.data.images.new(f"__PMVR_DOWNSCALE_{uuid.uuid4().hex}", size, size, alpha=False)
    scene = _png_scene(f"__PMVR_DOWNSCALE_{uuid.uuid4().hex}", context.scene)
    try:
        small.pixels.foreach_set(result.ravel())
        small.save_render(path, scene=scene)
    finally:
        bpy.data.images.remove(small)
        bpy.data.scenes.remove(scene)
    image.reload()


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
