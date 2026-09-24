"""PM VR semantic production pipeline package."""

import bpy
from bpy.app.handlers import persistent

from . import (
    bake,
    data,
    export,
    scene_debug,
    selection_sync,
    setup_ops,
    ui,
    viewport_overlay,
)


@persistent
def _load_post(_filepath):
    # Files saved by earlier versions may hold generated state materials
    # without a fake user; protect them before the next save drops them.
    bake.protect_all_generated_materials()


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES):
        bpy.utils.register_class(cls)
    data.register_properties()
    selection_sync.register()
    viewport_overlay.register()
    scene_debug.register()
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    try:
        bake.protect_all_generated_materials()
    except AttributeError:
        # bpy.data is restricted while add-ons register at startup; the
        # load_post handler covers the file that is opened next.
        pass


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    bake.shutdown()
    scene_debug.unregister()
    viewport_overlay.unregister()
    selection_sync.unregister()
    data.unregister_properties()
    for cls in reversed((*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES)):
        bpy.utils.unregister_class(cls)


def draw_stage(layout, context, stage):
    if stage == 'SETUP':
        ui.draw_setup(layout, context)
    elif stage == 'BAKE':
        ui.draw_bake(layout, context)
    elif stage == 'EXPORT':
        ui.draw_export(layout, context)


def draw_scene_debug(layout, context):
    scene_debug.draw_controls(layout, context, context.scene.pm_vr_project)


def draw_ui(layout, context):
    """Compatibility entry point used by the add-on UI smoke harness."""
    ui.draw_setup(layout, context)


def request_selection_sync():
    selection_sync.request_sync()
