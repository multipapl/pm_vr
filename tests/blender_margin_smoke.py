"""Bake margin follows the island padding and never paints over a neighbour.

Run with Blender --background --factory-startup --python this_file.py.

Two objects share one unit, packed with a 0.002 gap (2 px at 1024): red on
the left, blue on the right. Objects of a unit bake one after another into
one image, and Blender paints each object's margin over every pixel that is
not its own. A margin wider than the gap (what the old scaled margin did)
repaints the edge of the island baked first; the padding-based margin fills
only the gap, half from each side. A single-object unit gets the full gap.
"""

import os
import sys

import bpy
import numpy as np


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import blender_resolution_smoke as base  # noqa: E402
from PM_VR.modules.pipeline import bake, setup_ops  # noqa: E402

PROBLEMS = []
SIZE = 1024


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def emission_material(name, colour):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
    bsdf.inputs["Emission Color"].default_value = colour
    bsdf.inputs["Emission Strength"].default_value = 1.0
    return material


def island_plane(root, name, x, u_range, colour):
    plane = base.add_plane(root, name, 1.0)
    plane.location.x = x
    plane.data.materials.append(emission_material(name, colour))
    uv = plane.data.uv_layers["SimpleBake"]
    for loop in plane.data.loops:
        u, v = plane.data.uv_layers["UVMap"].data[loop.index].uv
        uv.data[loop.index].uv = (u_range[0] + u * (u_range[1] - u_range[0]), 0.05 + v * 0.9)
    return plane


def bake_unit(unit):
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    assert runtime.prepare() == "READY"
    order = [receiver["source"].name for receiver in runtime.receivers]
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    margin = runtime.margin
    runtime.finish()
    return order, margin


def islands(path):
    """Pixels inside the red and the blue island that took the other colour."""
    _size, rgb = base.load_rgb(path)
    rows = slice(int(0.06 * SIZE), int(0.94 * SIZE))
    red = rgb[rows, int(0.05 * SIZE) + 1: 511]
    blue = rgb[rows, 513: int(0.95 * SIZE) - 1]
    return int((red[..., 2] > red[..., 0]).sum()), int((blue[..., 0] > blue[..., 2]).sum())


def main():
    base.PM_VR.register()
    project, root, output = base.build()
    project.bake_resolution = str(SIZE)
    project.cycles_samples = 1
    project.uv_padding = 0.002
    check(abs(project.bl_rna.properties["uv_padding"].default - 0.002) < 1e-9, "padding default is not 0.002")
    check(setup_ops.bake_margin(project, 4096) == 8, f"4096: {setup_ops.bake_margin(project, 4096)}")
    check(setup_ops.bake_margin(project, 4096, 2) == 4, f"4096 shared: {setup_ops.bake_margin(project, 4096, 2)}")
    check(setup_ops.bake_margin(project, 2048, 3) == 2, f"2048 shared: {setup_ops.bake_margin(project, 2048, 3)}")
    check(setup_ops.bake_margin(project, 256, 2) == 1, f"256 shared: {setup_ops.bake_margin(project, 256, 2)}")

    red = island_plane(root, "Red", -0.6, (0.05, 0.499), (1, 0, 0, 1))
    blue = island_plane(root, "Blue", 0.6, (0.501, 0.95), (0, 0, 1, 1))
    bpy.ops.object.select_all(action='DESELECT')
    red.select_set(True)
    blue.select_set(True)
    bpy.context.view_layer.objects.active = red
    project.active_render_layer_index = [layer.display_name for layer in project.render_layers].index("Unlit")
    assert bpy.ops.pmvr.add_bake_unit(merge_selected=True) == {'FINISHED'}
    unit = project.bake_units[-1]
    unit.resolution = str(SIZE)
    base.activate_state(bpy.context, 'DAY')
    png = os.path.join(output, f"Unlit_{unit.display_name}_Beauty.png")

    # The old way: a margin wider than the gap.
    original = setup_ops.bake_margin
    bake.bake_margin = lambda *_args: 8
    try:
        order, margin = bake_unit(unit)
    finally:
        bake.bake_margin = original
    old_red, old_blue = islands(png)
    print(f"margin: {margin}px margin, bake order {order}: repainted pixels red {old_red}, blue {old_blue}")
    check(margin == 8 and (old_red or old_blue),
          "a margin wider than the gap did not repaint the neighbour; the test does not show the problem")

    order, margin = bake_unit(unit)
    new_red, new_blue = islands(png)
    print(f"margin: padding margin {margin}px, bake order {order}: repainted pixels red {new_red}, blue {new_blue}")
    check(margin == 1, f"shared unit margin {margin}, expected 1 (half of the 2 px gap)")
    check(new_red == 0 and new_blue == 0, f"padding margin still repaints: red {new_red}, blue {new_blue}")
    _size, rgb = base.load_rgb(png)
    gap = rgb[int(0.5 * SIZE), 511:513]
    check(gap[0][0] > gap[0][2] and gap[1][2] > gap[1][0], f"gap not filled half/half: {gap.round(2).tolist()}")
    log_text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check(", margin 1px," in log_text, "bake log misses the margin")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_MARGIN_SMOKE_FAILED" if PROBLEMS else "PM_VR_MARGIN_SMOKE_OK")
    base.PM_VR.unregister()


if __name__ == "__main__":
    main()
