"""Interactive regression: a copy keeps its own pipeline ID through undo.

The Duplicate undo step is stored before PM VR gives the copy a new ID, so
Ctrl+Z can bring the shared ID back. Needs a real window:

    blender --factory-startup --python tests/blender_gui_duplicate_undo.py

Prints PM_VR_GUI_DUPLICATE_UNDO_OK or PM_VR_GUI_DUPLICATE_UNDO_FAILED and quits.
"""

import os
import sys
import traceback

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.identity import duplicate_source_ids, ensure_source_id  # noqa: E402

STATE = {"step": 0, "problems": []}


def override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def check(label, condition):
    if not condition:
        STATE["problems"].append(label)
        print(f"[PM VR GUI] FAIL: {label}")


def tick():
    try:
        step = STATE["step"]
        if step == 0:
            PM_VR.register()
            source = bpy.data.objects["Cube"]
            ensure_source_id(source)
            STATE["source_id"] = source.pm_vr_pipeline.source_id
            bpy.ops.ed.undo_push(message="Register source")
        elif step == 1:
            with bpy.context.temp_override(**override()):
                bpy.ops.object.select_all(action='DESELECT')
                bpy.data.objects["Cube"].select_set(True)
                bpy.context.view_layer.objects.active = bpy.data.objects["Cube"]
                bpy.ops.object.duplicate('EXEC_DEFAULT', True)
        elif step == 2:
            check("copy got its own ID after Shift+D", not duplicate_source_ids())
            with bpy.context.temp_override(**override()):
                bpy.ops.transform.translate('EXEC_DEFAULT', True, value=(1.0, 0.0, 0.0))
        elif step == 3:
            with bpy.context.temp_override(**override()):
                bpy.ops.ed.undo()
        elif step == 4:
            copy = bpy.data.objects.get("Cube.001")
            check("undo kept the duplicated object", copy is not None)
            check("copy keeps its own ID after undo", not duplicate_source_ids())
            check("original keeps its ID", bpy.data.objects["Cube"].pm_vr_pipeline.source_id == STATE["source_id"])
            print("PM_VR_GUI_DUPLICATE_UNDO_FAILED" if STATE["problems"] else "PM_VR_GUI_DUPLICATE_UNDO_OK")
            bpy.ops.wm.quit_blender()
            return None
        STATE["step"] += 1
        return 1.0
    except Exception:
        traceback.print_exc()
        print("PM_VR_GUI_DUPLICATE_UNDO_FAILED")
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=2.0, persistent=True)
