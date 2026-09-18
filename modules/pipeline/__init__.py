"""PM VR semantic production pipeline package."""

import bpy

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


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES):
        bpy.utils.register_class(cls)
    data.register_properties()
    selection_sync.register()
    viewport_overlay.register()
    scene_debug.register()


def unregister():
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
