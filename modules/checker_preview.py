"""Shared checker asset and migration cleanup for the retired material preview."""

import os

import bpy
from bpy.app.handlers import persistent


_ASSET_FILE = "PMVR_UV_Checker.png"
_OWNER_KEY = "pm_vr_checker_preview"
_LEGACY_MATERIAL_NAME = "PMVR_CheckerPreview"


class PMVR_CheckerSlotState(bpy.types.PropertyGroup):
    """Legacy state kept only long enough to restore old material overrides."""

    object: bpy.props.PointerProperty(type=bpy.types.Object)
    slot_index: bpy.props.IntProperty(default=-1)
    original_link: bpy.props.StringProperty(default='DATA')
    original_material: bpy.props.PointerProperty(type=bpy.types.Material)
    local_enabled: bpy.props.BoolProperty(default=False)
    global_enabled: bpy.props.BoolProperty(default=False)


class PMVR_CheckerMeshState(bpy.types.PropertyGroup):
    """Legacy placeholder-mesh state used during migration cleanup."""

    mesh: bpy.props.PointerProperty(type=bpy.types.Mesh)


def _asset_path():
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_root, "assets", _ASSET_FILE)


def get_checker_image():
    """Return the shared A1-H8 image used by the GPU debug renderer."""
    path = _asset_path()
    if not os.path.isfile(path):
        raise RuntimeError(f"Checker asset is missing: {path}")
    normalized = os.path.normcase(os.path.abspath(path))
    for image in bpy.data.images:
        image_path = bpy.path.abspath(image.filepath, library=image.library)
        if image_path and os.path.normcase(os.path.abspath(image_path)) == normalized:
            image[_OWNER_KEY] = True
            image.use_fake_user = True
            try:
                image.colorspace_settings.name = 'sRGB'
            except (TypeError, ValueError):
                pass
            return image
    image = bpy.data.images.load(path, check_existing=True)
    image[_OWNER_KEY] = True
    image.use_fake_user = True
    try:
        image.colorspace_settings.name = 'sRGB'
    except (TypeError, ValueError):
        pass
    return image


def _restore_legacy_layer(view_layer):
    slot_states = view_layer.pm_vr_checker_slot_states
    mesh_states = view_layer.pm_vr_checker_mesh_states
    had_state = bool(
        len(slot_states)
        or len(mesh_states)
        or view_layer.pm_vr_checker_enabled
    )
    for state in slot_states:
        obj = state.object
        if not obj or state.slot_index < 0 or state.slot_index >= len(obj.material_slots):
            continue
        try:
            slot = obj.material_slots[state.slot_index]
            slot.link = state.original_link
            if state.original_link == 'OBJECT':
                slot.material = state.original_material
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    for state in mesh_states:
        mesh = state.mesh
        try:
            if mesh and len(mesh.materials) == 1 and mesh.materials[0] is None:
                mesh.materials.pop(index=0)
        except (ReferenceError, RuntimeError, TypeError):
            pass
    slot_states.clear()
    mesh_states.clear()
    view_layer.pm_vr_checker_enabled = False
    return had_state


def _remove_unused_legacy_materials():
    for material in tuple(bpy.data.materials):
        if not (
            material.name == _LEGACY_MATERIAL_NAME
            or material.get(_OWNER_KEY)
        ):
            continue
        material.use_fake_user = False
        if material.users == 0:
            bpy.data.materials.remove(material)


def _restore_legacy_overrides():
    scenes = getattr(bpy.data, "scenes", ())
    restored = 0
    for scene in scenes:
        for view_layer in scene.view_layers:
            restored += int(_restore_legacy_layer(view_layer))
    _remove_unused_legacy_materials()
    if restored:
        print(
            f"[PM VR][Checker] Restored {restored} legacy material "
            "override layer(s)"
        )


def _update_tiling(_scene, _context):
    try:
        from .pipeline import viewport_overlay

        viewport_overlay.tag_redraw()
    except (ImportError, RuntimeError):
        pass


@persistent
def _load_post(_filepath):
    _restore_legacy_overrides()


CLASSES = (
    PMVR_CheckerSlotState,
    PMVR_CheckerMeshState,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pm_vr_checker_tiling = bpy.props.IntProperty(
        name="Checker Tiling",
        description="Number of checker repetitions across the 0-1 UV tile",
        default=1,
        min=1,
        max=32,
        soft_max=8,
        update=_update_tiling,
    )
    bpy.types.ViewLayer.pm_vr_checker_enabled = bpy.props.BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    bpy.types.ViewLayer.pm_vr_checker_slot_states = bpy.props.CollectionProperty(
        type=PMVR_CheckerSlotState,
        options={'HIDDEN'},
    )
    bpy.types.ViewLayer.pm_vr_checker_mesh_states = bpy.props.CollectionProperty(
        type=PMVR_CheckerMeshState,
        options={'HIDDEN'},
    )
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if getattr(bpy.data, "scenes", None) is not None:
        _restore_legacy_overrides()


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if getattr(bpy.data, "scenes", None) is not None:
        _restore_legacy_overrides()
    for property_owner, property_name in (
        (bpy.types.ViewLayer, "pm_vr_checker_mesh_states"),
        (bpy.types.ViewLayer, "pm_vr_checker_slot_states"),
        (bpy.types.ViewLayer, "pm_vr_checker_enabled"),
        (bpy.types.Scene, "pm_vr_checker_tiling"),
    ):
        if hasattr(property_owner, property_name):
            delattr(property_owner, property_name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
