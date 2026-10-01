"""Render Probes writes world-aligned EXR Half ZIP panoramas per lighting state.

Run with Blender --background --factory-startup --python this_file.py.

Emissive planes mark the directions: red at +Y, blue at -Y, green at +X in
the Day lighting collection only, a yellow generated result at -X (hidden
like in a bake), a magenta plane above outside Source Root (hidden like in
a bake). The world is 0.2 in Day and 0.02 in Evening, the view transform
AgX. A probe camera turned 180 degrees still renders red in the middle.
Checked: files named after the USD prim (Probe.002 -> Probe_002), only
panoramic cameras of Runtime layers, ZIP half RGB at the Probe Size, linear
values, Day/Evening content, and every scene setting put back.
"""

import math
import os
import sys
import tempfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.bake_scene import (  # noqa: E402
    ensure_scene_collection,
    find_pipeline_collection,
    pipeline_collection,
)
from PM_VR.modules.pipeline.constants import GENERATED_COLLECTION, WORK_COLLECTION  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)
        print(f"[PM VR] FAIL: {message}")


def emitter(collection, name, colour, location, rotation):
    bpy.ops.mesh.primitive_plane_add(size=6.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    material = bpy.data.materials.new(f"{name}Mat")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.remove(tree.nodes["Principled BSDF"])
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*colour, 1.0)
    tree.links.new(emission.outputs[0], tree.nodes["Material Output"].inputs["Surface"])
    obj.data.materials.append(material)
    return obj


def world(name, value):
    item = bpy.data.worlds.new(name)
    item.use_nodes = True
    background = item.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (value, value, value, 1.0)
    background.inputs["Strength"].default_value = 1.0
    return item


def camera(collection, name, kind, rotation_z=0.0):
    data = bpy.data.cameras.new(name)
    data.type = kind
    if kind == 'PANO':
        data.panorama_type = 'EQUIRECTANGULAR'
    obj = bpy.data.objects.new(name, data)
    obj.rotation_euler = (math.radians(90.0), 0.0, math.radians(rotation_z))
    collection.objects.link(obj)
    return obj


def register(obj, layer_id):
    meta = obj.pm_vr_pipeline
    meta.source_id = new_id()
    meta.is_registered_source = True
    meta.render_layer_id = layer_id
    meta.processing_role = 'EXPORT_ORIGINAL'


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 4
    project.probe_width = '512'
    output = tempfile.mkdtemp(prefix="pmvr_probes_")
    project.usdz_output_directory = os.path.join(output, "USDZ") + os.sep
    root = bpy.data.collections.new("ProbeRoot")
    scene.collection.children.link(root)
    day, evening, room, runtime = (bpy.data.collections.new(name) for name in ("PrDay", "PrEvening", "PrRoom", "PrRuntime"))
    for collection in (day, evening, room, runtime):
        root.children.link(collection)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = world("PrDayWorld", 0.2)
    project.evening_world = world("PrEveningWorld", 0.02)

    emitter(room, "Red", (1.0, 0.0, 0.0), (0, 5, 0), (math.radians(90), 0, 0))
    emitter(room, "Blue", (0.0, 0.0, 1.0), (0, -5, 0), (math.radians(90), 0, 0))
    emitter(day, "Green", (0.0, 1.0, 0.0), (5, 0, 0), (0, math.radians(90), 0))
    generated = pipeline_collection(GENERATED_COLLECTION)
    ensure_scene_collection(scene, generated)
    emitter(generated, "Yellow", (1.0, 1.0, 0.0), (-5, 0, 0), (0, math.radians(90), 0))["pmvr_generated"] = True
    emitter(scene.collection, "Magenta", (1.0, 0.0, 1.0), (0, 0, 5), (0, 0, 0))

    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), "Runtime", 'RUNTIME'
    for obj in (
        camera(runtime, "Probe_Test", 'PANO', rotation_z=180.0),
        camera(runtime, "Probe.002", 'PANO'),
        camera(runtime, "RuntimePerspective", 'PERSP'),
    ):
        register(obj, layer.layer_id)
    camera(room, "Probe_NotRuntime", 'PANO')
    scene.camera = camera(scene.collection, "MainCam", 'PERSP')

    render = scene.render
    render.engine = 'BLENDER_EEVEE' if 'BLENDER_EEVEE' in {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else render.engine
    render.resolution_x, render.resolution_y, render.resolution_percentage = 1920, 1080, 50
    render.use_compositing = True
    render.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'AgX'
    scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = False
    project.bake_day = project.bake_evening = True
    activate_state(bpy.context, 'DAY')
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "probes.blend"))
    return project, output


def settings(scene):
    render = scene.render
    names = (
        "engine", "resolution_x", "resolution_y", "resolution_percentage", "use_compositing",
        "use_sequencer", "film_transparent", "use_persistent_data", "use_border",
    )
    image = render.image_settings
    return (
        scene.camera.name if scene.camera else None,
        {name: getattr(render, name) for name in names},
        (image.file_format, image.color_mode, image.color_depth),
        scene.cycles.samples,
        scene.world.name if scene.world else None,
    )


def read(path):
    import OpenImageIO as oiio

    buf = oiio.ImageBuf(path)
    spec = buf.spec()
    pixels = buf.get_pixels(oiio.FLOAT)
    return spec, pixels


def mean(pixels, u, v):
    height, width = pixels.shape[:2]
    x, y = min(width - 3, max(2, int(u * width))), min(height - 3, max(2, int(v * height)))
    block = pixels[y - 2:y + 3, x - 2:x + 3, :3]
    return tuple(float(block[..., c].mean()) for c in range(3))


def main():
    PM_VR.register()
    project, output = build()
    scene = bpy.context.scene
    before = settings(scene)
    probe_rotation = tuple(bpy.data.objects["Probe_Test"].rotation_euler)
    generated_excluded = bpy.context.view_layer.layer_collection.children[find_pipeline_collection(GENERATED_COLLECTION).name].exclude

    result = bpy.ops.pmvr.render_probes()
    check(result == {'FINISHED'}, f"render_probes {result}: {project.last_operation_summary}")
    check("4 ready, 0 failed" in project.last_operation_summary, project.last_operation_summary)

    folder = os.path.join(output, "probes")
    files = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    wanted = ["Probe_002.exr", "Probe_002_Evening.exr", "Probe_Test.exr", "Probe_Test_Evening.exr"]
    check(files == wanted, f"probe files {files}")

    images = {}
    for name in files:
        spec, pixels = read(os.path.join(folder, name))
        images[name] = pixels
        check(spec.width == 512 and spec.height == 256, f"{name}: {spec.width}x{spec.height}")
        check(spec.get_string_attribute("compression") == "zip", f"{name}: {spec.get_string_attribute('compression')}")
        check(list(spec.channelnames) == ["R", "G", "B"], f"{name}: channels {list(spec.channelnames)}")
        check(all(str(spec.channelformat(i)) == "half" for i in range(spec.nchannels)), f"{name}: not half")

    for day_name, evening_name in (("Probe_Test.exr", "Probe_Test_Evening.exr"), ("Probe_002.exr", "Probe_002_Evening.exr")):
        day, evening = images.get(day_name), images.get(evening_name)
        if day is None or evening is None:
            continue
        # World-aligned: +Y (red) in the middle, -Y (blue) at the seam.
        centre = mean(day, 0.5, 0.5)
        check(centre[0] > 0.5 and centre[2] < 0.1, f"{day_name}: centre {centre}, expected red")
        seam = mean(day, 0.003, 0.5)
        check(seam[2] > 0.5 and seam[0] < 0.1, f"{day_name}: seam {seam}, expected blue")
        # Linear light: the world straight up, not the view transform, and
        # not the magenta plane outside Source Root.
        check(abs(mean(day, 0.5, 0.002)[1] - 0.2) < 0.01, f"{day_name}: zenith {mean(day, 0.5, 0.002)}")
        check(abs(mean(evening, 0.5, 0.002)[1] - 0.02) < 0.005, f"{evening_name}: zenith {mean(evening, 0.5, 0.002)}")
        sides = [mean(day, u, 0.5) for u in (0.25, 0.75)]
        greens = [side for side in sides if side[1] > 0.5 and side[0] < 0.1]
        check(len(greens) == 1, f"{day_name}: sides {sides}, expected green on one")
        check(not any(side[0] > 0.5 and side[1] > 0.5 for side in sides), f"{day_name}: generated result visible {sides}")
        evening_sides = [mean(evening, u, 0.5) for u in (0.25, 0.75)]
        check(all(side[1] < 0.1 for side in evening_sides), f"{evening_name}: Day light visible {evening_sides}")
    if "Probe_Test.exr" in images and "Probe_002.exr" in images:
        difference = abs(images["Probe_Test.exr"] - images["Probe_002.exr"]).max()
        check(difference < 1e-3, f"a turned probe camera changed the panorama by {difference}")

    check(settings(scene) == before, f"scene settings changed:\n{before}\n{settings(scene)}")
    check(project.active_lighting_state == 'DAY', f"state {project.active_lighting_state}")
    check(tuple(bpy.data.objects["Probe_Test"].rotation_euler) == probe_rotation, "probe camera moved")
    check(not any(obj.name.startswith("__PMVR_PROBE") for obj in bpy.data.objects), "temporary camera left")
    check(find_pipeline_collection(WORK_COLLECTION) is None, "PMVR_WORK left")
    check(not any(k.startswith("pmvr_bake_restore") for obj in bpy.data.objects for k in obj.keys()), "restore markers left")
    check(bpy.data.objects["Magenta"].hide_render is False, "hidden object not restored")
    generated_now = bpy.context.view_layer.layer_collection.children[find_pipeline_collection(GENERATED_COLLECTION).name].exclude
    check(generated_now == generated_excluded, "generated collection exclusion not restored")
    check(not project.operation_running, "operation still running")
    text = bpy.data.texts.get("PMVR Pipeline Log").as_string()
    check('"Probe_Test" is rotated' in text, "rotated probe not logged")
    check("Probes (Day + Evening): 4 ready, 0 failed" in text, "summary not logged")

    # No panoramic camera: refused, nothing changes.
    for name in ("Probe_Test", "Probe.002"):
        bpy.data.objects[name].data.type = 'PERSP'
    before = settings(scene)
    try:
        refused = bpy.ops.pmvr.render_probes() == {'CANCELLED'}
    except RuntimeError as exc:
        refused = "No panoramic" in str(exc)
    check(refused, "ran without probe cameras")
    check(settings(scene) == before, "refusal changed the scene")

    print("PM_VR_PROBES_SMOKE_FAILED" if PROBLEMS else "PM_VR_PROBES_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
