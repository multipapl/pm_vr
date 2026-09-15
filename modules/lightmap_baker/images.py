"""Float image creation and transactional scene-linear EXR export."""

import os
import re
import uuid

import bpy

from .constants import TAG_EXPORT_PATH


INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def set_scene_linear_colorspace(image):
    candidates = ("Linear Rec.709", "Linear", "scene_linear", "Non-Color")
    if image.colorspace_settings.name in candidates:
        return image.colorspace_settings.name
    for colorspace in candidates:
        try:
            image.colorspace_settings.name = colorspace
            return colorspace
        except (TypeError, ValueError):
            continue
    return image.colorspace_settings.name


def create_float_image(name, resolution, background=(0.0, 0.0, 0.0, 1.0)):
    image = bpy.data.images.new(
        name=name,
        width=resolution,
        height=resolution,
        alpha=False,
        float_buffer=True,
        is_data=False,
        tiled=False,
    )
    image.generated_color = background
    image.generated_type = 'BLANK'
    image.file_format = 'OPEN_EXR'
    set_scene_linear_colorspace(image)
    return image


def remove_image(image):
    if image and image.users == 0:
        bpy.data.images.remove(image)


def _safe_filename(value):
    safe = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    return safe or "Lightmap"


def build_final_path(settings, source):
    directory = bpy.path.abspath(settings.output_directory)
    if not directory:
        raise RuntimeError("output directory is empty")
    filename = f"{_safe_filename(source.name)}_LM.exr"
    return os.path.abspath(os.path.join(directory, filename))


def validate_export_path(final_path, old_images):
    if not os.path.isfile(final_path):
        return

    normalized = os.path.normcase(os.path.abspath(final_path))
    for image in old_images:
        old_path = image.get(TAG_EXPORT_PATH, "")
        if old_path and os.path.normcase(os.path.abspath(old_path)) == normalized:
            return

    raise RuntimeError(
        f'output file already exists and is not owned by this baker: "{final_path}"'
    )


def _set_standard_view(owner):
    try:
        owner.view_settings.view_transform = "Standard"
    except (TypeError, ValueError):
        pass
    try:
        owner.view_settings.look = "None"
    except (TypeError, ValueError):
        pass
    owner.view_settings.exposure = 0.0
    owner.view_settings.gamma = 1.0


def save_linear_exr(image, filepath):
    export_scene = bpy.data.scenes.new(
        name=f"__PM_LM_EXPORT_{uuid.uuid4().hex}"
    )
    try:
        settings = export_scene.render.image_settings
        settings.file_format = 'OPEN_EXR'
        settings.color_mode = 'RGB'
        settings.color_depth = '32'
        settings.exr_codec = 'PIZ'
        if hasattr(settings, "color_management"):
            try:
                settings.color_management = 'OVERRIDE'
            except (TypeError, ValueError):
                pass
        _set_standard_view(export_scene)
        if hasattr(settings, "view_settings"):
            _set_standard_view(settings)
        image.save_render(filepath, scene=export_scene)
    finally:
        bpy.data.scenes.remove(export_scene)


class StagedExport:
    def __init__(self, final_path, staging_path):
        self.final_path = final_path
        self.staging_path = staging_path
        self.backup_path = ""
        self.committed = False

    def commit(self):
        if os.path.exists(self.final_path):
            self.backup_path = (
                f"{self.final_path}.pm_lm_backup_{uuid.uuid4().hex}"
            )
            os.replace(self.final_path, self.backup_path)

        try:
            os.replace(self.staging_path, self.final_path)
            self.committed = True
        except Exception:
            if self.backup_path and os.path.exists(self.backup_path):
                os.replace(self.backup_path, self.final_path)
                self.backup_path = ""
            raise

    def assign_to_image(self, image):
        if bpy.data.filepath:
            try:
                filepath = bpy.path.relpath(self.final_path)
            except ValueError:
                filepath = self.final_path
        else:
            filepath = self.final_path
        image.filepath = filepath
        image.filepath_raw = filepath
        image.source = 'FILE'
        image.file_format = 'OPEN_EXR'
        set_scene_linear_colorspace(image)
        image.reload()

    def rollback(self):
        try:
            if self.committed and os.path.isfile(self.final_path):
                os.remove(self.final_path)
            if self.backup_path and os.path.exists(self.backup_path):
                os.replace(self.backup_path, self.final_path)
                self.backup_path = ""
        finally:
            self.committed = False

    def finalize(self):
        if self.backup_path and os.path.isfile(self.backup_path):
            try:
                os.remove(self.backup_path)
            except OSError:
                pass
        self.backup_path = ""

    def cleanup(self):
        if self.staging_path and os.path.isfile(self.staging_path):
            try:
                os.remove(self.staging_path)
            except OSError:
                pass


def stage_export(image, final_path):
    directory = os.path.dirname(final_path)
    os.makedirs(directory, exist_ok=True)
    staging_path = os.path.join(
        directory,
        f".pm_lm_{uuid.uuid4().hex}.exr",
    )
    staged = StagedExport(final_path, staging_path)
    try:
        save_linear_exr(image, staging_path)
    except Exception:
        staged.cleanup()
        raise
    return staged
