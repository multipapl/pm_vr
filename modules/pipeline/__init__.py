"""PM VR semantic production pipeline package."""

import bpy

from . import bake, data, export, setup_ops, ui


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES):
        bpy.utils.register_class(cls)
    data.register_properties()


def unregister():
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


def draw_ui(layout, context):
    """Compatibility entry point used by the add-on UI smoke harness."""
    ui.draw_setup(layout, context)
