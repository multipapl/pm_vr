"""Interactive regression: cancelling a running Beauty queue.

Needs a real window and simulated input; it cannot run with --background:

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_cancel.py -- esc
    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_cancel.py -- button

The queue bakes unit B once, then queues A and B again and cancels while B's
Cycles job runs. Expected: A is committed, B keeps its previous PNG, generated
objects and materials, and no temporary data or hidden sources remain. The
script prints PM_VR_GUI_BAKE_CANCEL_OK or PM_VR_GUI_BAKE_CANCEL_FAILED and quits.
"""

import glob
import hashlib
import os
import sys
import tempfile
import time
import traceback

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402

MODE = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "esc"
STATE = {"phase": "setup", "t0": 0.0, "sent": False}


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def add_cube(collection, name, location):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    material = bpy.data.materials.new(f"{name} Material")
    material.use_nodes = True
    obj.data.materials.append(material)
    return obj


def setup():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.cycles.device = 'CPU'
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 48
    project.bake_resolution = '256'
    output = tempfile.mkdtemp(prefix="pmvr_gui_cancel_")
    project.beauty_output_directory = output + os.sep
    root = bpy.data.collections.new("Root")
    day = bpy.data.collections.new("Day")
    evening = bpy.data.collections.new("Evening")
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Day World")
    project.evening_world = bpy.data.worlds.new("Evening World")
    day.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN')))
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = "LO"
    for unit_name, members in (("A", [add_cube(root, "A0", (0, 3, 0))]),
                               ("B", [add_cube(root, f"B{i}", (i * 1.5, 0, 0)) for i in range(3)])):
        unit = project.bake_units.add()
        unit.unit_id = new_id()
        unit.artifact_key = unit.unit_id
        unit.display_name = unit_name
        unit.render_layer_id = layer.layer_id
        unit.resolution = '1024'
        unit_id = unit.unit_id
        for obj in members:
            metadata = obj.pm_vr_pipeline
            metadata.source_id = new_id()
            metadata.is_registered_source = True
            metadata.render_layer_id = layer.layer_id
            metadata.processing_role = 'BAKE'
            metadata.bake_unit_id = unit_id
    project.bake_day = True
    project.bake_evening = False
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "cancel.blend"))
    STATE["output"] = output


def unit(name):
    return next(u for u in bpy.context.scene.pm_vr_project.bake_units if u.display_name == name)


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def start_queue(names):
    project = bpy.context.scene.pm_vr_project
    project.bake_queue.clear()
    for name in names:
        project.bake_queue.add().unit_id = unit(name).unit_id
    with bpy.context.temp_override(**view3d_override()):
        bpy.ops.pmvr.bake_queue('INVOKE_DEFAULT')


def snapshot_b():
    unit_id = unit("B").unit_id
    return {
        "status": unit("B").day_status,
        "png": {os.path.basename(p): file_hash(p) for p in glob.glob(os.path.join(STATE["output"], "*_B_Beauty.png"))},
        "generated": sorted(
            (o.name, o.data.name, tuple(m.name for m in o.data.materials))
            for o in bpy.data.objects
            if o.get("pmvr_generated") and o.get("pmvr_unit_id") == unit_id
        ),
    }


def verify():
    project = bpy.context.scene.pm_vr_project
    problems = []
    if "cancelled" not in project.last_operation_summary:
        problems.append(f"summary does not report cancellation: {project.last_operation_summary}")
    if unit("A").day_status != "Ready":
        problems.append(f"unit A was not committed: {unit('A').day_status}")
    if snapshot_b() != STATE["b_before"]:
        problems.append(f"unit B previous result changed: {STATE['b_before']} -> {snapshot_b()}")
    if [o.name for o in bpy.data.objects if o.name.startswith("__PMVR")] or bpy.data.collections.get("PMVR_WORK"):
        problems.append("temporary bake data left behind")
    if [f for f in os.listdir(STATE["output"]) if "pmvr_tmp" in f or "pmvr_denoise" in f]:
        problems.append("staging files left behind")
    hidden = [o.name for o in bpy.data.objects if not o.get("pmvr_generated") and o.type == 'MESH' and o.hide_render]
    if hidden:
        problems.append(f"sources left hidden: {hidden}")
    if project.operation_running:
        problems.append("operation_running stayed on")
    return problems


def finish(problems):
    for problem in problems:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_BAKE_CANCEL_FAILED" if problems else "PM_VR_GUI_BAKE_CANCEL_OK")
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE["phase"] == "setup":
            setup()
            STATE["t0"] = time.monotonic()
            start_queue(["B"])
            STATE["phase"] = "first"
            return 0.3
        project = bpy.context.scene.pm_vr_project
        if time.monotonic() - STATE["t0"] > 600:
            finish(["timeout"])
            return None
        if STATE["phase"] == "first":
            if not project.operation_running:
                STATE["b_before"] = snapshot_b()
                if STATE["b_before"]["status"] != "Ready":
                    finish([f"initial bake of B failed: {project.last_operation_summary}"])
                    return None
                bpy.data.objects["Sun"].data.energy = 0.5
                start_queue(["A", "B"])
                STATE["phase"] = "second"
            return 0.3
        if not STATE["sent"] and unit("A").day_status == "Ready" and bpy.app.is_job_running('OBJECT_BAKE'):
            if MODE == "esc":
                window = bpy.context.window_manager.windows[0]
                window.event_simulate(type='ESC', value='PRESS')
                window.event_simulate(type='ESC', value='RELEASE')
            else:
                with bpy.context.temp_override(**view3d_override()):
                    bpy.ops.pmvr.cancel_bake_queue()
            STATE["sent"] = True
        if STATE["sent"] and not project.operation_running:
            finish(verify())
            return None
        return 0.3
    except Exception:
        traceback.print_exc()
        finish(["exception"])
        return None


bpy.app.timers.register(tick, first_interval=2.0)
