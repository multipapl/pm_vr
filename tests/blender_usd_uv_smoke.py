"""Baked objects reach USD with the atlas on "st" and the authored layout on "UVMap".

Run with Blender --background --factory-startup --python this_file.py.

The bake copy renders with UVMap and the generated object inherited that flag;
Blender's USD export names the render UV map "st" on the mesh but the active
one in materials, so such objects shipped "SimpleBake, st" with materials
reading a "UVMap" that did not exist (UniPlace, 2026-09-29). Checked here:
- after a real bake every generated object has SimpleBake as active and
  render UV map, and a tangent Normal Map in a generated PBR material names
  UVMap;
- the exported "st" holds the SimpleBake coordinates and "UVMap" the
  authored ones; the atlas reads "st", roughness and opacity read "UVMap";
- a file saved with the old flags is repaired on open, a flag switched back
  by hand is repaired at export;
- usd_check finds the old failure in a file written without the fix, and a
  texture missing from the package; the export operator counts such a file
  as failed and logs each problem.
"""

import os
import sys
import tempfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules import collection_export  # noqa: E402
from PM_VR.modules.pipeline import bake, export, usd_check  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)
        print(f"[PM VR] FAIL: {message}")


def image(output, name, colour):
    img = bpy.data.images.new(name, 16, 16)
    img.pixels = list(colour) * (16 * 16)
    img.filepath_raw = os.path.join(output, f"{name}.png")
    img.file_format = 'PNG'
    img.save()
    return img


def add_plane(collection, name, x, material):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    for loop in obj.data.uv_layers[0].data:
        loop.uv = (loop.uv[0] * 2.0 - 0.5, loop.uv[1] * 2.0 - 0.5)
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for index, loop in enumerate(bake_uv.data):
        loop.uv = (0.1 + 0.2 * (index % 2), 0.1 + 0.2 * (index // 2))
    # As on most UniPlace sources: the camera on SimpleBake before the bake.
    obj.data.uv_layers.active = bake_uv
    bake_uv.active_render = True
    obj.data.materials.append(material)
    return obj


def pbr_material(output):
    material = bpy.data.materials.new("UvPanelMat")
    material.use_nodes = True
    tree = material.node_tree
    principled = tree.nodes["Principled BSDF"]
    roughness = tree.nodes.new("ShaderNodeTexImage")
    roughness.image = image(output, "UvRoughness", (0.4, 0.4, 0.4, 1.0))
    roughness.image.colorspace_settings.name = 'Non-Color'
    tree.links.new(roughness.outputs["Color"], principled.inputs["Roughness"])
    normal_image = tree.nodes.new("ShaderNodeTexImage")
    normal_image.image = image(output, "UvNormal", (0.5, 0.5, 1.0, 1.0))
    normal_image.image.colorspace_settings.name = 'Non-Color'
    normal = tree.nodes.new("ShaderNodeNormalMap")
    tree.links.new(normal_image.outputs["Color"], normal.inputs["Color"])
    tree.links.new(normal.outputs["Normal"], principled.inputs["Normal"])
    principled.inputs["Base Color"].default_value = (0.6, 0.3, 0.2, 1.0)
    return material


def alpha_material(output):
    material = bpy.data.materials.new("UvLeafMat")
    material.use_nodes = True
    tree = material.node_tree
    principled = tree.nodes["Principled BSDF"]
    mask = tree.nodes.new("ShaderNodeTexImage")
    mask.image = image(output, "UvOpacity", (1.0, 1.0, 1.0, 1.0))
    mask.image.colorspace_settings.name = 'Non-Color'
    tree.links.new(mask.outputs["Color"], principled.inputs["Alpha"])
    principled.inputs["Base Color"].default_value = (0.2, 0.6, 0.2, 1.0)
    return material


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 1
    project.bake_resolution = '256'
    output = tempfile.mkdtemp(prefix="pmvr_usd_uv_")
    for attribute in ("beauty_output_directory", "usdz_output_directory", "glb_output_directory"):
        setattr(project, attribute, output + os.sep)
    root = bpy.data.collections.new("UvRoot")
    scene.collection.children.link(root)
    day = bpy.data.collections.new("UvDay")
    evening = bpy.data.collections.new("UvEvening")
    room = bpy.data.collections.new("UvRoom")
    for collection in (day, evening, room):
        root.children.link(collection)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("UvDayWorld")
    project.evening_world = bpy.data.worlds.new("UvEveningWorld")
    day.objects.link(bpy.data.objects.new("UvSun", bpy.data.lights.new("UvSun", 'SUN')))

    unlit_material = bpy.data.materials.new("UvWallMat")
    unlit_material.use_nodes = True
    members = {}
    for index, (layer_name, layer_type, object_name, material) in enumerate((
        ("LO_Uv", 'UNLIT', "UvWall", unlit_material),
        ("PB_Uv", 'PBR', "UvPanel", pbr_material(output)),
        ("AL_Uv", 'ALPHA', "UvLeaf", alpha_material(output)),
    )):
        layer = project.render_layers.add()
        layer.layer_id, layer.display_name, layer.layer_type = new_id(), layer_name, layer_type
        layer.export_usdz, layer.export_glb = True, False
        obj = add_plane(room, object_name, index * 2, material)
        unit = project.bake_units.add()
        unit.unit_id = unit.artifact_key = new_id()
        unit.display_name, unit.render_layer_id, unit.resolution = object_name, layer.layer_id, '256'
        meta = obj.pm_vr_pipeline
        meta.source_id = new_id()
        meta.is_registered_source = True
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'BAKE'
        meta.bake_unit_id = unit.unit_id
        members[object_name] = unit.unit_id
    path = os.path.join(output, "usd_uv.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return project, output, members, path


def bake_day(project, members):
    activate_state(bpy.context, 'DAY')
    for unit_id in members.values():
        unit = next(u for u in project.bake_units if u.unit_id == unit_id)
        runtime = bake.BeautyBakeRuntime(bpy.context, unit)
        assert runtime.prepare() != "SKIPPED"
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()


def generated(name):
    return next(
        obj for obj in bpy.data.objects
        if obj.get("pmvr_generated") and obj.name.startswith(name)
    )


def flags(obj):
    uv_layers = obj.data.uv_layers
    render = next((layer.name for layer in uv_layers if layer.active_render), None)
    return uv_layers.active.name, render


def break_flags(obj):
    """What 76 UniPlace objects had: active SimpleBake, camera on UVMap."""
    obj.data.uv_layers["UVMap"].active_render = True


def normal_map_uv(obj):
    return [
        node.uv_map for slot in obj.material_slots if slot.material
        for node in slot.material.node_tree.nodes if node.type == 'NORMAL_MAP'
    ]


def usd_mesh(path, name):
    """UV sets as {name: sorted coordinates} and texture -> varname read."""
    from pxr import Sdf, Usd, UsdGeom, UsdShade

    stage = Usd.Stage.Open(path)
    prim = next(
        p for p in stage.Traverse()
        if p.IsA(UsdGeom.Mesh) and p.GetParent().GetName().startswith(name)
    )
    uv_sets = {}
    for primvar in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
        if primvar.GetTypeName() == Sdf.ValueTypeNames.TexCoord2fArray:
            uv_sets[primvar.GetPrimvarName()] = sorted(
                (round(uv[0], 4), round(uv[1], 4)) for uv in primvar.Get()
            )
    material, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    reads = {}
    for shader_prim in Usd.PrimRange(material.GetPrim()):
        shader = UsdShade.Shader(shader_prim)
        if shader and shader.GetIdAttr().Get() == "UsdUVTexture":
            source = shader.GetInput("st").GetConnectedSources()[0][0].source
            reader = UsdShade.Shader(source.GetPrim())
            texture = os.path.basename(shader.GetInput("file").Get().path)
            reads[texture] = str(reader.GetInput("varname").Get())
    return uv_sets, reads


def blender_uvs(obj, layer):
    return sorted((round(loop.uv[0], 4), round(loop.uv[1], 4)) for loop in obj.data.uv_layers[layer].data)


def log_text():
    text = bpy.data.texts.get("PMVR Pipeline Log")
    return text.as_string() if text else ""


def main():
    PM_VR.register()
    project, output, members, blend_path = build()
    bake_day(project, members)
    names = list(members)

    # 1. A fresh bake leaves SimpleBake active and render on every result.
    for name in names:
        check(flags(generated(name)) == ("SimpleBake", "SimpleBake"), f"{name} after bake: {flags(generated(name))}")
    check(normal_map_uv(generated("UvPanel")) == ["UVMap"], f"PBR normal map uv {normal_map_uv(generated('UvPanel'))}")
    check(normal_map_uv(bpy.data.objects["UvPanel"]) == [""], "the source material's normal map was changed")

    # 2. Export: the atlas is on "st" with the SimpleBake coordinates.
    result = bpy.ops.pmvr.export_semantic_layers(export_format='USDZ')
    check(result == {'FINISHED'}, f"export {result}: {project.last_operation_summary}")
    check(" 0 failed" in project.last_operation_summary, project.last_operation_summary)
    for layer_name, name in (("LO_Uv", "UvWall"), ("PB_Uv", "UvPanel"), ("AL_Uv", "UvLeaf")):
        path = os.path.join(output, f"{layer_name}.usdz")
        check(usd_check.check_usd(path) == [], f"{layer_name}: {usd_check.check_usd(path)}")
        uv_sets, reads = usd_mesh(path, name)
        obj = generated(name)
        check(sorted(uv_sets) == ["UVMap", "st"], f"{name} UV sets {sorted(uv_sets)}")
        check(uv_sets.get("st") == blender_uvs(obj, "SimpleBake"), f"{name}: st is not the SimpleBake layout")
        check(uv_sets.get("UVMap") == blender_uvs(obj, "UVMap"), f"{name}: UVMap is not the authored layout")
        atlas = [varname for texture, varname in reads.items() if texture.endswith("_Beauty.png")]
        others = {texture: varname for texture, varname in reads.items() if not texture.endswith("_Beauty.png")}
        check(atlas == ["st"], f"{name}: atlas reads {atlas}")
        check(all(varname == "UVMap" for varname in others.values()), f"{name}: textures read {others}")
        if name != "UvWall":
            check(others, f"{name}: no source texture in USD")

    # 3. usd_check finds the old failure in a file written without the fix.
    for name in names:
        break_flags(generated(name))
    old_file = os.path.join(output, "old_flags.usdz")
    assembly = bpy.data.collections.new("UvOld")
    bpy.context.scene.collection.children.link(assembly)
    for name in names:
        assembly.objects.link(generated(name))
    collection_export.export_usdz(assembly, old_file)
    found = usd_check.check_usd(old_file)
    for name in names:
        check(any(p.startswith(name) and "UV sets SimpleBake, st" in p for p in found), f"{name}: not caught in {found}")
    for name in ("UvPanel", "UvLeaf"):
        check(any(p.startswith(name) and "reads UV set UVMap" in p for p in found), f"{name}: missing UVMap not caught")
    roughness = bpy.data.images["UvRoughness"]
    kept_path = roughness.filepath
    roughness.filepath = os.path.join(output, "gone", "UvRoughness.png")
    missing_file = os.path.join(output, "missing_texture.usdz")
    collection_export.export_usdz(assembly, missing_file)
    roughness.filepath = kept_path
    missing = [p for p in usd_check.check_usd(missing_file) if "is not in the file" in p]
    check(len(missing) == 1 and "UvRoughness.png" in missing[0], f"missing texture: {missing}")
    bpy.context.scene.collection.children.unlink(assembly)
    bpy.data.collections.remove(assembly)

    # 4. A file saved with the old flags is repaired on open.
    for material in bpy.data.materials:
        for node in material.node_tree.nodes if material.get("pmvr_generated") and material.node_tree else ():
            if node.type == 'NORMAL_MAP':
                node.uv_map = ""
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    project = bpy.context.scene.pm_vr_project
    for name in names:
        check(flags(generated(name)) == ("SimpleBake", "SimpleBake"), f"{name} after reopen: {flags(generated(name))}")
    check(normal_map_uv(generated("UvPanel")) == ["UVMap"], "normal map not pinned on open")
    check("SimpleBake made active and render UV on 3 generated object(s)" in log_text(), "open: no log line")

    # 5. A flag switched by hand afterwards is repaired at export.
    break_flags(generated("UvLeaf"))
    result = bpy.ops.pmvr.export_semantic_layers(export_format='USDZ')
    check(result == {'FINISHED'} and " 0 failed" in project.last_operation_summary, project.last_operation_summary)
    check(flags(generated("UvLeaf")) == ("SimpleBake", "SimpleBake"), "export did not repair the flag")
    check("AL_Uv (USDZ): SimpleBake made active and render UV on 1 generated object(s): UvLeaf" in log_text(),
          "export: no log line")
    check(usd_check.check_usd(os.path.join(output, "AL_Uv.usdz")) == [], "AL_Uv not clean after repair")

    # 6. Without the repair, the operator reports the file as failed.
    break_flags(generated("UvPanel"))
    original = export.use_bake_uv
    export.use_bake_uv = lambda obj: False
    try:
        bpy.ops.pmvr.export_semantic_layers(export_format='USDZ')
    finally:
        export.use_bake_uv = original
    summary = project.last_operation_summary
    check(" 1 failed" in summary and "2 ready" in summary, f"broken file not counted as failed: {summary}")
    check("USD check PB_Uv.usdz: UvPanel" in log_text(), "problem not logged")
    check(os.path.exists(os.path.join(output, "PB_Uv.usdz")), "failed file not written")

    print("PM_VR_USD_UV_SMOKE_FAILED" if PROBLEMS else "PM_VR_USD_UV_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
