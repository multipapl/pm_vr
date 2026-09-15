"""PM Lightmap Baker package."""

import bpy

from .constants import UI_CATEGORY
from . import data
from . import progress
from . import runner
from . import ui


def draw_ui(layout, context):
    ui.draw_ui(layout, context)


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *runner.CLASSES):
        bpy.utils.register_class(cls)
    data.register_scene_properties()


def unregister():
    progress.shutdown()
    data.unregister_scene_properties()
    for cls in reversed((*data.CLASSES, *ui.CLASSES, *runner.CLASSES)):
        bpy.utils.unregister_class(cls)
