"""Fill Empty UV Space: the atlas outside the islands takes the islands' colours.

Run with Blender --background --factory-startup --python this_file.py.

1. The rasterizer matches a pixel-by-pixel test on random triangles (tiny,
   large, partly outside the image). Pull-push keeps weighted pixels and
   fills odd sizes.
2. A real bake of a shared unit, a red and a blue emissive plane in one
   atlas: with the fill no background is left, island pixels equal the
   unfilled bake, a pixel beside the red island is red and one beside the
   blue island blue; with the setting off the background stays black.
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


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)
        print(f"[PM VR] FAIL: {message}")


def brute_force(triangles, width, height):
    mask = numpy.zeros((height, width), dtype=bool)
    py, px = numpy.mgrid[0:height, 0:width] + 0.5
    for triangle in triangles:
        mask |= uv_fill._inside(triangle[None], px[None], py[None])[0]
    return mask


def unit_checks():
    rng = numpy.random.default_rng(7)
    width, height = 300, 260
    triangles = []
    for scale in (1.5, 6, 20, 70, 180, 400):
        centres = rng.uniform(-40, 340, size=(12, 1, 2))
        triangles.append(centres + rng.uniform(-scale, scale, size=(12, 3, 2)))
    triangles = numpy.concatenate(triangles)
    fast = uv_fill.rasterize(triangles, width, height)
    slow = brute_force(triangles, width, height)
    check((fast == slow).all(), f"rasterizer differs from brute force in {(fast != slow).sum()} pixel(s)")
    check(fast.any() and not fast.all(), "rasterizer test covers nothing or everything")

    for size in ((192, 192), (97, 61)):
        colour = rng.uniform(0, 1, size=(*size, 3)).astype(numpy.float32)
        weight = rng.uniform(size=size) < 0.1
        filled = uv_fill.pull_push(colour, weight)
        check(filled.shape == colour.shape, f"pull-push shape {filled.shape}")
        check(numpy.allclose(filled[weight], colour[weight]), f"pull-push changed kept pixels at {size}")
        low, high = colour[weight].min(axis=0), colour[weight].max(axis=0)
        check(((filled >= low - 1e-5) & (filled <= high + 1e-5)).all(), f"pull-push left the kept colour range at {size}")


def emitter(collection, name, colour, x, uv_box):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0, 0))
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
    principled = material.node_tree.nodes["Principled BSDF"]
    principled.inputs["Base Color"].default_value = (0, 0, 0, 1)
    principled.inputs["Emission Color"].default_value = (*colour, 1)
    principled.inputs["Emission Strength"].default_value = 1.0
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
    project.cycles_samples = 4
    project.bake_resolution = '256'
    output = tempfile.mkdtemp(prefix="pmvr_uv_fill_")
    project.beauty_output_directory = output + os.sep
    root, day, evening = (bpy.data.collections.new(name) for name in ("FillRoot", "FillDay", "FillEvening"))
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("FillDayWorld")
    project.evening_world = bpy.data.worlds.new("FillEveningWorld")
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), "LO_Fill", 'UNLIT'
    unit = project.bake_units.add()
    unit.unit_id = unit.artifact_key = new_id()
    unit.display_name, unit.render_layer_id, unit.resolution = "Pair", layer.layer_id, '256'
    red = emitter(root, "Red", (0.8, 0.05, 0.05), 0, ((0.05, 0.1), (0.35, 0.9)))
    blue = emitter(root, "Blue", (0.05, 0.05, 0.8), 3, ((0.65, 0.1), (0.95, 0.9)))
    for obj in (red, blue):
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'BAKE'
        meta.bake_unit_id = unit.unit_id
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "fill.blend"))
    return project, unit, output


def bake_codes(project, unit, fill):
    project.fill_empty_uv = fill
    activate_state(bpy.context, 'DAY')
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    runtime.prepare()
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    margin = runtime.margin
    runtime.finish()
    path = next(
        os.path.join(project.beauty_output_directory, name)
        for name in os.listdir(project.beauty_output_directory) if name.endswith("_Beauty.png")
    )
    return uv_fill._load_codes(bpy.path.abspath(path)), margin


def main():
    PM_VR.register()
    unit_checks()
    project, unit, _output = build()
    black, margin = bake_codes(project, unit, fill=False)
    filled, _margin = bake_codes(project, unit, fill=True)
    size = black.shape[0]
    meshes = [obj.data for obj in bpy.data.objects if obj.name in ("Red", "Blue")]
    for mesh in meshes:
        mesh.uv_layers.active = mesh.uv_layers["SimpleBake"]
    islands = uv_fill.rasterize(uv_fill.island_triangles(meshes, size, size), size, size)

    background = black.max(axis=-1) <= uv_fill.BACKGROUND_CODE
    check(background.mean() > 0.3, f"unfilled bake has little background ({background.mean():.0%})")
    check(not (filled.max(axis=-1) <= uv_fill.BACKGROUND_CODE).any(), "filled atlas still has background pixels")
    difference = numpy.abs(filled[islands].astype(int) - black[islands].astype(int)).max()
    check(difference <= 1, f"island pixels differ by {difference} code(s) between the bakes")
    row = size // 2
    left = filled[row, int(0.02 * size)]
    right = filled[row, int(0.98 * size)]
    middle = filled[row, size // 2]
    # AgX pales the emissive colours; compare with the islands themselves.
    red_island = filled[islands & (numpy.arange(size)[None, :] < size // 2)].mean(axis=0)
    blue_island = filled[islands & (numpy.arange(size)[None, :] > size // 2)].mean(axis=0)
    near = lambda pixel, colour: numpy.abs(pixel - colour).max()
    check(near(left, red_island) < 6, f"beside the red island {left}, red island {red_island.round()}")
    check(near(right, blue_island) < 6, f"beside the blue island {right}, blue island {blue_island.round()}")
    check(
        min(red_island[2], blue_island[2]) < middle[2] < max(red_island[2], blue_island[2])
        and min(red_island[0], blue_island[0]) < middle[0] < max(red_island[0], blue_island[0]),
        f"between the islands {middle}, expected a mix of {red_island.round()} and {blue_island.round()}",
    )
    text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check("filled" in text and "of the atlas outside the UV islands" in text, "fill not logged")

    # The staged PNG held open by another reader for a moment (the texture
    # cache, an antivirus scan): the fill waits and still replaces it.
    import threading
    import time as _time
    held = {}
    original_write = uv_fill.write_encoded

    def write_while_held(path, encoded):
        original_write(path, encoded)
        target = held["path"]
        handle = open(target, "rb")
        threading.Timer(1.0, handle.close).start()
    uv_fill.write_encoded = write_while_held
    original_fill = uv_fill.fill_png

    def fill_remembering(path, meshes, margin):
        held["path"] = path
        return original_fill(path, meshes, margin)
    uv_fill.fill_png = fill_remembering
    try:
        started = _time.monotonic()
        locked, _margin = bake_codes(project, unit, fill=True)
        check(not (locked.max(axis=-1) <= uv_fill.BACKGROUND_CODE).any(), "fill lost while the file was held open")
        check(_time.monotonic() - started < 60, "held file made the bake hang")
    finally:
        uv_fill.write_encoded = original_write
        uv_fill.fill_png = original_fill

    # A fill that fails keeps the black background; the unit is not lost.
    uv_fill.fill_png = lambda *args: (_ for _ in ()).throw(RuntimeError("test fill failure"))
    try:
        kept, _margin = bake_codes(project, unit, fill=True)
    finally:
        uv_fill.fill_png = original_fill
    check((kept.max(axis=-1) <= uv_fill.BACKGROUND_CODE).any(), "failed fill did not keep the black background")
    text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check("empty UV space not filled, black kept: test fill failure" in text, "failed fill not logged")
    check(unit.day_status == "Ready", f"a failed fill failed the unit: {unit.day_status}")

    print("PM_VR_UV_FILL_SMOKE_FAILED" if PROBLEMS else "PM_VR_UV_FILL_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
