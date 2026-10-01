"""Bake signatures are stable under Bevel and still catch real changes.

Run with Blender --background --factory-startup --python this_file.py.

Bevel (multi-threaded) returns evaluated UVs a float step apart on every
evaluation. Signatures hashed those UVs, so Day and Evening of one object
disagreed ("Structurally incompatible", export refused) and variants were
refused as "changed since the bake". Signatures now use the authored
SimpleBake UVs: repeated preparations agree, Day + Evening + a variant are
Ready. A signature from before (no version prefix) counts as compatible and
clears an old false alarm; a real topology change is still caught.
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
from PM_VR.modules.pipeline import bake, export, variants  # noqa: E402

PROBLEMS = []
INCOMPATIBLE = "Structurally incompatible — rebake required"


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def bevelled(root):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.5)
    obj = bpy.context.object
    obj.name = "Counter"
    obj.location.z = 1.0
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    root.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    bevel = obj.modifiers.new("Bevel", 'BEVEL')
    bevel.width = 0.01
    bevel.segments = 3
    bevel.limit_method = 'NONE'
    material = bpy.data.materials.new("Stone")
    material.use_nodes = True
    obj.data.materials.append(material)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def evaluated_uvs(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        layer = mesh.uv_layers["SimpleBake"]
        coords = np.empty(len(layer.data) * 2, dtype=np.float32)
        layer.data.foreach_get("uv", coords)
        return coords
    finally:
        bpy.data.meshes.remove(mesh)


def prepared_signature(unit):
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    try:
        runtime.prepare()
        return runtime.signature
    finally:
        runtime.cleanup(keep_image=False)


def bake_unit(unit, variant_id=""):
    runtime = bake.BeautyBakeRuntime(bpy.context, unit, variant_id=variant_id)
    assert runtime.prepare() == "READY"
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    runtime.finish()


def main():
    base.PM_VR.register()
    project, root, output = base.build()
    project.bake_resolution = '256'
    project.cycles_samples = 1
    evening = bpy.data.collections["ResEvening"]
    evening.objects.link(bpy.data.objects.new("EveningLamp", bpy.data.lights.new("EveningLamp", 'POINT')))
    obj = bevelled(root)
    project.active_render_layer_index = [layer.display_name for layer in project.render_layers].index("Unlit")
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    unit = project.bake_units[-1]
    unit.resolution = '256'
    base.activate_state(bpy.context, 'DAY')

    runs = [evaluated_uvs(obj) for _ in range(6)]
    noisy = any(not np.array_equal(runs[0], run) for run in runs[1:])
    print(f"signature: evaluated Bevel UVs differ between evaluations: {noisy} "
          f"(max {max(float(np.abs(run - runs[0]).max()) for run in runs):.2g})")
    signatures = {prepared_signature(unit) for _ in range(4)}
    check(len(signatures) == 1, f"repeated preparations disagree: {signatures}")
    check(next(iter(signatures)).startswith("2:"), "signature lacks its version")

    # Day, Evening and a variant: all Ready, export accepts both states.
    bpy.ops.pmvr.add_bake_variant()
    unit.variants[0].title = "Dark"
    unit.variants[0].material = bpy.data.materials.new("StoneDark")
    for state in ('DAY', 'EVENING'):
        base.activate_state(bpy.context, state)
        bake_unit(unit)
        bake_unit(unit, unit.variants[0].variant_id)
    check((unit.day_status, unit.evening_status) == ("Ready", "Ready"), f"statuses {unit.day_status!r}, {unit.evening_status!r}")
    for state in ('DAY', 'EVENING'):
        check(export._unit_ready(unit, state)[0], f"export refuses {state}: {export._unit_ready(unit, state)[1]}")
        check(variants.variant_status(unit, unit.variants[0], state) == "Ready", f"variant {state} not Ready")

    # A file from before: an old signature and a false alarm on Evening.
    unit.evening_signature = "a" * 64
    unit.evening_status = INCOMPATIBLE
    base.activate_state(bpy.context, 'DAY')
    bake_unit(unit)
    check(unit.evening_status == "Ready", f"old false alarm not cleared: {unit.evening_status!r}")
    check(export._unit_ready(unit, 'EVENING')[0], "export refuses an older Evening result")

    # A real change is still caught: more geometry after both bakes.
    base.activate_state(bpy.context, 'EVENING')
    bake_unit(unit)
    obj.modifiers["Bevel"].segments = 5
    base.activate_state(bpy.context, 'DAY')
    bake_unit(unit)
    check(unit.evening_status == INCOMPATIBLE, f"topology change not caught: {unit.evening_status!r}")
    check(not export._unit_ready(unit, 'DAY')[0], "export accepts Day and Evening of different structure")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_SIGNATURE_SMOKE_FAILED" if PROBLEMS else "PM_VR_SIGNATURE_SMOKE_OK")
    base.PM_VR.unregister()


if __name__ == "__main__":
    main()
