"""Interactive regression: after a bake the Image Editor still shows the atlas.

    blender --factory-startup --python tests/blender_gui_image_editor.py

A bake switches Image Editors to the image it bakes. The Guided denoise
bakes temporary guide images and removes them, which left an editor that
showed the atlas empty. Baked with every denoise mode, an editor showing
the bake image must show it (or its committed file) afterwards. Prints
PM_VR_GUI_IMAGE_EDITOR_OK or PM_VR_GUI_IMAGE_EDITOR_FAILED and quits.
"""

import os
import sys
import traceback

import bpy

ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
sys.argv = [sys.argv[0]]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_denoise_smoke.py"), encoding="utf8").read().split("def main():")[0])
from PM_VR.modules.pipeline import bake as B  # noqa: E402


def run():
    problems = []
    try:
        PM_VR.register()
        project, unit = build()
        window = bpy.context.window_manager.windows[0]
        area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
        area.type = 'IMAGE_EDITOR'
        space = area.spaces.active
        for mode in ('GUIDED', 'IMAGE', 'OFF'):
            project.beauty_denoise = mode
            activate_state(bpy.context, 'DAY')
            runtime = B.BeautyBakeRuntime(bpy.context, unit)
            runtime.prepare()
            space.image = runtime.image
            for index in range(len(runtime.receivers)):
                runtime.select_receiver(index)
                bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs())
            target = runtime.image
            runtime.finish()
            if space.image is None or space.image != target:
                problems.append(f"{mode}: editor shows {space.image.name if space.image else None}, expected {target.name}")
    except Exception:
        traceback.print_exc()
        problems.append("exception")
    for problem in problems:
        print(f"[PM VR GUI] FAIL: {problem}")
    print("PM_VR_GUI_IMAGE_EDITOR_FAILED" if problems else "PM_VR_GUI_IMAGE_EDITOR_OK")
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1.0)
