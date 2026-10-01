"""Export does not depend on what is visible in the file.

Run with Blender --background --factory-startup --python this_file.py.

Baked units and Export Original objects are placed in a disabled collection,
with their render toggle off, hidden with H, and with generated results hidden
by the Show Generated switch. Every one of them must still be exported, in
USDZ and GLB, and the file's visibility must be unchanged afterwards. Only the
Day/Evening lighting collections decide state-specific content: an object that
lives only inside the Evening collection is exported in Evening, not in Day.
"""

import json
import os
import struct
import sys
import tempfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, export  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


def child(parent, name):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def add_plane(collection, name, x):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    material = bpy.data.materials.new(f"{name}Mat")
    material.use_nodes = True
    obj.data.materials.append(material)
    return obj


def register(obj, layer_id, role, unit_id=""):
    meta = obj.pm_vr_pipeline
    meta.source_id = new_id()
    meta.is_registered_source = True
    meta.render_layer_id = layer_id
    meta.processing_role = role
    meta.bake_unit_id = unit_id


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 1
    project.bake_resolution = '256'
    project.uv_padding = 0.008
    output = tempfile.mkdtemp(prefix="pmvr_export_vis_")
    for attribute in ("beauty_output_directory", "usdz_output_directory", "glb_output_directory"):
        setattr(project, attribute, output + os.sep)
    root = bpy.data.collections.new("VisRoot")
    scene.collection.children.link(root)
    day, evening = child(root, "VisDay"), child(root, "VisEvening")
    room, hidden_room = child(root, "Room"), child(root, "HiddenRoom")
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("VisDayWorld")
    project.evening_world = bpy.data.worlds.new("VisEveningWorld")
    day.objects.link(bpy.data.objects.new("VisSun", bpy.data.lights.new("VisSun", 'SUN')))
    evening.objects.link(bpy.data.objects.new("VisLamp", bpy.data.lights.new("VisLamp", 'POINT')))

    baked = project.render_layers.add()
    baked.layer_id, baked.display_name, baked.layer_type = new_id(), "LO_Vis", 'UNLIT'
    glass = project.render_layers.add()
    glass.layer_id, glass.display_name, glass.layer_type = new_id(), "GL_Vis", 'GLASS'
    for layer in (baked, glass):
        layer.export_usdz = layer.export_glb = True

    units = {}
    for name, collection, x in (
        ("WallA", room, 0), ("WallB", hidden_room, 2), ("WallC", room, 4), ("WallE", evening, 6),
    ):
        obj = add_plane(collection, name, x)
        unit = project.bake_units.add()
        unit.unit_id = unit.artifact_key = new_id()
        unit.display_name, unit.render_layer_id, unit.resolution = name, baked.layer_id, '256'
        register(obj, baked.layer_id, 'BAKE', unit.unit_id)
        units[name] = unit.unit_id
    for name, collection, x in (
        ("GlassG1", room, 0), ("GlassG2", hidden_room, 2), ("GlassG3", room, 4),
        ("GlassG4", room, 6), ("GlassG5", evening, 8), ("GlassG6", room, 10),
    ):
        register(add_plane(collection, name, x), glass.layer_id, 'EXPORT_ORIGINAL')
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "visibility.blend"))
    return project, output, units


def bake_all(project, units):
    for state in ('DAY', 'EVENING'):
        activate_state(bpy.context, state)
        for unit_id in units.values():
            unit = next(u for u in project.bake_units if u.unit_id == unit_id)
            runtime = bake.BeautyBakeRuntime(bpy.context, unit)
            if runtime.prepare() == "SKIPPED":
                runtime.cleanup(keep_image=False)
                continue
            for index in range(len(runtime.receivers)):
                runtime.select_receiver(index)
                assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
            runtime.finish()


def usd_meshes(path):
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(path)
    # Mesh prims carry the mesh data name; their Xform parent is the object.
    return sorted(
        prim.GetParent().GetName() for prim in stage.Traverse()
        if prim.IsA(UsdGeom.Mesh)
    )


def glb_nodes(path):
    with open(path, "rb") as handle:
        data = handle.read()
    length, _chunk_type = struct.unpack_from("<II", data, 12)
    document = json.loads(data[20:20 + length].decode("utf-8"))
    return sorted(node["name"] for node in document.get("nodes", []) if "mesh" in node)


def sources_in(names):
    """Source names found in exported names (generated names carry a suffix)."""
    return sorted({
        source for source in (
            "WallA", "WallB", "WallC", "WallE",
            "GlassG1", "GlassG2", "GlassG3", "GlassG4", "GlassG5", "GlassG6",
        )
        if any(name.startswith(source) for name in names)
    })


def visibility():
    state = {}
    for obj in bpy.data.objects:
        try:
            hidden = obj.hide_get()
        except RuntimeError:
            hidden = "not in view layer"
        state[obj.name] = (obj.hide_render, obj.hide_viewport, hidden)
    excluded = {
        lc.collection.name: lc.exclude
        for lc in bpy.context.view_layer.layer_collection.children["VisRoot"].children
    }
    return state, excluded


def main():
    PM_VR.register()
    project, output, units = build()
    bake_all(project, units)
    activate_state(bpy.context, 'DAY')

    # The file as someone left it while working.
    bpy.context.view_layer.layer_collection.children["VisRoot"].children["HiddenRoom"].exclude = True
    bpy.data.objects["WallC"].hide_render = True
    bpy.data.objects["GlassG3"].hide_render = True
    bpy.data.objects["GlassG4"].hide_set(True)
    bpy.data.objects["GlassG6"].hide_viewport = True
    project.show_generated = False
    before = visibility()

    problems = []
    expected = {
        'DAY': (["WallA", "WallB", "WallC"], ["GlassG1", "GlassG2", "GlassG3", "GlassG4", "GlassG6"]),
        'EVENING': (
            ["WallA", "WallB", "WallC", "WallE"],
            ["GlassG1", "GlassG2", "GlassG3", "GlassG4", "GlassG5", "GlassG6"],
        ),
    }
    for state in ('DAY', 'EVENING'):
        bpy.ops.pmvr.set_lighting_state(state=state)
        before = visibility()
        result = bpy.ops.pmvr.export_semantic_layers(export_format='BOTH')
        if result != {'FINISHED'}:
            problems.append(f"{state}: export {result}: {project.last_operation_summary}")
            continue
        suffix = "" if state == 'DAY' else "_Evening"
        for layer_name, wanted in zip(("LO_Vis", "GL_Vis"), expected[state]):
            usd_path = os.path.join(output, f"{layer_name}{suffix}.usdz")
            glb_path = os.path.join(output, f"{layer_name}{suffix}.glb")
            if not (os.path.exists(usd_path) and os.path.exists(glb_path)):
                problems.append(f"{state} {layer_name}: not exported ({project.last_operation_summary})")
                continue
            usd = sources_in(usd_meshes(usd_path))
            glb = sources_in(glb_nodes(glb_path))
            for label, found in (("USDZ", usd), ("GLB", glb)):
                if found != wanted:
                    problems.append(f"{state} {layer_name} {label}: {found}, expected {wanted}")
        if visibility() != before:
            problems.append(f"{state}: export changed the file's visibility")
    for problem in problems:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_EXPORT_VISIBILITY_SMOKE_FAILED" if problems else "PM_VR_EXPORT_VISIBILITY_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
