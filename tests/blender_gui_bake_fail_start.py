"""Interactive regression: a queue whose every unit fails at the start.

Needs a real window; it cannot run with --background:

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_fail_start.py

The only queued unit is a PBR object whose material mixes two Principled
BSDFs, so it fails validation before any Cycles job starts. The queue used to
return from execute() after registering its modal handler, leaving Blender
with a handler for a freed operator: the status bar crashed drawing its keys
(UniPlace, 2026-09-29, twice). Now the queue ends through modal(): the window
keeps drawing, the queue is not running, the summary reports the failure.
Prints PM_VR_GUI_BAKE_FAIL_START_OK or PM_VR_GUI_BAKE_FAIL_START_FAILED and quits.
"""

import os
import sys
import time
import traceback

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import blender_resolution_smoke as base  # noqa: E402

STATE = {"phase": "setup", "t0": 0.0, "redraws": 0}


def two_principled():
    mat = bpy.data.materials.new("TwoLooks")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
    first = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    second = nodes.new("ShaderNodeBsdfPrincipled")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(first.outputs[0], mix.inputs[1])
    links.new(second.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def finish(problems):
    for problem in problems:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_BAKE_FAIL_START_FAILED" if problems else "PM_VR_GUI_BAKE_FAIL_START_OK")
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE["phase"] == "setup":
            base.PM_VR.register()
            project, root, _output = base.build()
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(_output, "fail_start.blend"))
            lamp = base.add_plane(root, "Lamp", 0.5)
            lamp.data.materials.append(two_principled())
            project.active_render_layer_index = [l.display_name for l in project.render_layers].index("PBR")
            assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
            project.bake_queue.clear()
            project.bake_queue.add().unit_id = project.bake_units[-1].unit_id
            project.bake_day, project.bake_evening = True, False
            base.activate_state(bpy.context, 'DAY')
            with bpy.context.temp_override(**view3d_override()):
                result = bpy.ops.pmvr.bake_queue('INVOKE_DEFAULT')
            print("[PM VR GUI] queue returned", result)
            STATE["result"] = result
            STATE["t0"] = time.monotonic()
            STATE["phase"] = "watch"
            return 0.2
        project = bpy.context.scene.pm_vr_project
        # Let the window draw the status bar a few times.
        STATE["redraws"] += 1
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        if STATE["redraws"] < 15:
            return 0.2
        problems = []
        if project.operation_running:
            problems.append("queue still marked running")
        if "1 failed" not in project.last_operation_summary:
            problems.append(f"summary: {project.last_operation_summary!r}")
        print("[PM VR GUI] summary:", project.last_operation_summary)
        finish(problems)
        return None
    except Exception:
        traceback.print_exc()
        finish(["exception"])
        return None


bpy.app.timers.register(tick, first_interval=2.0)
