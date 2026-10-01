"""Interactive regression: Save During Bake keeps a crash from costing the night.

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_autosave.py

Units A, B, C, Day only, with the save interval forced due. Expected: the
file is saved after A and after B and when the queue ends; a copy taken at
the first save opens unlocked (no bake running), with A out of the queue,
no temporary bake markers left, the viewport in the user's shading (not
the bake's wireframe). A second run with the interval 0 saves nothing.
Prints PM_VR_GUI_BAKE_AUTOSAVE_OK or PM_VR_GUI_BAKE_AUTOSAVE_FAILED.
"""

import os
import shutil
import sys
import time
import traceback

import bpy

ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
sys.argv = [sys.argv[0]]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_gui_bake_resume.py"), encoding="utf8").read().split("def tick():")[0])
from PM_VR.modules.pipeline import bake as B  # noqa: E402

STATE.update({"phase": "setup", "copy": None})


def text():
    return bpy.data.texts["PMVR Pipeline Log"].as_string()


def tick():
    try:
        phase = STATE["phase"]
        if phase == "setup":
            setup()
            project().bake_evening = False
            project().bake_autosave_minutes = 30
            bpy.ops.wm.save_mainfile()
            B.autosave_due = lambda last, minutes: minutes > 0
            original = B.PMVR_OT_BakeQueue._save_file

            def save_and_copy(self, context, reason):
                original(self, context, reason)
                if reason == "during the bake" and not STATE["copy"]:
                    STATE["copy"] = STATE["path"].replace(".blend", "_mid.blend")
                    shutil.copy(STATE["path"], STATE["copy"])
            B.PMVR_OT_BakeQueue._save_file = save_and_copy
            STATE["t0"] = time.monotonic()
            bake()
            STATE["phase"] = "first"
            return 0.5
        if time.monotonic() - STATE["t0"] > 600:
            check(False, f"timeout in {phase}")
            finish()
            return None
        if phase == "first":
            if project().operation_running:
                return 0.3
            log_text = text()
            check(log_text.count("Saved the file during the bake") == 2, f"saves during the bake: {log_text.count('Saved the file during the bake')}")
            check(log_text.count("Saved the file at the end of the bake") == 1, "no save at the end")
            check(not bpy.data.is_dirty, "file dirty after the queue ended")
            # Nothing is saved with the interval 0.
            project().bake_autosave_minutes = 0
            for name in "ABC":
                project().bake_queue.add().unit_id = unit_id(name)
            STATE["mtime"] = os.path.getmtime(STATE["path"])
            STATE["saves"] = text().count("Saved the file")
            bake()
            STATE["phase"] = "second"
            return 0.5
        if phase == "second":
            if project().operation_running:
                return 0.3
            check(text().count("Saved the file") == STATE["saves"], "saved with the interval 0")
            check(os.path.getmtime(STATE["path"]) == STATE["mtime"], "file written with the interval 0")
            bpy.ops.wm.open_mainfile(filepath=STATE["copy"])
            STATE["phase"] = "reopened"
            return 0.5
        if phase == "reopened":
            names = [n for n, _d, _e in queue()]
            check(names == ["B", "C"], f"queue in the mid-bake copy {names}")
            check(not project().operation_running, "mid-bake copy opens with a bake running")
            markers = [o.name for o in bpy.data.objects if any(k.startswith("pmvr_bake_restore") for k in o.keys())]
            check(not markers, f"restore markers left: {markers}")
            check(not any(c.get("pmvr_temporary_work") for c in bpy.data.collections), "PMVR_WORK left in the copy")
            shading = [
                space.shading.type for window in bpy.context.window_manager.windows
                for area in window.screen.areas if area.type == 'VIEW_3D' for space in area.spaces if space.type == 'VIEW_3D'
            ]
            check(shading and 'WIREFRAME' not in shading, f"viewport saved as {shading}")
            finish()
            return None
        return 0.3
    except Exception:
        traceback.print_exc()
        check(False, "exception")
        finish()
        return None


def finish():
    for problem in STATE["problems"]:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_BAKE_AUTOSAVE_FAILED" if STATE["problems"] else "PM_VR_GUI_BAKE_AUTOSAVE_OK")
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
