"""Interactive regression: Setup lists follow the active object after File > Open.

Blender drops message-bus subscriptions on file load, so this needs a real
window and cannot run with --background:

    blender --factory-startup --python tests/blender_gui_selection_sync.py

Prints PM_VR_GUI_SELECTION_SYNC_OK or PM_VR_GUI_SELECTION_SYNC_FAILED and quits.
"""

import os
import sys
import tempfile
import traceback

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402

STATE = {"step": 0, "results": {}}


def setup():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    for index, name in enumerate(("First", "Second")):
        layer = project.render_layers.add()
        layer.layer_id = new_id()
        layer.display_name = name
        layer.layer_type = 'GLASS'
        obj = bpy.data.objects.new(f"{name} Object", None)
        scene.collection.objects.link(obj)
        obj.pm_vr_pipeline.source_id = new_id()
        obj.pm_vr_pipeline.is_registered_source = True
        obj.pm_vr_pipeline.render_layer_id = layer.layer_id
        obj.pm_vr_pipeline.processing_role = 'EXPORT_ORIGINAL'
    scene.pm_vr_ui_state.stage = 'SETUP'
    STATE["blend"] = os.path.join(tempfile.mkdtemp(prefix="pmvr_gui_sync_"), "sync.blend")


def activate(name):
    bpy.context.view_layer.objects.active = bpy.data.objects[name]


def tick():
    project = getattr(bpy.context.scene, "pm_vr_project", None)
    try:
        step = STATE["step"]
        if step == 0:
            setup()
            activate("First Object")
        elif step == 1:
            bpy.context.scene.pm_vr_project.active_render_layer_index = 0
            activate("Second Object")
        elif step == 2:
            STATE["results"]["before_load"] = project.active_render_layer_index
            activate("First Object")
            bpy.ops.wm.save_as_mainfile(filepath=STATE["blend"])
            bpy.ops.wm.open_mainfile(filepath=STATE["blend"])
        elif step == 3:
            project.active_render_layer_index = 0
            activate("Second Object")
        else:
            STATE["results"]["after_load"] = project.active_render_layer_index
            ok = STATE["results"] == {"before_load": 1, "after_load": 1}
            print(f"[PM VR GUI] active layer index: {STATE['results']}")
            print("PM_VR_GUI_SELECTION_SYNC_OK" if ok else "PM_VR_GUI_SELECTION_SYNC_FAILED")
            bpy.ops.wm.quit_blender()
            return None
        STATE["step"] += 1
        return 1.0
    except Exception:
        traceback.print_exc()
        print("PM_VR_GUI_SELECTION_SYNC_FAILED")
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=2.0, persistent=True)
