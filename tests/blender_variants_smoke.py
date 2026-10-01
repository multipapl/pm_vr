"""Material variants: baked with their unit, exported to the Mac contract.

Run with Blender --background --factory-startup --python this_file.py.

A one-object Unlit unit (two material slots: Fabric and Legs) gets two
variants of Fabric. The queue-style bake bakes the unit, then each variant,
for Day and Evening: the variants use their material on the bake copies only
(the source keeps Fabric), the unit's own result is untouched, each variant
keeps a PNG. USDZ export writes Variants/<Object>_<Variant>[_Evening].usdz
holding only the object under its scene name, in place, with the variant's
colour and a VariantMarker; materialVariants.json. Swatches are made by
hand: export never writes or replaces them, and a default one joins the
manifest. Export
restores the object's materials and every name it borrowed. PBR units and
units of several objects are refused with a reason.
"""

import hashlib
import json
import os
import sys
import zipfile

import bpy
import numpy as np


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import blender_resolution_smoke as base  # noqa: E402
from PM_VR.modules.pipeline import bake, variants  # noqa: E402

PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def material(name, colour):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(node for node in mat.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Base Color"].default_value = colour
    return mat


def sofa(root):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=1.0)
    obj = bpy.context.object
    obj.name = "Sofa"
    obj.location.z = 1.0
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    root.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    obj.data.materials.append(material("Fabric", (0.8, 0.7, 0.5, 1)))
    obj.data.materials.append(material("Legs", (0.1, 0.1, 0.1, 1)))
    for polygon in obj.data.polygons:
        polygon.material_index = 1 if polygon.center.x > 0.25 else 0
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def bake_runtime(unit, variant_id=""):
    runtime = bake.BeautyBakeRuntime(bpy.context, unit, variant_id=variant_id)
    status = runtime.prepare()
    if status != "READY":
        return status, runtime
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    return status, runtime


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()


def mean_rgb(path):
    _size, rgb = base.load_rgb(path)
    h, w = rgb.shape[:2]
    return rgb[h // 4: 3 * h // 4, w // 8: w // 2].reshape(-1, 3).mean(axis=0)


def usd_objects(path):
    """{prim name: world translation} of Xform prims, or None without pxr."""
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        return None
    stage = Usd.Stage.Open(path)
    found = {}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Xform):
            matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            found[prim.GetName()] = tuple(round(value, 4) for value in matrix.ExtractTranslation())
    return found


def usdz_pngs(path):
    with zipfile.ZipFile(path) as archive:
        return [name for name in archive.namelist() if name.lower().endswith(".png")]


def main():
    base.PM_VR.register()
    project, root, output = base.build()
    project.bake_resolution = '256'
    project.cycles_samples = 4
    evening = bpy.data.collections["ResEvening"]
    lamp = bpy.data.objects.new("EveningLamp", bpy.data.lights.new("EveningLamp", 'POINT'))
    lamp.data.energy = 500
    lamp.location = (0, 0, 3)
    evening.objects.link(lamp)
    layers = {layer.display_name: layer for layer in project.render_layers}
    for name, layer in layers.items():
        layer.enabled = name == "Unlit"
        layer.export_glb = False

    obj = sofa(root)
    project.active_render_layer_index = list(project.render_layers).index(layers["Unlit"])
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    unit = project.bake_units[-1]
    unit.resolution = '256'

    # Refusals: a PBR unit, a unit of two objects.
    pbr_obj = base.add_plane(root, "Vase", 0.5)
    pbr_obj.data.materials.append(material("Glaze", (0.2, 0.3, 0.8, 1)))
    project.active_render_layer_index = list(project.render_layers).index(layers["PBR"])
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    try:
        refused = bpy.ops.pmvr.add_bake_variant() == {'CANCELLED'}
    except RuntimeError as exc:  # background Blender raises the operator's error
        refused = "Unlit layer" in str(exc)
    check(refused and not len(project.bake_units[-1].variants), "a PBR unit accepted a variant")
    check("Unlit layer" in variants.variant_problem(project, project.bake_units[-1]), "PBR refusal reason")
    project.active_render_layer_index = list(project.render_layers).index(layers["Unlit"])
    project.active_bake_unit_index = list(project.bake_units).index(unit)

    assert bpy.ops.pmvr.add_bake_variant() == {'FINISHED'}
    assert bpy.ops.pmvr.add_bake_variant() == {'FINISHED'}
    check(unit.variant_material == bpy.data.materials["Fabric"], "Changes did not default to the first material")
    unit.variant_default_title = "Beige"
    blue, green = unit.variants
    blue.title, blue.material = "Blue", material("FabricBlue", (0.1, 0.2, 0.9, 1))
    green.title, green.material = "Green", material("FabricGreen", (0.1, 0.8, 0.2, 1))
    check(variants.variant_problem(project, unit) == "", variants.variant_problem(project, unit))
    spot = bpy.data.objects.new("ButtonSpot", None)
    spot.location = (0.2, 0.1, 1.6)
    root.objects.link(spot)
    unit.variant_marker = spot
    holder = bpy.data.objects.new("VariantMarker", None)
    root.objects.link(holder)

    # Bake the unit and its variants, as the queue orders them.
    for state in ('DAY', 'EVENING'):
        base.activate_state(bpy.context, state)
        status, runtime = bake_runtime(unit)
        assert status == "READY"
        runtime.finish()
        main_png = bpy.path.abspath(bpy.data.images[unit.day_beauty_image if state == 'DAY' else unit.evening_beauty_image].filepath)
        main_hash = file_hash(main_png)
        for variant in unit.variants:
            status, runtime = bake_runtime(unit, variant.variant_id)
            check(status == "READY", f"{state} {variant.title}: {status}")
            check([slot.material.name for slot in obj.material_slots] == ["Fabric", "Legs"],
                  f"source materials changed during the variant bake: {[s.material.name for s in obj.material_slots]}")
            runtime.finish()
            check(variants.variant_status(unit, variant, state) == "Ready", f"{state} {variant.title} not Ready")
        check(file_hash(main_png) == main_hash, f"{state}: a variant bake changed the unit's atlas")
    beauty = bpy.path.abspath(project.beauty_output_directory)
    for name in ("Unlit_Sofa_Blue_Beauty.png", "Unlit_Sofa_Green_Evening_Beauty.png"):
        check(os.path.exists(os.path.join(beauty, name)), f"missing {name}")
    day_default = mean_rgb(bpy.path.abspath(bpy.data.images[unit.day_beauty_image].filepath))
    day_blue = mean_rgb(bpy.path.abspath(blue.day_file))
    day_green = mean_rgb(bpy.path.abspath(green.day_file))
    print(f"variants: Day fabric colour default {day_default.round(3)}, Blue {day_blue.round(3)}, Green {day_green.round(3)}")
    check(day_default[0] > day_default[2] and day_blue[2] > day_blue[0] and day_green[1] > day_green[0],
          "variant atlases do not show their colours")

    # Export Day and Evening.
    generated_materials = None
    for state in ('DAY', 'EVENING'):
        base.activate_state(bpy.context, state)
        from PM_VR.modules.pipeline import export
        generated = export._unit_generated(project, unit, export._generated_beauty_index())
        generated_materials = [m.name for m in generated.data.materials]
        assert bpy.ops.pmvr.export_semantic_layers(export_format='USDZ') == {'FINISHED'}, project.last_operation_summary
        check([m.name for m in generated.data.materials] == generated_materials, f"{state}: export left the variant material bound")
    entity = variants.usd_name(generated.name)
    check(variants.usd_name("4K_TreeLeafTerace01.001") == "_4K_TreeLeafTerace01_001",
          f"leading digit: {variants.usd_name('4K_TreeLeafTerace01.001')}")
    print(f"variants: generated object {generated.name!r} -> entity {entity!r}")
    folder = os.path.join(output, "Variants")
    files = sorted(os.listdir(folder))
    print(f"variants: files {files}")
    for name in (f"{entity}_Blue.usdz", f"{entity}_Green.usdz", f"{entity}_Blue_Evening.usdz",
                 f"{entity}_Green_Evening.usdz", "materialVariants.json"):
        check(name in files, f"Variants/{name} missing")
    check(not [f for f in files if f.endswith("_swatch.jpg")], f"export wrote swatches: {files}")

    manifest = json.load(open(os.path.join(folder, "materialVariants.json"), encoding="utf-8"))
    print(f"variants: manifest {json.dumps(manifest)}")
    expected = {"materialVariants": [{
        "id": "sofa", "title": "Sofa", "entity": entity,
        "options": [
            {"id": "default", "title": "Beige"},
            {"id": "blue", "title": "Blue", "model": f"Variants/{entity}_Blue.usdz"},
            {"id": "green", "title": "Green", "model": f"Variants/{entity}_Green.usdz"},
        ],
    }]}
    check(manifest == expected, f"manifest differs: {manifest}")

    # Hand-made swatches stay as they are; the default one joins the manifest.
    hand_made = {}
    for name in (f"{entity}_swatch.jpg", f"{entity}_Blue_swatch.jpg"):
        image = bpy.data.images.new(f"hand_{name}", 16, 16)
        image.filepath_raw = os.path.join(folder, name)
        image.file_format = 'JPEG'
        image.save()
        bpy.data.images.remove(image)
        hand_made[name] = file_hash(os.path.join(folder, name))
    base.activate_state(bpy.context, 'DAY')
    assert bpy.ops.pmvr.export_semantic_layers(export_format='USDZ') == {'FINISHED'}, project.last_operation_summary
    for name, digest in hand_made.items():
        check(file_hash(os.path.join(folder, name)) == digest, f"export replaced the hand-made {name}")
    manifest = json.load(open(os.path.join(folder, "materialVariants.json"), encoding="utf-8"))
    expected["materialVariants"][0]["options"][0]["swatch"] = f"Variants/{entity}_swatch.jpg"
    check(manifest == expected, f"manifest with the hand-made default swatch: {manifest}")

    scene_objects = usd_objects(os.path.join(output, "Unlit.usdz"))
    variant_objects = usd_objects(os.path.join(folder, f"{entity}_Blue.usdz"))
    if scene_objects is None:
        print("variants: pxr not available, USD structure not checked")
    else:
        print(f"variants: scene prims {scene_objects}; variant prims {variant_objects}")
        check(entity in scene_objects, f"entity {entity} not in the scene USDZ")
        check(set(variant_objects) - {entity, "VariantMarker", "root", "_materials"} == set(),
              f"variant file holds more than the object: {variant_objects}")
        check(variant_objects.get(entity) == scene_objects.get(entity), "variant object is not in place")
        check(variant_objects.get("VariantMarker") == (0.2, 0.1, 1.6), f"marker at {variant_objects.get('VariantMarker')}")
    check(usdz_pngs(os.path.join(folder, f"{entity}_Blue.usdz")) == ["textures/Unlit_Sofa_Blue_Beauty.png"],
          f"variant texture {usdz_pngs(os.path.join(folder, f'{entity}_Blue.usdz'))}")
    check(usdz_pngs(os.path.join(output, "Unlit.usdz")) == ["textures/Unlit_Sofa_Beauty.png"],
          f"scene texture {usdz_pngs(os.path.join(output, 'Unlit.usdz'))}")
    check(holder.name == "VariantMarker" and not bpy.data.objects.get("VariantMarker.001"), "marker name not restored")
    check(not [image.name for image in bpy.data.images if image.name.startswith("Unlit_Sofa_Blue")],
          "export left variant images in the file")

    # A variant without an Evening bake is left out of the Evening export.
    os.remove(os.path.join(folder, f"{entity}_Green_Evening.usdz"))
    green.evening_file = ""
    base.activate_state(bpy.context, 'EVENING')
    bpy.ops.pmvr.export_semantic_layers(export_format='USDZ')
    check(not os.path.exists(os.path.join(folder, f"{entity}_Green_Evening.usdz")), "unbaked Evening variant exported")
    check('variant "Green": no Evening bake' in bpy.data.texts["PMVR Pipeline Log"].as_string(), "log misses the Evening gap")

    # A partial export (another layer only) keeps the manifest.
    before = json.load(open(os.path.join(folder, "materialVariants.json"), encoding="utf-8"))
    layers["Unlit"].enabled = False
    layers["Emissive"].enabled = True
    base.activate_state(bpy.context, 'DAY')
    bpy.ops.pmvr.export_semantic_layers(export_format='USDZ')
    after = json.load(open(os.path.join(folder, "materialVariants.json"), encoding="utf-8"))
    check(after == before and after["materialVariants"], f"partial export rewrote the manifest: {after}")
    layers["Unlit"].enabled = True
    layers["Emissive"].enabled = False

    # Two objects in the unit: refused with the reason.
    bpy.ops.object.select_all(action='DESELECT')
    pbr_obj.select_set(True)
    bpy.context.view_layer.objects.active = pbr_obj
    project.active_bake_unit_index = list(project.bake_units).index(unit)
    bpy.ops.pmvr.assign_selected_to_unit()
    check("one object" in variants.variant_problem(project, unit), variants.variant_problem(project, unit))

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_VARIANTS_SMOKE_FAILED" if PROBLEMS else "PM_VR_VARIANTS_SMOKE_OK")
    base.PM_VR.unregister()


if __name__ == "__main__":
    main()
