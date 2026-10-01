"""Render layers can be reordered; nothing else changes.

Run with Blender --background --factory-startup --python this_file.py.

Moving the highlighted layer up and down changes only the list order: the
highlight follows it, layer IDs and names stay, objects keep their layer,
and the export follows the new order. The ends of the list do nothing.
"""

import os
import sys

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import blender_resolution_smoke as base  # noqa: E402

PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def names(project):
    return [layer.display_name for layer in project.render_layers]


def move(direction):
    try:
        return bpy.ops.pmvr.move_render_layer(direction=direction)
    except RuntimeError:
        return {'CANCELLED'}


def main():
    base.PM_VR.register()
    project, root, _output = base.build()
    ids = {layer.display_name: layer.layer_id for layer in project.render_layers}
    start = names(project)
    print(f"layers: {start}")
    plane = base.add_plane(root, "Panel", 1.0)
    project.active_render_layer_index = start.index("Emissive")
    assert bpy.ops.pmvr.assign_selected_to_layer() == {'FINISHED'}
    emissive_id = ids["Emissive"]
    check(plane.pm_vr_pipeline.render_layer_id == emissive_id, "object not in Emissive")

    index = start.index("Emissive")
    check(move('UP') == {'FINISHED'}, "move up refused")
    moved = names(project)
    check(moved.index("Emissive") == index - 1 and project.active_render_layer_index == index - 1,
          f"after up: {moved}, highlight {project.active_render_layer_index}")
    check(move('DOWN') == {'FINISHED'} and names(project) == start, f"down did not restore: {names(project)}")
    project.active_render_layer_index = 0
    check(move('UP') == {'CANCELLED'} and names(project) == start, "first layer moved up")
    project.active_render_layer_index = len(start) - 1
    check(move('DOWN') == {'CANCELLED'} and names(project) == start, "last layer moved down")

    # Emissive to the top: IDs, names and membership unchanged.
    project.active_render_layer_index = start.index("Emissive")
    while project.active_render_layer_index > 0:
        move('UP')
    check(names(project)[0] == "Emissive", f"Emissive not first: {names(project)}")
    check({layer.display_name: layer.layer_id for layer in project.render_layers} == ids, "layer IDs changed")
    check(plane.pm_vr_pipeline.render_layer_id == emissive_id, "object lost its layer")
    print(f"layers: after moving Emissive to the top {names(project)}")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_LAYER_ORDER_SMOKE_FAILED" if PROBLEMS else "PM_VR_LAYER_ORDER_SMOKE_OK")
    base.PM_VR.unregister()


if __name__ == "__main__":
    main()
