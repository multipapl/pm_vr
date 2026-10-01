"""Interactive regression: bake scenarios in the real modal Beauty queue.

Needs a real window; it cannot run with --background:

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_scenarios.py -- finish
    blender --factory-startup --enable-event-simulate --python tests/blender_gui_bake_scenarios.py -- esc

First the queue is started with a scenario that hides a unit's own object: it
must refuse to start without touching the outliner. Then units K (layer
default "Kitchen", Bedroom off) and B (override "Bedroom", Kitchen off) are
queued. While each Cycles job runs, the collection states are sampled: K must
bake with Bedroom off, B with Kitchen off. The counter PNG must be lit (the
canopy in Bedroom would shadow it). "esc" cancels during B. Either way the
outliner must end exactly as it started and the restore marker must be gone.
Prints PM_VR_GUI_BAKE_SCENARIOS_OK or PM_VR_GUI_BAKE_SCENARIOS_FAILED and quits.
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
from PM_VR.modules.pipeline import scenarios  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402

MODE = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "finish"
STATE = {"phase": "setup", "t0": 0.0, "sent": False, "seen": {"K": set(), "B": set()}}


def layer_collection(name, root=None):
    root = root or bpy.context.view_layer.layer_collection
    for child in root.children:
        if child.collection.name == name:
            return child
        found = layer_collection(name, child)
        if found:
            return found
    return None


def root_flags():
    return {
        key: lc.exclude
        for key, lc in scenarios._walk(bpy.context.view_layer.layer_collection)
        if key[0] == "Root"
    }


def add_plane(collection, name, location, size=2.0):
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
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
    project.cycles_samples = 16
    project.bake_resolution = '256'
    output = tempfile.mkdtemp(prefix="pmvr_gui_scenarios_")
    project.beauty_output_directory = output + os.sep
    root = bpy.data.collections.new("Root")
    scene.collection.children.link(root)
    collections = {}
    for name in ("Day", "Evening", "Kitchen", "Bedroom"):
        collections[name] = bpy.data.collections.new(name)
        root.children.link(collections[name])
    project.source_root_collection = root
    project.day_lighting_collection = collections["Day"]
    project.evening_lighting_collection = collections["Evening"]
    project.day_world = bpy.data.worlds.new("Day World")
    project.evening_world = bpy.data.worlds.new("Evening World")
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    sun.data.energy = 4.0
    collections["Day"].objects.link(sun)
    counter = add_plane(collections["Kitchen"], "Counter", (0, 0, 0))
    bed = add_plane(collections["Bedroom"], "Bed", (10, 0, 0))
    add_plane(collections["Bedroom"], "Canopy", (0, 0, 2), size=6.0)
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = "LO"
    STATE["units"] = {}
    for name, obj in (("K", counter), ("B", bed)):
        unit = project.bake_units.add()
        unit.unit_id = new_id()
        unit.artifact_key = unit.unit_id
        unit.display_name = name
        unit.render_layer_id = layer.layer_id
        unit.resolution = '512'
        metadata = obj.pm_vr_pipeline
        metadata.source_id = new_id()
        metadata.is_registered_source = True
        metadata.render_layer_id = layer.layer_id
        metadata.processing_role = 'BAKE'
        metadata.bake_unit_id = unit.unit_id
        STATE["units"][name] = unit.unit_id
    project.bake_day = True
    # Both states in the full run: afterwards the scene is back in Day and the
    # baked results must show Day, not the last baked Evening.
    project.bake_evening = MODE == "finish"
    bpy.ops.pmvr.set_lighting_state(state='DAY')

    layer_collection("Bedroom").exclude = True
    bpy.ops.pmvr.add_bake_scenario()
    project.bake_scenarios[-1].display_name = "Kitchen"
    layer_collection("Bedroom").exclude = False
    layer_collection("Kitchen").exclude = True
    bpy.ops.pmvr.add_bake_scenario()
    project.bake_scenarios[-1].display_name = "Bedroom"
    layer_collection("Kitchen").exclude = False
    layer.bake_scenario = project.bake_scenarios[0].scenario_id
    STATE["output"] = output
    STATE["original"] = root_flags()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "scenarios.blend"))


def unit(name):
    return next(u for u in bpy.context.scene.pm_vr_project.bake_units if u.unit_id == STATE["units"][name])


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def start_queue():
    project = bpy.context.scene.pm_vr_project
    project.bake_queue.clear()
    for name in ("K", "B"):
        project.bake_queue.add().unit_id = unit(name).unit_id
    with bpy.context.temp_override(**view3d_override()):
        return bpy.ops.pmvr.bake_queue('INVOKE_DEFAULT')


def mean_brightness(path):
    import numpy as np

    image = bpy.data.images.load(path, check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    pixels = pixels.reshape(-1, 4)
    return float(pixels[pixels[:, 3] > 0.5][:, :3].mean())


def verify():
    project = bpy.context.scene.pm_vr_project
    problems = []
    seen = STATE["seen"]
    # (Kitchen excluded, Bedroom excluded) while the unit's Cycles job ran.
    if seen["K"] != {(False, True)}:
        problems.append(f"K baked with collections {seen['K']}, expected Bedroom off only")
    if MODE == "finish" and seen["B"] != {(True, False)}:
        problems.append(f"B baked with collections {seen['B']}, expected Kitchen off only")
    if MODE == "esc" and not seen["B"]:
        problems.append("B's Cycles job was never observed")
    if root_flags() != STATE["original"]:
        problems.append(f"outliner not restored: {STATE['original']} -> {root_flags()}")
    if scenarios.RESTORE_COLLECTIONS in bpy.context.scene:
        problems.append("restore marker left in the scene")
    if unit("K").day_status != "Ready":
        problems.append(f"K not ready: {unit('K').day_status}")
    expected_b = "Ready" if MODE == "finish" else ""
    if unit("B").day_status != expected_b:
        problems.append(f"B status {unit('B').day_status!r}, expected {expected_b!r}")
    if MODE == "esc" and "cancelled" not in project.last_operation_summary:
        problems.append(f"summary does not report cancellation: {project.last_operation_summary}")
    brightness = mean_brightness(os.path.join(STATE["output"], "LO_K_Beauty.png"))
    print(f"[PM VR GUI] counter brightness {brightness:.3f}")
    if brightness < 0.3:
        problems.append(f"counter is shadowed ({brightness:.3f}): Bedroom was on during its bake")
    if project.operation_running:
        problems.append("operation_running stayed on")
    shown = sorted({
        (obj.get("pmvr_state"), obj.material_slots[0].material.get("pmvr_state"))
        for obj in bpy.data.objects if obj.get("pmvr_generated")
    })
    if project.active_lighting_state != 'DAY' or shown != [('DAY', 'DAY')]:
        problems.append(f"after the queue: lighting {project.active_lighting_state}, baked results show {shown}")
    return problems


def finish(problems):
    for problem in problems:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_BAKE_SCENARIOS_FAILED" if problems else "PM_VR_GUI_BAKE_SCENARIOS_OK")
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE["phase"] == "setup":
            setup()
            # B inherits "Kitchen", which switches off B's own Bed. An operator
            # that reports an error raises in Python.
            try:
                result = start_queue()
            except RuntimeError as exc:
                result = {'CANCELLED'} if "Bake not started" in str(exc) else {str(exc)}
            project = bpy.context.scene.pm_vr_project
            if result != {'CANCELLED'} or not project.last_operation_summary.startswith("Bake not started"):
                finish([f"queue started despite a hidden unit: {result} {project.last_operation_summary}"])
                return None
            if root_flags() != STATE["original"] or project.operation_running:
                finish(["refused queue changed the outliner or stayed running"])
                return None
            print("[PM VR GUI] refused start:", project.last_operation_summary)
            unit("B").bake_scenario = project.bake_scenarios[1].scenario_id
            STATE["t0"] = time.monotonic()
            if start_queue() != {'RUNNING_MODAL'}:
                finish([f"queue did not start: {project.last_operation_summary}"])
                return None
            STATE["phase"] = "running"
            return 0.1
        project = bpy.context.scene.pm_vr_project
        if time.monotonic() - STATE["t0"] > 600:
            finish(["timeout"])
            return None
        if bpy.app.is_job_running('OBJECT_BAKE'):
            # Jobs run Day K, Day B, then Evening K, Evening B.
            if unit("K").day_status != "Ready":
                name = "K"
            elif unit("B").day_status != "Ready":
                name = "B"
            else:
                name = "K" if unit("K").evening_status != "Ready" else "B"
            STATE["seen"][name].add((layer_collection("Kitchen").exclude, layer_collection("Bedroom").exclude))
            if MODE == "esc" and name == "B" and not STATE["sent"]:
                window = bpy.context.window_manager.windows[0]
                window.event_simulate(type='ESC', value='PRESS')
                window.event_simulate(type='ESC', value='RELEASE')
                STATE["sent"] = True
        elif not project.operation_running:
            finish(verify())
            return None
        return 0.1
    except Exception:
        traceback.print_exc()
        finish(["exception"])
        return None


bpy.app.timers.register(tick, first_interval=2.0)
