"""Successful rebakes refresh geometry/UVs without changing object identity.

Real CPU bakes reproduce a legacy-signature SimpleBake repack, then edits
which leave the compatibility signature unchanged. USDZ is inspected with
pxr for stable prim paths, current points/UVs and the correct packaged PNG.
A material-slot failure must be refused before old artifacts are replaced.
"""

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy
import numpy as np
from pxr import Sdf, Tf, Usd, UsdGeom, UsdShade

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import bake, export, generated
from PM_VR.modules.pipeline.bake_scene import PipelineBakeError
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state

PROBLEMS = []
REPORT = {}


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def values(collection, field, width=1):
    result = np.empty(len(collection) * width, dtype=np.float32)
    if result.size:
        collection.foreach_get(field, result)
    return result.reshape(-1, width)


def source(root, name, x, atlas_x):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0.0, 1.0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    root.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in uv.data:
        loop.uv = (atlas_x + loop.uv.x * 0.3, 0.1 + loop.uv.y * 0.3)
    material = bpy.data.materials.new(name + " Material")
    material.use_nodes = True
    bsdf = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Base Color"].default_value = (0.7, 0.2, 0.1, 1.0)
    obj.data.materials.append(material)
    return obj


def add_unit(project, layer, members, name):
    unit = project.bake_units.add()
    unit.unit_id = new_id()
    unit.artifact_key = unit.unit_id
    unit.display_name = name
    unit.render_layer_id = layer.layer_id
    unit.resolution = '256'
    for obj in members:
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.processing_role = 'BAKE'
        meta.render_layer_id = layer.layer_id
        meta.bake_unit_id = unit.unit_id
    return unit


def bake_unit(unit, state):
    activate_state(bpy.context, state)
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    try:
        assert runtime.prepare() == "READY"
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()
    finally:
        runtime.cleanup(keep_image=runtime.finished)


def generated_for(unit, obj):
    return generated.find_generated(unit.unit_id, obj.pm_vr_pipeline.source_id)


def write_usdz(project, layer, objects, path):
    textures = export.ExportTextures()
    try:
        export._write_usdz(bpy.context, project, layer, objects, str(path), textures)
    finally:
        textures.cleanup()


def inspect_usdz(path, members, unit):
    stage = Usd.Stage.Open(str(path))
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    for obj in members:
        gen = generated_for(unit, obj)
        entity = Tf.MakeValidIdentifier(gen.name)
        matches = [p for p in meshes if p.GetName() == entity]
        if not matches:
            matches = [p for p in meshes if p.GetParent().GetName() == entity]
        check(len(matches) == 1, f"USDZ mesh missing/ambiguous: {gen.name}")
        if len(matches) != 1:
            continue
        mesh = UsdGeom.Mesh(matches[0])
        points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float32)
        check(np.allclose(points, values(obj.data.vertices, "co", 3), atol=1e-6),
              f"USDZ has stale points: {gen.name}")
        pv = UsdGeom.PrimvarsAPI(matches[0])
        names = {v.GetPrimvarName() for v in pv.GetPrimvars()
                 if str(v.GetTypeName()) == "texCoord2f[]"}
        check(names == {"st", "UVMap"}, f"USDZ UV names changed: {gen.name}: {names}")
        for name, source_name in (("st", "SimpleBake"), ("UVMap", "UVMap")):
            coords = np.asarray(pv.GetPrimvar(name).ComputeFlattened(), dtype=np.float32)
            check(np.allclose(coords, values(obj.data.uv_layers[source_name].data, "uv", 2), atol=1e-6),
                  f"USDZ has stale {name}: {gen.name}")
        binding = UsdShade.MaterialBindingAPI(matches[0]).ComputeBoundMaterial()[0]
        check(bool(binding), f"USDZ material missing: {gen.name}")
    image = bpy.data.images[unit.day_beauty_image]
    expected_png = hashlib.sha256(Path(bpy.path.abspath(image.filepath)).read_bytes()).hexdigest()
    packaged = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".png"):
                packaged.append(hashlib.sha256(archive.read(name)).hexdigest())
    check(expected_png in packaged, "USDZ did not package the current Day PNG")
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        if shader:
            inp = shader.GetInput("file")
            if inp:
                asset = inp.Get()
                check(isinstance(asset, Sdf.AssetPath) and bool(asset.resolvedPath),
                      f"USDZ texture asset is unresolved: {prim.GetPath()}")
    return [str(p.GetPath()) for p in stage.Traverse()]


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.cycles.device = 'CPU'
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 1
    project.bake_resolution = '256'
    project.beauty_denoise = 'OFF'
    project.fill_empty_uv = False
    output = Path(tempfile.mkdtemp(prefix="pmvr_rebake_geometry_"))
    for attr in ("beauty_output_directory", "lightmap_output_directory",
                 "usdz_output_directory", "glb_output_directory", "probe_output_directory"):
        setattr(project, attr, str(output) + os.sep)
    root = bpy.data.collections.new("Rebake Sources")
    scene.collection.children.link(root)
    day = bpy.data.collections.new("Rebake Day")
    evening = bpy.data.collections.new("Rebake Evening")
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Rebake Day World")
    project.evening_world = bpy.data.worlds.new("Rebake Evening World")
    for collection in (day, evening):
        lamp = bpy.data.objects.new(collection.name + " Sun", bpy.data.lights.new(collection.name + " Sun", 'SUN'))
        collection.objects.link(lamp)
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = "Rebake_Unlit"
    layer.layer_type = 'UNLIT'
    parent = source(root, "RepackParent", 0.0, 0.1)
    child = source(root, "RepackChild", 2.0, 0.6)
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = world
    members = [parent, child]
    unit = add_unit(project, layer, members, "Shared Repack")
    bake_unit(unit, 'DAY')
    identities = {
        obj.name: {"pointer": generated_for(unit, obj).as_pointer(),
                   "name": generated_for(unit, obj).name,
                   "mesh_name": generated_for(unit, obj).data.name,
                   "tags": {k: generated_for(unit, obj).get(k)
                            for k in ("pmvr_source_id", "pmvr_unit_id", "pmvr_layer_id", "pmvr_schema")}}
        for obj in members
    }
    baseline = output / "before.usdz"
    write_usdz(project, layer, [generated_for(unit, obj) for obj in members], baseline)
    old_paths = inspect_usdz(baseline, members, unit)

    # A real existing v1 record is accepted for compatibility. That must
    # never justify retaining its generated UVs after a successful repack.
    unit.day_signature = unit.day_signature.removeprefix("2:")
    for loop in parent.data.uv_layers["SimpleBake"].data:
        x, y = loop.uv
        loop.uv = (0.5 - y, x)
    bake_unit(unit, 'DAY')
    for obj in members:
        check(np.array_equal(values(generated_for(unit, obj).data.uv_layers["SimpleBake"].data, "uv", 2),
                             values(obj.data.uv_layers["SimpleBake"].data, "uv", 2)),
              f"legacy-signature repack kept stale UVs: {obj.name}")
    bake_unit(unit, 'EVENING')
    check(unit.day_signature == unit.evening_signature, "Day/Evening signatures disagree after repack")

    # Vertex positions and the primary UV channel are intentionally outside
    # the existing signature. Keep that contract; refresh geometry anyway.
    old_signature = unit.day_signature
    parent.data.vertices[0].co.z += 0.25
    for loop in parent.data.uv_layers["UVMap"].data:
        loop.uv.x = 1.0 - loop.uv.x
    parent.data.update()
    bake_unit(unit, 'DAY')
    check(unit.day_signature == old_signature, "signature contract was changed")
    for obj in members:
        gen = generated_for(unit, obj)
        check(np.array_equal(values(gen.data.vertices, "co", 3), values(obj.data.vertices, "co", 3)),
              f"rebake kept stale vertex positions: {obj.name}")
        for uv_name in ("UVMap", "SimpleBake"):
            check(np.array_equal(values(gen.data.uv_layers[uv_name].data, "uv", 2),
                                 values(obj.data.uv_layers[uv_name].data, "uv", 2)),
                  f"rebake kept stale {uv_name}: {obj.name}")
        before = identities[obj.name]
        check(gen.as_pointer() == before["pointer"] and gen.name == before["name"],
              f"rebake changed canonical object identity: {obj.name}")
        check(gen.data.name == before["mesh_name"], f"rebake changed mesh name: {obj.name}")
        check(all(gen.get(k) == v for k, v in before["tags"].items()), f"rebake changed tags: {obj.name}")
        check(np.allclose(gen.matrix_world, obj.matrix_world), f"rebake shifted {obj.name}")
    check(generated_for(unit, child).parent == generated_for(unit, parent), "rebake broke parent link")
    after = output / "after.usdz"
    write_usdz(project, layer, [generated_for(unit, obj) for obj in members], after)
    new_paths = inspect_usdz(after, members, unit)
    check(new_paths == old_paths, "rebake changed USD prim paths")
    REPORT["usd_prim_paths_preserved"] = new_paths == old_paths

    # Preflight must check the NEW evaluated mesh, not the compatible old
    # mesh. Simulate an evaluated modifier result with an extra slot.
    pbr_layer = project.render_layers.add()
    pbr_layer.layer_id = new_id()
    pbr_layer.display_name = "Rebake_PBR"
    pbr_layer.layer_type = 'PBR'
    pbr = source(root, "PbrPanel", 4.0, 0.1)
    pbr_unit = add_unit(project, pbr_layer, [pbr], "Pbr Preflight")
    bake_unit(pbr_unit, 'DAY')
    gen = generated_for(pbr_unit, pbr)
    mesh_pointer = gen.data.as_pointer()
    materials = list(gen.data.materials)
    png = Path(bpy.path.abspath(bpy.data.images[pbr_unit.day_beauty_image].filepath))
    png_hash = hashlib.sha256(png.read_bytes()).hexdigest()
    pbr_unit.day_signature = pbr_unit.day_signature.removeprefix("2:")
    runtime = bake.BeautyBakeRuntime(bpy.context, pbr_unit)
    refused = False
    try:
        assert runtime.prepare() == "READY"
        runtime.receivers[0]["mesh"].materials.append(bpy.data.materials.new("Modifier Extra Slot"))
        runtime.finish()
    except PipelineBakeError as exc:
        refused = "evaluated mesh has" in str(exc)
    finally:
        runtime.cleanup(keep_image=runtime.finished)
    check(refused, "fresh evaluated material mismatch was not refused before commit")
    check(gen.data.as_pointer() == mesh_pointer, "failed rebake replaced the old mesh")
    check(list(gen.data.materials) == materials, "failed rebake replaced the old materials")
    check(hashlib.sha256(png.read_bytes()).hexdigest() == png_hash, "failed rebake replaced the old PNG")
    REPORT.update({"output": str(output), "problems": PROBLEMS, "cpu_only": True})
    (output / "result.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    for problem in PROBLEMS:
        print("[PM VR] FAIL:", problem, flush=True)
    print("PMVR_REBAKE_RESULT", json.dumps(REPORT), flush=True)
    print("PM_VR_REBAKE_GEOMETRY_SMOKE_FAILED" if PROBLEMS else "PM_VR_REBAKE_GEOMETRY_SMOKE_OK", flush=True)
    PM_VR.unregister()
    if PROBLEMS:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
