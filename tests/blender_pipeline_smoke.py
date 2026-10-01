"""Run with Blender --background --factory-startup --python this_file.py."""

import os
import sys
import tempfile
import zipfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, export, validation  # noqa: E402
from PM_VR.modules.pipeline.generated import prepare_materials  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


def link_child(parent, child):
    parent.children.link(child)


def bake_beauty_for_test(context, unit):
    """Drive the same runtime used by the modal queue without UI events."""
    runtime = bake.BeautyBakeRuntime(context, unit)
    try:
        status = runtime.prepare()
        if status == "SKIPPED":
            runtime.cleanup(keep_image=False)
            return status
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            result = bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs())
            assert result == {'FINISHED'}, result
        runtime.finish()
        return "SUCCESS"
    except Exception as exc:
        runtime.fail(exc)
        raise


def main():
    PM_VR.register()
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 1
    project.bake_resolution = '256'
    project.uv_padding = 0.008
    output_directory = tempfile.mkdtemp(prefix="pmvr_smoke_")
    project.beauty_output_directory = output_directory
    project.glb_output_directory = output_directory
    project.usdz_output_directory = output_directory
    project.lightmap_output_directory = output_directory

    root = bpy.data.collections.new("Smoke Source Root")
    day = bpy.data.collections.new("Smoke Day")
    evening = bpy.data.collections.new("Smoke Evening")
    scene.collection.children.link(root)
    link_child(root, day)
    link_child(root, evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Smoke Day World")
    project.evening_world = bpy.data.worlds.new("Smoke Evening World")

    bpy.ops.mesh.primitive_plane_add(size=2)
    source = bpy.context.object
    for collection in list(source.users_collection):
        collection.objects.unlink(source)
    root.objects.link(source)
    if not source.data.uv_layers:
        source.data.uv_layers.new(name="UVMap")
    source.data.uv_layers[0].name = "UVMap"
    source.data.uv_layers.new(name="SimpleBake", do_init=True)
    material = bpy.data.materials.new("Smoke Material")
    material.use_nodes = True
    source.data.materials.append(material)
    source_mesh_pointer = source.data.as_pointer()
    source_material_pointer = material.as_pointer()

    bpy.ops.mesh.primitive_plane_add(size=1, location=(0.0, 0.0, 1.0))
    source_two = bpy.context.object
    for collection in list(source_two.users_collection):
        collection.objects.unlink(source_two)
    root.objects.link(source_two)
    if not source_two.data.uv_layers:
        source_two.data.uv_layers.new(name="UVMap")
    source_two.data.uv_layers[0].name = "UVMap"
    source_two.data.uv_layers.new(name="SimpleBake", do_init=True)
    source_two.data.materials.append(material.copy())

    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = "Smoke Scene"
    layer.output_base_name = "Smoke_Scene"
    layer.layer_type = 'UNLIT'
    unit = project.bake_units.add()
    unit.unit_id = new_id()
    unit.artifact_key = unit.unit_id
    unit.display_name = "Smoke Unit"
    unit.render_layer_id = layer.layer_id
    unit.resolution = '256'
    metadata = source.pm_vr_pipeline
    metadata.source_id = new_id()
    metadata.is_registered_source = True
    metadata.render_layer_id = layer.layer_id
    metadata.processing_role = 'BAKE'
    metadata.bake_unit_id = unit.unit_id
    metadata_two = source_two.pm_vr_pipeline
    metadata_two.source_id = new_id()
    metadata_two.is_registered_source = True
    metadata_two.render_layer_id = layer.layer_id
    metadata_two.processing_role = 'BAKE'
    metadata_two.bake_unit_id = unit.unit_id

    activate_state(bpy.context, 'DAY')
    errors = [issue.message for issue in validation.validate_all(bpy.context) if issue.severity == 'ERROR']
    assert not errors, errors
    assert bake_beauty_for_test(bpy.context, unit) == 'SUCCESS'
    assert unit.day_status == "Ready"
    assert bpy.data.images.get(unit.day_beauty_image)
    generated = [obj for obj in bpy.data.objects if obj.get("pmvr_generated")]
    assert len(generated) == 2
    assert all(obj.get("pmvr_layer_type") == 'UNLIT' for obj in generated)
    assert all(obj.data.materials[0].get("pmvr_state") == 'DAY' for obj in generated)
    assert len({obj.data.materials[0].as_pointer() for obj in generated}) == 1
    activate_state(bpy.context, 'EVENING')
    assert bake_beauty_for_test(bpy.context, unit) == 'SUCCESS'
    assert unit.evening_status == "Ready"
    generated_after_evening = [obj for obj in bpy.data.objects if obj.get("pmvr_generated")]
    assert generated_after_evening == generated
    assert all(obj.data.materials[0].get("pmvr_state") == 'EVENING' for obj in generated)
    assert source.data.as_pointer() == source_mesh_pointer
    assert source.material_slots[0].material.as_pointer() == source_material_pointer
    status, filepath = export.export_semantic_layer(bpy.context, layer, 'GLB')
    assert status == 'SUCCESS', filepath
    assert filepath.endswith("Smoke Scene_Evening.glb") and os.path.isfile(filepath)
    status, filepath = export.export_semantic_layer(bpy.context, layer, 'USDZ')
    assert status == 'SUCCESS', filepath
    assert filepath.endswith("Smoke Scene_Evening.usdz") and os.path.isfile(filepath)
    with zipfile.ZipFile(filepath) as package:
        assert any(name.lower().endswith('.png') for name in package.namelist())

    test_image = bpy.data.images.new("Smoke Processor Image", width=8, height=8)
    second_material = material.copy()
    source.data.materials.append(second_material)
    layer.layer_type = 'PBR'
    staged, created = prepare_materials(unit, layer, [source], 'DAY', test_image)
    assert len(staged[metadata.source_id]) == 2
    assert all(mat.get("pmvr_state") == 'DAY' for mat in created)
    for generated_material in created:
        bpy.data.materials.remove(generated_material)
    layer.layer_type = 'ALPHA'
    for source_material in source.data.materials:
        principled = next(node for node in source_material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
        principled.inputs["Alpha"].default_value = 0.5
    staged, created = prepare_materials(unit, layer, [source], 'DAY', test_image)
    assert len(staged[metadata.source_id]) == 2
    for generated_material in created:
        bpy.data.materials.remove(generated_material)
    bpy.data.images.remove(test_image)
    layer.layer_type = 'UNLIT'
    status, message = bake.bake_lightmap_unit(bpy.context, unit)
    assert status == 'SUCCESS', message
    assert unit.evening_lightmap_status == "Ready"
    lightmap_generated = [
        obj for obj in bpy.data.objects
        if obj.get("pmvr_generated") and obj.get("pmvr_mode") == 'LIGHTMAP'
    ]
    assert len(lightmap_generated) == 2
    assert bpy.data.images.get(unit.evening_lightmap_image)
    print("PMVR_PIPELINE_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
