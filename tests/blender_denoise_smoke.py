"""Denoise modes of the Beauty bake: Guided keeps texture detail.

Run with Blender --background --factory-startup --python this_file.py.

A shared unit of two planes with a fine checker (4 px at the bake size) lit
by the sky only, 2 samples: the raw bake is noisy. Off keeps the noise, Image
Only (SimpleBake's way) and Guided (albedo and normal guides) are compared
with the same bake at 256 samples: Guided must be clearly closer. Also: the mean colour does not move, the guide images
are removed, the samples are restored, a failing guided denoise falls back
to Image Only with a warning, and every mode is logged.
"""

import os
import sys
import tempfile

import bpy
import numpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, uv_fill  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


PROBLEMS = []
SIZE = 256


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)
        print(f"[PM VR] FAIL: {message}")


def plane(collection, name, x, uv_box):
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    (u0, v0), (u1, v1) = uv_box
    for loop in bake_uv.data:
        loop.uv = (u0 + loop.uv[0] * (u1 - u0), v0 + loop.uv[1] * (v1 - v0))
    material = bpy.data.materials.new(f"{name}Mat")
    material.use_nodes = True
    tree = material.node_tree
    checker = tree.nodes.new("ShaderNodeTexChecker")
    checker.inputs["Scale"].default_value = 0.45 * SIZE / 4
    checker.inputs["Color1"].default_value = (0.8, 0.7, 0.6, 1)
    checker.inputs["Color2"].default_value = (0.2, 0.2, 0.25, 1)
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    tree.links.new(uv.outputs["UV"], checker.inputs["Vector"])
    tree.links.new(checker.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)
    return obj


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.cycles.device = 'CPU'
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 2
    project.bake_resolution = str(SIZE)
    project.fill_empty_uv = False
    output = tempfile.mkdtemp(prefix="pmvr_denoise_")
    project.beauty_output_directory = output + os.sep
    root, day, evening = (bpy.data.collections.new(n) for n in ("DnRoot", "DnDay", "DnEvening"))
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("DnDayWorld")
    project.evening_world = bpy.data.worlds.new("DnEveningWorld")
    # Sky light only at 2 samples: a noisy bake, as in shadowed fabric.
    world = project.day_world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.6, 0.6, 0.6, 1)
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), "LO_Dn", 'UNLIT'
    unit = project.bake_units.add()
    unit.unit_id = unit.artifact_key = new_id()
    unit.display_name, unit.render_layer_id, unit.resolution = "Pair", layer.layer_id, str(SIZE)
    for obj in (
        plane(root, "Left", 0, ((0.02, 0.02), (0.48, 0.98))),
        plane(root, "Right", 3, ((0.52, 0.02), (0.98, 0.98))),
    ):
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'BAKE'
        meta.bake_unit_id = unit.unit_id
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "denoise.blend"))
    return project, unit


def bake_codes(project, unit, mode):
    project.beauty_denoise = mode
    activate_state(bpy.context, 'DAY')
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    runtime.prepare()
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    runtime.finish()
    path = next(
        os.path.join(project.beauty_output_directory, n)
        for n in os.listdir(project.beauty_output_directory) if n.endswith("_Beauty.png")
    )
    codes = uv_fill._load_codes(bpy.path.abspath(path)).astype(numpy.float32) / 255.0
    return codes[8:SIZE - 8, 8:SIZE - 8]


def detail(codes):
    """Mean |pixel - 3x3 box| of the luma: texture plus noise."""
    luma = codes @ numpy.array([0.2126, 0.7152, 0.0722], numpy.float32)
    padded = numpy.pad(luma, 1, mode='edge')
    box = sum(padded[dy:dy + luma.shape[0], dx:dx + luma.shape[1]] for dy in range(3) for dx in range(3)) / 9
    return float(numpy.abs(luma - box).mean())


def main():
    PM_VR.register()
    project, unit = build()
    raw = bake_codes(project, unit, 'OFF')
    image_only = bake_codes(project, unit, 'IMAGE')
    guided = bake_codes(project, unit, 'GUIDED')
    # The noise-free checker at 256 samples, as the reference detail.
    project.cycles_samples = 256
    reference = bake_codes(project, unit, 'OFF')
    project.cycles_samples = 2
    d = {name: detail(v) for name, v in (("raw", raw), ("image", image_only), ("guided", guided), ("reference", reference))}
    print("[PM VR] detail " + ", ".join(f"{k} {v:.4f}" for k, v in d.items()))
    check(abs(d["guided"] - d["reference"]) < 0.2 * d["reference"], f"guided detail far from the reference: {d}")
    error = {name: float(numpy.abs(value - reference).mean()) for name, value in (("image", image_only), ("guided", guided))}
    print(f"[PM VR] mean abs error vs reference {error}")
    check(error["guided"] < 0.75 * error["image"], f"guided not clearly closer to the 256-sample bake than image-only: {error}")
    for name, value in (("image", image_only), ("guided", guided)):
        shift = numpy.abs(value.mean(axis=(0, 1)) - raw.mean(axis=(0, 1))).max()
        check(shift < 0.02, f"{name} moved the mean colour by {shift:.3f}")
    check(not [i.name for i in bpy.data.images if i.name.startswith(("__PMVR_ALBEDO", "__PMVR_NORMAL"))], "guide images left")
    check(bpy.context.scene.cycles.samples != bake.GUIDE_SAMPLES, "guide samples left on the scene")

    original = bake.denoise_image
    bake.denoise_image = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("test failure"))
    try:
        fallback = bake_codes(project, unit, 'GUIDED')
    finally:
        bake.denoise_image = original
    check(numpy.abs(fallback - image_only).mean() < 0.01, "a failed guided denoise did not fall back to image-only")
    text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check("guided denoise (guides" in text, "guided denoise not logged")
    check("guided denoise failed, image-only denoise used: test failure" in text, "fallback not logged")

    print("PM_VR_DENOISE_SMOKE_FAILED" if PROBLEMS else "PM_VR_DENOISE_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
