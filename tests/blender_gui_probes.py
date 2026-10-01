"""Interactive regression: Render Probes as a modal render job.

Needs a real window and simulated input; it cannot run with --background:

    blender --factory-startup --enable-event-simulate --python tests/blender_gui_probes.py -- finish
    blender --factory-startup --python tests/blender_gui_probes.py -- esc
    blender --factory-startup --enable-event-simulate --python tests/blender_gui_probes.py -- button

A simulated Esc never reaches a render job (Blender stops renders on the
real key), so esc presses the real key, and only while the test's own
window is in front; otherwise it fails instead of typing into another window.

Three probe cameras, every look. finish: six EXRs. esc: Esc while the
first probe renders; nothing is written and the run stops. button: Cancel
while the second renders; it finishes and is kept, the third is not
rendered. Always: no render window opens, the Render display preference,
camera and render settings come back, the Bake button is free again.
Prints PM_VR_GUI_PROBES_OK or PM_VR_GUI_PROBES_FAILED and quits.
"""

import ctypes
import math
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
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402

MODE = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "finish"
STATE = {"phase": "setup", "t0": 0.0, "sent": False}


def setup():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = False
    # Long enough renders to cancel one in the middle.
    scene.cycles.use_adaptive_sampling = False
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 8 if MODE == "finish" else 256
    project.probe_width = '512'
    output = tempfile.mkdtemp(prefix="pmvr_gui_probes_")
    project.usdz_output_directory = os.path.join(output, "USDZ") + os.sep
    root = bpy.data.collections.new("Root")
    day, evening, runtime = (bpy.data.collections.new(name) for name in ("Day", "Evening", "RuntimeCol"))
    scene.collection.children.link(root)
    for collection in (day, evening, runtime):
        root.children.link(collection)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Day World")
    project.evening_world = bpy.data.worlds.new("Evening World")
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0, location=(0, 3, 0))
    sphere = bpy.context.object
    for owner in list(sphere.users_collection):
        owner.objects.unlink(sphere)
    root.objects.link(sphere)
    day.objects.link(bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN')))
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), "Runtime", 'RUNTIME'
    for index in range(3):
        data = bpy.data.cameras.new(f"Probe_{index}")
        data.type = 'PANO'
        data.panorama_type = 'EQUIRECTANGULAR'
        obj = bpy.data.objects.new(f"Probe_{index}", data)
        obj.location = (index, 0, 1)
        obj.rotation_euler = (math.radians(90), 0, 0)
        runtime.objects.link(obj)
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'EXPORT_ORIGINAL'
    main = bpy.data.objects.new("MainCam", bpy.data.cameras.new("MainCam"))
    scene.collection.objects.link(main)
    scene.camera = main
    scene.render.resolution_x, scene.render.resolution_y = 640, 480
    project.bake_day, project.bake_evening = True, False
    activate_state(bpy.context, 'DAY')
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "probes.blend"))
    STATE["folder"] = os.path.join(output, "probes")
    STATE["display"] = bpy.context.preferences.view.render_display_type
    STATE["windows"] = len(bpy.context.window_manager.windows)


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return {"window": window, "area": area, "region": region}


def press_real_esc():
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    if pid.value != os.getpid():
        # An unattended run may initially hide its owned window. The real
        # keyboard check is restricted to that process and its GHOST window.
        handles = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @callback_type
        def visit(handle, _param):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == os.getpid():
                name = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(handle, name, len(name))
                if name.value.startswith('GHOST_'):
                    handles.append(handle)
            return True
        user32.EnumWindows(visit, 0)
        print('PROBE_ESC_OWN_WINDOWS', handles, 'foreground PID', pid.value, flush=True)
        if not handles:
            return False
        # Render cancellation polls actual keyboard state, so posted messages
        # cannot exercise it. A native-key test needs its own interactive
        # window. Show only the HWND whose PID was checked above.
        handle = handles[0]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow(handle, 9)
        user32.SetForegroundWindow(handle)
        # Windows may deny a timer callback foreground activation. Attach
        # this test thread briefly to the foreground input queue, activate
        # ONLY the verified owned window, then detach before sending keys.
        foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        if foreground_thread != current_thread:
            attached = user32.AttachThreadInput(current_thread, foreground_thread, True)
            try:
                if attached:
                    user32.SetForegroundWindow(handle)
            finally:
                if attached:
                    user32.AttachThreadInput(current_thread, foreground_thread, False)
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value != os.getpid():
            return False
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 2, 0)
    return True


def files():
    folder = STATE["folder"]
    return sorted(f for f in os.listdir(folder)) if os.path.isdir(folder) else []


def verify():
    scene = bpy.context.scene
    project = scene.pm_vr_project
    problems = []
    expected = {
        "finish": ["Probe_0.exr", "Probe_0_Evening.exr", "Probe_1.exr", "Probe_1_Evening.exr", "Probe_2.exr", "Probe_2_Evening.exr"],
        "esc": [],
        "button": ["Probe_0.exr", "Probe_1.exr"],
    }[MODE]
    if files() != expected:
        problems.append(f"files {files()}, expected {expected}")
    cancelled = "cancelled" in project.last_operation_summary
    if cancelled != (MODE != "finish"):
        problems.append(f"summary: {project.last_operation_summary}")
    if bpy.context.preferences.view.render_display_type != STATE["display"]:
        problems.append(f"render display {bpy.context.preferences.view.render_display_type}, was {STATE['display']}")
    if STATE["max_windows"] != STATE["windows"]:
        problems.append("a render window opened")
    if scene.camera.name != "MainCam" or (scene.render.resolution_x, scene.render.resolution_y) != (640, 480):
        problems.append(f"scene not restored: {scene.camera.name} {scene.render.resolution_x}x{scene.render.resolution_y}")
    if scene.render.use_persistent_data:
        problems.append("persistent data left on")
    if [o.name for o in bpy.data.objects if o.name.startswith("__PMVR")]:
        problems.append("temporary camera left")
    if project.operation_running:
        problems.append("operation_running stayed on")
    return problems


def finish(problems):
    for problem in problems:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_PROBES_FAILED" if problems else "PM_VR_GUI_PROBES_OK")
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE["phase"] == "setup":
            setup()
            STATE["t0"] = time.monotonic()
            STATE["max_windows"] = STATE["windows"]
            with bpy.context.temp_override(**view3d_override()):
                bpy.ops.pmvr.render_probes('INVOKE_DEFAULT')
            STATE["phase"] = "running"
            return 0.2
        project = bpy.context.scene.pm_vr_project
        STATE["max_windows"] = max(STATE["max_windows"], len(bpy.context.window_manager.windows))
        if time.monotonic() - STATE["t0"] > 600:
            finish(["timeout"])
            return None
        # Esc early in the first render, while the new window is still in
        # front; Cancel during the second.
        ready = files() == ["Probe_0.exr"] if MODE == "button" else time.monotonic() - STATE["t0"] > 2.0
        if MODE != "finish" and not STATE["sent"] and ready and bpy.app.is_job_running('RENDER'):
            if MODE == "esc":
                if not press_real_esc():
                    finish(["the test window is not in front; Esc not sent"])
                    return None
            else:
                with bpy.context.temp_override(**view3d_override()):
                    bpy.ops.pmvr.cancel_bake_queue()
            STATE["sent"] = True
        if not project.operation_running and (MODE == "finish" or STATE["sent"]):
            finish(verify())
            return None
        return 0.2
    except Exception:
        traceback.print_exc()
        finish(["exception"])
        return None


bpy.app.timers.register(tick, first_interval=1.0)
