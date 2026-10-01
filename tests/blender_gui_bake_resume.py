"""Interactive regression: a stopped bake queue continues where it stopped.

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_resume.py

Units A, B, C queued for Day + Evening. Cancel while B's Evening bake runs,
save, reopen, Bake again. Expected: after the stop the queue holds B and C
marked Day-baked (A left it); the second run bakes only B and C Evening;
every unit has exactly one successful bake per state; the queue ends empty.
A queue baked through is refused. Prints PM_VR_GUI_BAKE_RESUME_OK or
PM_VR_GUI_BAKE_RESUME_FAILED and quits.
"""

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

STATE = {"phase": "setup", "t0": 0.0, "sent": False, "problems": []}


def check(condition, message):
    if not condition:
        STATE["problems"].append(message)


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
    project.cycles_samples = 64
    project.bake_resolution = '256'
    output = tempfile.mkdtemp(prefix="pmvr_gui_resume_")
    project.beauty_output_directory = output + os.sep
    root, day, evening = (bpy.data.collections.new(name) for name in ("Root", "Day", "Evening"))
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Day World")
    project.evening_world = bpy.data.worlds.new("Evening World")
    day.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN')))
    evening.objects.link(bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", 'POINT')))
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name = new_id(), "LO"
    for index, name in enumerate("ABC"):
        obj = add_cube(root, f"{name}0", (index * 2, 0, 0))
        unit = project.bake_units.add()
        unit.unit_id = unit.artifact_key = new_id()
        unit.display_name, unit.render_layer_id, unit.resolution = name, layer.layer_id, '256'
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'BAKE'
        meta.bake_unit_id = unit.unit_id
        project.bake_queue.add().unit_id = unit.unit_id
    project.bake_day = project.bake_evening = True
    STATE["path"] = os.path.join(output, "resume.blend")
    bpy.ops.wm.save_as_mainfile(filepath=STATE["path"])


def project():
    return bpy.context.scene.pm_vr_project


def unit_id(name):
    return next(u.unit_id for u in project().bake_units if u.display_name == name)


def successes(name, state):
    return sum(
        1 for r in project().build_records
        if r.unit_id == unit_id(name) and r.lighting_state == state and r.status == "SUCCESS"
    )


def queue():
    names = {u.unit_id: u.display_name for u in project().bake_units}
    return [(names[e.unit_id], e.day_done, e.evening_done) for e in project().bake_queue]


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def bake():
    with bpy.context.temp_override(**view3d_override()):
        return bpy.ops.pmvr.bake_queue('INVOKE_DEFAULT')


def finish():
    for problem in STATE["problems"]:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_BAKE_RESUME_FAILED" if STATE["problems"] else "PM_VR_GUI_BAKE_RESUME_OK")
    bpy.ops.wm.quit_blender()


def tick():
    try:
        phase = STATE["phase"]
        if time.monotonic() - STATE["t0"] > 900 and phase != "setup":
            check(False, f"timeout in {phase}")
            finish()
            return None
        if phase == "setup":
            setup()
            STATE["t0"] = time.monotonic()
            bake()
            STATE["phase"] = "first"
            return 0.2
        if phase == "first":
            if not STATE["sent"] and successes("A", 'EVENING') and bpy.app.is_job_running('OBJECT_BAKE'):
                with bpy.context.temp_override(**view3d_override()):
                    bpy.ops.pmvr.cancel_bake_queue()
                STATE["sent"] = True
            if STATE["sent"] and not project().operation_running:
                check(queue() == [("B", True, False), ("C", True, False)], f"queue after stop {queue()}")
                check("cancelled" in project().last_operation_summary, project().last_operation_summary)
                bpy.ops.wm.save_mainfile()
                bpy.ops.wm.open_mainfile(filepath=STATE["path"])
                STATE["phase"] = "reopened"
            return 0.2
        if phase == "reopened":
            check(queue() == [("B", True, False), ("C", True, False)], f"queue after reopen {queue()}")
            bake()
            STATE["phase"] = "second"
            return 0.5
        if phase == "second":
            if project().operation_running:
                return 0.2
            check(queue() == [], f"queue after the second run {queue()}")
            for name in "ABC":
                for state in ('DAY', 'EVENING'):
                    check(successes(name, state) == 1, f"{name} {state}: {successes(name, state)} successful bakes")
            text = bpy.data.texts["PMVR Pipeline Log"].as_string()
            check("Continuing the queue: 2 unit bake(s) already done in it are skipped" in text, "no continue line")
            # A queue baked through is refused.
            entry = project().bake_queue.add()
            entry.unit_id = unit_id("A")
            entry.day_done = entry.evening_done = True
            try:
                refused = bake() == {'CANCELLED'}
            except RuntimeError:
                refused = True
            check(refused, "a queue baked through started again")
            check("already baked" in project().last_operation_summary, project().last_operation_summary)
            finish()
            return None
        return 0.2
    except Exception:
        traceback.print_exc()
        check(False, "exception")
        finish()
        return None


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
