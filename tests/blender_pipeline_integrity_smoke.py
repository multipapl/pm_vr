"""Regression scenarios for artifact integrity found during the pipeline audit.

Run with Blender --background --factory-startup --python this_file.py.

Covers:
- Day/Evening materials survive save/reopen and each state's export embeds
  its own Beauty texture;
- export keeps PBR/Alpha polygon material slots and restores the preview binding;
- a bake that fails late leaves the previous PNG, mesh and materials current;
- a baked child keeps its generated parent from another unit of the layer;
- the USDZ root layer carries the final file name;
- duplicated generated objects block export instead of exporting either copy;
- removing a unit releases its protected generated materials;
- a file saved (or recovered) mid-bake gets its temporary bake state undone on load;
- Shift+D gives copies of sources and generated objects their own identity, while
  IDs already shared in a loaded file are left to the explicit repair operator;
- linked duplicates bake independently in separate units (object-linked
  materials included) and are refused inside one unit.
"""

import glob
import hashlib
import json
import os
import struct
import sys
import tempfile
import zipfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, export  # noqa: E402
from PM_VR.modules.pipeline import validation  # noqa: E402
from PM_VR.modules.pipeline.identity import duplicate_source_ids, new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def principled(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    node = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs["Base Color"].default_value = (*color, 1.0)
    return material


def build_project():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    for collection in (project.render_layers, project.bake_units, project.bake_queue, project.build_records):
        collection.clear()
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 1
    project.bake_resolution = '256'
    project.uv_padding = 0.008
    output = tempfile.mkdtemp(prefix="pmvr_integrity_")
    for attribute in (
        "beauty_output_directory", "glb_output_directory",
        "usdz_output_directory", "lightmap_output_directory",
    ):
        setattr(project, attribute, output + os.sep)
    root = bpy.data.collections.new("Integrity Root")
    day = bpy.data.collections.new("Integrity Day")
    evening = bpy.data.collections.new("Integrity Evening")
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Integrity Day World")
    project.evening_world = bpy.data.worlds.new("Integrity Evening World")
    sun = bpy.data.objects.new("Integrity Sun", bpy.data.lights.new("Integrity Sun", 'SUN'))
    day.objects.link(sun)
    lamp = bpy.data.objects.new("Integrity Lamp", bpy.data.lights.new("Integrity Lamp", 'POINT'))
    lamp.data.energy = 300.0
    lamp.location = (0.0, 0.0, 3.0)
    evening.objects.link(lamp)
    return project, root, output


def add_source(collection, name, location, materials=None, primitive="cube"):
    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    else:
        bpy.ops.mesh.primitive_plane_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    for material in materials or [principled(f"{name} Material", (0.8, 0.3, 0.2))]:
        obj.data.materials.append(material)
    if len(obj.data.materials) > 1:
        for index, polygon in enumerate(obj.data.polygons):
            polygon.material_index = index % len(obj.data.materials)
    return obj


def add_layer(project, name, layer_type):
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = name
    layer.layer_type = layer_type
    return layer.layer_id


def add_unit(project, layer_id, name, members):
    unit = project.bake_units.add()
    unit.unit_id = new_id()
    unit.artifact_key = unit.unit_id
    unit.display_name = name
    unit.render_layer_id = layer_id
    unit.resolution = '256'
    for obj in members:
        metadata = obj.pm_vr_pipeline
        metadata.source_id = metadata.source_id or new_id()
        metadata.is_registered_source = True
        metadata.render_layer_id = layer_id
        metadata.processing_role = 'BAKE'
        metadata.bake_unit_id = unit.unit_id
    return unit.unit_id


def unit_by_id(unit_id):
    return next(u for u in bpy.context.scene.pm_vr_project.bake_units if u.unit_id == unit_id)


def layer_by_id(layer_id):
    return next(l for l in bpy.context.scene.pm_vr_project.render_layers if l.layer_id == layer_id)


def bake_unit(unit_id):
    """Drive the modal queue's runtime synchronously with EXEC bakes."""
    runtime = bake.BeautyBakeRuntime(bpy.context, unit_by_id(unit_id))
    try:
        if runtime.prepare() == "SKIPPED":
            runtime.cleanup(keep_image=False)
            return "SKIPPED"
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()
        return "SUCCESS"
    except Exception as exc:
        runtime.fail(exc)
        return f"FAILED: {exc}"


def generated_for(obj):
    source_id = obj.pm_vr_pipeline.source_id
    return next(
        item for item in bpy.data.objects
        if item.get("pmvr_generated") and item.get("pmvr_source_id") == source_id
        and item.get("pmvr_mode") == 'BEAUTY'
    )


def usd_textures(path):
    from pxr import Sdf, Usd, UsdShade

    textures = []
    stage = Usd.Stage.Open(path)
    for prim in stage.Traverse():
        shader = UsdShade.Shader(prim)
        file_input = shader.GetInput("file") if shader else None
        value = file_input.Get() if file_input else None
        if isinstance(value, Sdf.AssetPath):
            textures.append(os.path.basename(value.path))
    return textures


def usd_prim_paths(path):
    from pxr import Usd

    stage = Usd.Stage.Open(path)
    return [str(prim.GetPath()) for prim in stage.Traverse()]


def glb_json(path):
    with open(path, "rb") as handle:
        data = handle.read()
    length, _chunk_type = struct.unpack_from("<II", data, 12)
    return json.loads(data[20:20 + length].decode("utf-8"))


def scenario_state_isolation_across_reopen():
    project, root, output = build_project()
    layer_id = add_layer(project, "LO_State", 'UNLIT')
    wall = add_source(root, "State Wall", (0, 0, 0), primitive="plane")
    unit_id = add_unit(project, layer_id, "StateUnit", [wall])
    blend = os.path.join(output, "state.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    for state in ('DAY', 'EVENING'):
        activate_state(bpy.context, state)
        assert bake_unit(unit_id) == "SUCCESS"
    # Generated objects are bound to Evening; the Day material has no object user.
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    states = {
        material.get("pmvr_state") for material in bpy.data.materials
        if material.get("pmvr_generated") and material.get("pmvr_unit_id") == unit_id
    }
    assert states == {'DAY', 'EVENING'}, states
    layer = layer_by_id(layer_id)
    for state, expect_evening in (('DAY', False), ('EVENING', True)):
        activate_state(bpy.context, state)
        status, path = export.export_semantic_layer(bpy.context, layer, 'USDZ')
        assert status == 'SUCCESS', path
        textures = usd_textures(path)
        assert textures and all(("_Evening_" in name) == expect_evening for name in textures), (state, textures)
        with zipfile.ZipFile(path) as package:
            root_layer = package.namelist()[0]
        assert root_layer == os.path.splitext(os.path.basename(path))[0] + ".usdc", root_layer
    print("integrity: state isolation across reopen OK")


def scenario_pbr_slots_and_binding_restore():
    project, root, _output = build_project()
    layer_id = add_layer(project, "LO_PBR", 'PBR')
    chair = add_source(root, "PBR Chair", (0, 0, 0), [principled("Red", (0.9, 0.1, 0.1)), principled("Blue", (0.1, 0.1, 0.9))])
    unit_id = add_unit(project, layer_id, "ChairUnit", [chair])
    source_slots = [p.material_index for p in chair.data.polygons]
    for state in ('DAY', 'EVENING'):
        activate_state(bpy.context, state)
        assert bake_unit(unit_id) == "SUCCESS"
    generated = generated_for(chair)
    preview = [m.name for m in generated.data.materials]
    assert all(m.get("pmvr_state") == 'EVENING' for m in generated.data.materials)
    activate_state(bpy.context, 'DAY')
    status, path = export.export_semantic_layer(bpy.context, layer_by_id(layer_id), 'GLB')
    assert status == 'SUCCESS', path
    assert [p.material_index for p in generated.data.polygons] == source_slots
    assert [m.name for m in generated.data.materials] == preview, "export must restore the preview binding"
    gltf = glb_json(path)
    primitives = [len(mesh["primitives"]) for mesh in gltf["meshes"]]
    assert primitives == [2], primitives
    material_names = {m["name"] for m in gltf["materials"]}
    assert all("_DAY_" in name for name in material_names), material_names
    bpy.ops.pmvr.apply_preview_visibility()
    assert [p.material_index for p in generated.data.polygons] == source_slots
    print("integrity: PBR slots and binding restore OK")


def scenario_late_failure_keeps_previous_result():
    project, root, output = build_project()
    layer_id = add_layer(project, "LO_Fail", 'PBR')
    chair = add_source(root, "Fail Chair", (0, 0, 0))
    unit_id = add_unit(project, layer_id, "FailUnit", [chair])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "fail.blend"))
    activate_state(bpy.context, 'DAY')
    assert bake_unit(unit_id) == "SUCCESS"
    png = glob.glob(os.path.join(output, "*_Beauty.png"))
    assert len(png) == 1, png
    previous_hash = file_hash(png[0])
    generated = generated_for(chair)
    previous_mesh = generated.data.name
    previous_materials = [m.name for m in generated.data.materials]
    # A Geometry Nodes material assignment passes validation but produces a
    # material slot the source object does not have.
    group = bpy.data.node_groups.new("Integrity SetMaterial", 'GeometryNodeTree')
    group.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group_in = group.nodes.new('NodeGroupInput')
    group_out = group.nodes.new('NodeGroupOutput')
    set_material = group.nodes.new('GeometryNodeSetMaterial')
    set_material.inputs['Material'].default_value = principled("Green", (0.1, 0.8, 0.1))
    group.links.new(group_in.outputs[0], set_material.inputs['Geometry'])
    group.links.new(set_material.outputs['Geometry'], group_out.inputs[0])
    chair.modifiers.new("Integrity GN", 'NODES').node_group = group
    bpy.data.objects["Integrity Sun"].data.energy = 0.1
    status = bake_unit(unit_id)
    assert status.startswith("FAILED"), status
    assert file_hash(png[0]) == previous_hash, "previous Beauty PNG was overwritten"
    assert generated.data.name == previous_mesh
    assert [m.name for m in generated.data.materials] == previous_materials
    assert not [o.name for o in bpy.data.objects if o.name.startswith("__PMVR")]
    assert not [f for f in os.listdir(output) if "pmvr_tmp" in f or "pmvr_denoise" in f]
    assert unit_by_id(unit_id).day_status == "Ready"
    print("integrity: late failure keeps previous result OK")


def scenario_hierarchy_duplicates_and_removal():
    project, root, _output = build_project()
    layer_id = add_layer(project, "LO_Tree", 'UNLIT')
    wall = add_source(root, "Tree Wall", (0, 0, 0), primitive="plane")
    frame = add_source(root, "Tree Frame", (0.5, 0.0, 1.0))
    frame.parent = wall
    frame.matrix_parent_inverse = wall.matrix_world.inverted()
    wall.location = (3.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    expected = frame.matrix_world.translation.copy()
    frame_unit = add_unit(project, layer_id, "FrameUnit", [frame])
    wall_unit = add_unit(project, layer_id, "WallUnit", [wall])
    activate_state(bpy.context, 'DAY')
    # Child first: its parent's generated object does not exist yet.
    assert bake_unit(frame_unit) == "SUCCESS"
    assert bake_unit(wall_unit) == "SUCCESS"
    generated_frame = generated_for(frame)
    generated_wall = generated_for(wall)
    assert generated_frame.parent == generated_wall
    bpy.context.view_layer.update()
    assert (generated_frame.matrix_world.translation - expected).length < 1e-5
    status, path = export.export_semantic_layer(bpy.context, layer_by_id(layer_id), 'USDZ')
    assert status == 'SUCCESS', path
    frame_paths = [p for p in usd_prim_paths(path) if p.rsplit("/", 1)[-1].startswith("Tree_Frame")]
    assert frame_paths and all("/Tree_Wall" in p for p in frame_paths), frame_paths

    duplicate = generated_wall.copy()
    bpy.data.collections["PMVR_GENERATED"].objects.link(duplicate)
    try:
        export.export_semantic_layer(bpy.context, layer_by_id(layer_id), 'GLB')
    except export.PipelineExportError as exc:
        assert "several generated Beauty objects" in str(exc), exc
    else:
        raise AssertionError("duplicate generated objects must block export")
    bpy.data.objects.remove(duplicate, do_unlink=True)

    project.active_render_layer_index = next(i for i, l in enumerate(project.render_layers) if l.layer_id == layer_id)
    project.active_bake_unit_index = next(i for i, u in enumerate(project.bake_units) if u.unit_id == wall_unit)
    assert bpy.ops.pmvr.remove_bake_unit() == {'FINISHED'}
    bpy.context.view_layer.update()
    assert (generated_frame.matrix_world.translation - expected).length < 1e-5, "child moved when parent unit was removed"
    leftovers = [m.name for m in bpy.data.materials if m.get("pmvr_unit_id") == wall_unit]
    assert not leftovers, leftovers
    print("integrity: hierarchy, duplicates and unit removal OK")


def scenario_recovery_after_mid_bake_save():
    project, root, output = build_project()
    layer_id = add_layer(project, "LO_Recover", 'UNLIT')
    first = add_source(root, "Recover A", (0, 0, 0))
    second = add_source(root, "Recover B", (2, 0, 0))
    first_unit = add_unit(project, layer_id, "RecoverA", [first])
    second_unit = add_unit(project, layer_id, "RecoverB", [second])
    outside = bpy.data.objects.new("Recover Outside", None)
    bpy.context.scene.collection.objects.link(outside)
    blend = os.path.join(output, "recover.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    activate_state(bpy.context, 'DAY')
    assert bake_unit(first_unit) == "SUCCESS"  # creates PMVR_GENERATED
    runtime = bake.BeautyBakeRuntime(bpy.context, unit_by_id(second_unit))
    assert runtime.prepare() == "READY"
    assert second.hide_render and outside.hide_render
    # Ctrl+S (or an autosave) while the unit is still baking.
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    runtime.cleanup(keep_image=False)
    bpy.ops.wm.open_mainfile(filepath=blend)
    hidden = [o.name for o in bpy.data.objects if o.hide_render]
    assert not hidden, hidden
    view_layer = bpy.context.view_layer
    generated_layers = [
        lc for lc in view_layer.layer_collection.children if lc.collection.name == "PMVR_GENERATED"
    ]
    assert generated_layers and not any(lc.exclude for lc in generated_layers)
    assert not bpy.data.collections.get("PMVR_WORK")
    assert not [o.name for o in bpy.data.objects if "pmvr_bake_restore_hide_render" in o]
    assert "pmvr_bake_restore_exclude" not in bpy.data.collections["PMVR_GENERATED"]
    print("integrity: recovery after mid-bake save OK")


def select_only(objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def scenario_duplicates_get_their_own_identity():
    project, root, output = build_project()
    layer_id = add_layer(project, "LO_Dup", 'UNLIT')
    other_id = add_layer(project, "TR_Dup", 'UNLIT')
    chair = add_source(root, "Dup Chair", (0, 0, 0))
    unit_id = add_unit(project, layer_id, "DupUnit", [chair])
    chair.pm_vr_pipeline.extra_export_layers.add().layer_id = other_id
    anchor = bpy.data.objects.new("Dup Anchor", None)
    root.objects.link(anchor)
    anchor.pm_vr_pipeline.source_id = new_id()
    anchor.pm_vr_pipeline.is_registered_source = True
    anchor.pm_vr_pipeline.render_layer_id = layer_id
    anchor.pm_vr_pipeline.processing_role = 'EXPORT_ORIGINAL'
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "dup.blend"))
    activate_state(bpy.context, 'DAY')
    assert bake_unit(unit_id) == "SUCCESS"
    chair_id, anchor_id = chair.pm_vr_pipeline.source_id, anchor.pm_vr_pipeline.source_id
    generated = generated_for(chair)

    select_only([chair, anchor, generated])
    bpy.ops.object.duplicate()
    copies = list(bpy.context.selected_objects)
    bpy.context.view_layer.update()  # depsgraph update, as after Shift+D in the UI
    assert not duplicate_source_ids(), duplicate_source_ids()
    chair_copy = next(o for o in copies if o.name.startswith("Dup Chair") and not o.get("pmvr_generated") and o.type == 'MESH' and o.pm_vr_pipeline.is_registered_source)
    anchor_copy = next(o for o in copies if o.type == 'EMPTY')
    generated_copy = next(o for o in copies if o not in (chair_copy, anchor_copy))
    assert chair.pm_vr_pipeline.source_id == chair_id and chair.pm_vr_pipeline.bake_unit_id == unit_id
    assert anchor.pm_vr_pipeline.source_id == anchor_id
    meta = chair_copy.pm_vr_pipeline
    assert meta.source_id != chair_id and meta.render_layer_id == layer_id
    assert meta.processing_role == 'BAKE' and meta.bake_unit_id == "" and len(meta.extra_export_layers) == 0
    assert anchor_copy.pm_vr_pipeline.source_id != anchor_id
    assert anchor_copy.pm_vr_pipeline.render_layer_id == layer_id
    assert not [k for k in generated_copy.keys() if k.startswith("pmvr_")], generated_copy.keys()
    assert generated.get("pmvr_generated")
    errors = [i for i in validation.validate_all(bpy.context) if i.severity == 'ERROR']
    assert [(i.message, i.object_name) for i in errors] == [("Bake object has no valid bake unit", chair_copy.name)], errors
    bpy.data.objects.remove(chair_copy, do_unlink=True)
    status, path = export.export_semantic_layer(bpy.context, layer_by_id(layer_id), 'USDZ')
    assert status == 'SUCCESS', path
    exported = usd_prim_paths(path)
    assert any(p.endswith("/Dup_Anchor_001") for p in exported), exported

    # IDs that are already shared when a file is loaded are not guessed.
    anchor_copy.pm_vr_pipeline.source_id = anchor_id
    blend = os.path.join(output, "dup_saved.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    bpy.data.objects.new("Trigger Growth", None)
    bpy.context.scene.collection.objects.link(bpy.data.objects["Trigger Growth"])
    bpy.context.view_layer.update()
    assert list(duplicate_source_ids()) == [anchor_id]
    select_only([bpy.data.objects["Dup Anchor.001"]])
    assert bpy.ops.pmvr.register_duplicate_as_new() == {'FINISHED'}
    assert not duplicate_source_ids()
    assert bpy.data.objects["Dup Anchor"].pm_vr_pipeline.source_id == anchor_id
    print("integrity: duplicates get their own identity OK")


def scenario_linked_duplicates():
    import numpy as np

    project, root, output = build_project()
    layer_id = add_layer(project, "LO_Linked", 'UNLIT')
    first = add_source(root, "Linked A", (0, 0, 0), [principled("Linked Blue", (0.1, 0.2, 0.9))])
    second = first.copy()
    second.name = "Linked B"
    second.location = (3, 0, 0)
    root.objects.link(second)
    second.material_slots[0].link = 'OBJECT'
    second.material_slots[0].material = principled("Linked Red", (0.9, 0.1, 0.1))
    second.pm_vr_pipeline.source_id = ""
    unit_a = add_unit(project, layer_id, "LinkedA", [first])
    unit_b = add_unit(project, layer_id, "LinkedB", [second])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "linked.blend"))
    activate_state(bpy.context, 'DAY')
    assert bake_unit(unit_a) == "SUCCESS" and bake_unit(unit_b) == "SUCCESS"

    def mean_rgb(name):
        image = bpy.data.images.load(os.path.join(output, name), check_existing=False)
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        bpy.data.images.remove(image)
        pixels = pixels.reshape(-1, 4)[:, :3]
        return pixels[pixels.sum(axis=1) > 0.02].mean(axis=0)

    red_a, _g, blue_a = mean_rgb("LO_Linked_LinkedA_Beauty.png")
    red_b, _g, blue_b = mean_rgb("LO_Linked_LinkedB_Beauty.png")
    assert blue_a > red_a, (red_a, blue_a)
    assert red_b > blue_b, "object-linked material was ignored by the bake"
    assert generated_for(first).data != generated_for(second).data
    second.pm_vr_pipeline.bake_unit_id = unit_a
    messages = [i.message for i in validation.validate_unit(bpy.context, unit_by_id(unit_a)) if i.severity == 'ERROR']
    assert any("Shares mesh data" in m for m in messages), messages
    print("integrity: linked duplicates OK")


def main():
    PM_VR.register()
    scenario_state_isolation_across_reopen()
    scenario_pbr_slots_and_binding_restore()
    scenario_late_failure_keeps_previous_result()
    scenario_hierarchy_duplicates_and_removal()
    scenario_recovery_after_mid_bake_save()
    scenario_duplicates_get_their_own_identity()
    scenario_linked_duplicates()
    print("PM_VR_PIPELINE_INTEGRITY_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
