"""Run with Blender --factory-startup --background --python this_file.py."""

from pathlib import Path
import importlib
import os
import sys
import tempfile

import bpy
import OpenImageIO as oiio


ADDONS_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDONS_DIRECTORY))

import PM_VR  # noqa: E402

runner = importlib.import_module("PM_VR.modules.lightmap_baker.runner")
constants = importlib.import_module(
    "PM_VR.modules.lightmap_baker.constants"
)
NODE_IMAGE_NAME = constants.NODE_IMAGE_NAME
NODE_MIX_NAME = constants.NODE_MIX_NAME
NODE_UV_NAME = constants.NODE_UV_NAME
TAG_GENERATED = constants.TAG_GENERATED


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _make_receiver():
    bpy.ops.mesh.primitive_plane_add(size=4.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.object
    obj.name = "Receiver"
    obj.data.name = "Receiver"

    primary = obj.data.uv_layers.active
    primary.name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake")
    for source_loop, target_loop in zip(primary.data, bake_uv.data):
        target_loop.uv = source_loop.uv
    obj.data.uv_layers.active = primary

    material = bpy.data.materials.new("Receiver")
    material.use_nodes = True
    tree = material.node_tree
    principled = next(
        node
        for node in tree.nodes
        if node.type == 'BSDF_PRINCIPLED'
    )
    principled.inputs["Metallic"].default_value = 1.0
    color = tree.nodes.new("ShaderNodeRGB")
    color.outputs["Color"].default_value = (0.8, 0.2, 0.1, 1.0)
    tree.links.new(color.outputs["Color"], principled.inputs["Base Color"])
    obj.data.materials.append(material)
    obj.modifiers.new(name="Bevel", type='BEVEL')
    return obj


def _make_light():
    data = bpy.data.lights.new("Key", type='AREA')
    data.energy = 800.0
    data.color = (0.3, 0.6, 1.0)
    data.shape = 'DISK'
    data.size = 3.0
    obj = bpy.data.objects.new("Key", data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (0.0, 0.0, 3.0)
    return obj


def _add_queue_item(settings, obj, use_override=False):
    item = settings.objects.add()
    item.source_object = obj
    item.source_name = obj.name
    item.use_resolution_override = use_override
    item.resolution = '256'
    return item


def _make_test_mesh(name, location, add_bake_uv=True):
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name
    primary = obj.data.uv_layers.active
    primary.name = "UVMap"
    if add_bake_uv:
        bake_uv = obj.data.uv_layers.new(name="SimpleBake")
        for source_loop, target_loop in zip(primary.data, bake_uv.data):
            target_loop.uv = source_loop.uv
        obj.data.uv_layers.active = primary
    return obj


def _make_basic_material(name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    return material


def _make_unsupported_source():
    obj = _make_test_mesh("Unsupported", (6.0, 0.0, 0.0))
    material = _make_basic_material("Unsupported")
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    mix = tree.nodes.new("ShaderNodeMixShader")
    first = tree.nodes.new("ShaderNodeBsdfPrincipled")
    second = tree.nodes.new("ShaderNodeBsdfPrincipled")
    tree.links.new(first.outputs["BSDF"], mix.inputs[1])
    tree.links.new(second.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    obj.data.materials.append(material)
    return obj


def _mix_socket(node, name):
    return next(
        socket
        for socket in node.inputs
        if socket.name == name and not socket.is_unavailable
    )


def _assert_result(source, previous=None, image_source='GENERATED'):
    result = bpy.data.objects.get(f"{source.name}_LM")
    assert result is not None
    assert result is not previous
    assert result.get(TAG_GENERATED, False)
    assert [layer.name for layer in result.data.uv_layers] == [
        "UVMap",
        "SimpleBake",
    ]
    assert [modifier.name for modifier in result.modifiers] == ["Bevel"]

    material = result.material_slots[0].material
    nodes = material.node_tree.nodes
    assert nodes.get(NODE_UV_NAME)
    assert nodes.get(NODE_IMAGE_NAME)
    assert nodes.get(NODE_MIX_NAME)
    image = nodes[NODE_IMAGE_NAME].image
    assert image.source == image_source
    assert tuple(image.size) == (256, 256)
    assert image.packed_file is None
    assert image.colorspace_settings.name in {
        "Linear Rec.709",
        "Linear",
        "scene_linear",
        "Non-Color",
    }
    assert max(image.pixels[:4096]) > 0.0
    pixel_values = image.pixels[:]
    red = sum(pixel_values[0::4])
    blue = sum(pixel_values[2::4])
    assert blue > red

    mix = nodes[NODE_MIX_NAME]
    assert _mix_socket(mix, "Factor").default_value == 1.0
    assert _mix_socket(mix, "A").is_linked
    assert _mix_socket(mix, "A").links[0].from_node.type == 'RGB'
    assert nodes[NODE_UV_NAME].uv_map == "SimpleBake"
    assert source.hide_render and source.hide_viewport
    assert not result.hide_render and not result.hide_viewport
    return result


def _assert_context_restored(scene, active_object, bake_state):
    assert scene.render.engine == 'BLENDER_EEVEE'
    assert bpy.context.view_layer.objects.active is active_object
    assert active_object.select_get()
    bake = scene.render.bake
    assert (
        bake.margin,
        bake.use_pass_direct,
        bake.use_pass_indirect,
        bake.use_pass_color,
    ) == bake_state


def _test_receiver_bake(scene, output_directory):
    source = _make_receiver()
    source_mesh = source.data
    source_material = source.material_slots[0].material
    source_node_count = len(source_material.node_tree.nodes)
    light = _make_light()

    settings = scene.pm_lightmap_settings
    settings.resolution = '256'
    settings.margin = 4
    settings.export_to_disk = False
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    assert bpy.ops.pm.lightmap_add_selected() == {'FINISHED'}

    source.select_set(False)
    light.select_set(True)
    bpy.context.view_layer.objects.active = light

    bake = scene.render.bake
    bake.margin = 7
    bake.use_pass_direct = False
    bake.use_pass_indirect = True
    bake.use_pass_color = True
    bake_state = (
        bake.margin,
        bake.use_pass_direct,
        bake.use_pass_indirect,
        bake.use_pass_color,
    )

    first_summary = runner.run_batch(bpy.context)
    assert first_summary.failed == 0
    assert first_summary.skipped == 0
    assert first_summary.successful == 1
    first_result = _assert_result(source)
    _assert_context_restored(scene, light, bake_state)
    assert source.data is source_mesh
    assert source.material_slots[0].material is source_material
    assert len(source_material.node_tree.nodes) == source_node_count
    assert source.data.uv_layers.active.name == "UVMap"

    settings.export_to_disk = True
    settings.output_directory = output_directory
    second_summary = runner.run_batch(bpy.context)
    assert second_summary.failed == 0
    assert second_summary.skipped == 0
    assert second_summary.successful == 1
    second_result = _assert_result(
        source,
        previous=first_result,
        image_source='FILE',
    )
    _assert_context_restored(scene, light, bake_state)

    filepath = os.path.join(output_directory, "Receiver_LM.exr")
    assert os.path.isfile(filepath)
    exr = oiio.ImageInput.open(filepath)
    assert exr is not None
    try:
        spec = exr.spec()
        assert (spec.width, spec.height, spec.nchannels) == (256, 256, 3)
        assert spec.format.basetype == oiio.BASETYPE.FLOAT
        assert spec.get_string_attribute("compression").lower() == "piz"
    finally:
        exr.close()
    stats = oiio.ImageBufAlgo.computePixelStats(oiio.ImageBuf(filepath))
    assert max(stats.max[:3]) > 1.0

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    second_result.select_set(True)
    bpy.context.view_layer.objects.active = second_result

    third_summary = runner.run_batch(bpy.context)
    assert third_summary.failed == 0
    assert third_summary.skipped == 0
    assert third_summary.successful == 1
    final_result = _assert_result(
        source,
        previous=second_result,
        image_source='FILE',
    )
    _assert_context_restored(scene, final_result, bake_state)
    return source, final_result, light


def _test_skip_and_fallback(scene, generated_result, light):
    settings = scene.pm_lightmap_settings
    settings.objects.clear()
    settings.resolution = '512'
    settings.export_to_disk = False

    unsupported = _make_unsupported_source()
    missing_uv = _make_test_mesh("MissingUV", (9.0, 0.0, 0.0), False)
    missing_uv.data.materials.append(_make_basic_material("MissingUV"))
    multiple = _make_test_mesh("MultipleMaterials", (12.0, 0.0, 0.0))
    multiple.data.materials.append(_make_basic_material("MultiA"))
    multiple.data.materials.append(_make_basic_material("MultiB"))
    collision = _make_test_mesh("Collision", (15.0, 0.0, 0.0))
    collision.data.materials.append(_make_basic_material("Collision"))
    blocker_mesh = bpy.data.meshes.new("Collision_LM")
    blocker = bpy.data.objects.new("Collision_LM", blocker_mesh)
    scene.collection.objects.link(blocker)

    _add_queue_item(settings, unsupported, use_override=True)
    _add_queue_item(settings, missing_uv)
    _add_queue_item(settings, multiple)
    _add_queue_item(settings, collision)
    _add_queue_item(settings, generated_result)

    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    light.hide_viewport = False
    light.hide_set(False)
    light.select_set(True)
    bpy.context.view_layer.objects.active = light

    summary = runner.run_batch(bpy.context)
    assert summary.failed == 0
    assert summary.skipped == 4
    assert summary.successful == 0
    assert summary.warnings == 1

    result = bpy.data.objects.get("Unsupported_LM")
    material = result.material_slots[0].material
    nodes = material.node_tree.nodes
    assert tuple(nodes[NODE_IMAGE_NAME].image.size) == (256, 256)
    assert nodes.get(NODE_UV_NAME)
    assert nodes.get(NODE_IMAGE_NAME)
    assert not nodes.get(NODE_MIX_NAME)
    assert not nodes[NODE_IMAGE_NAME].outputs["Color"].is_linked
    assert unsupported.hide_render and unsupported.hide_viewport
    for skipped in (missing_uv, multiple, collision):
        assert not skipped.hide_render and not skipped.hide_viewport
    assert bpy.data.objects.get("Collision_LM") is blocker


def _assert_no_temporary_resources():
    collections = (
        bpy.data.objects,
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.images,
        bpy.data.scenes,
        bpy.data.node_groups,
        bpy.data.cameras,
    )
    leftovers = [
        item.name
        for collection in collections
        for item in collection
        if item.name.startswith("__PM_LM_")
    ]
    assert leftovers == []


def main():
    PM_VR.register()
    try:
        _clear_scene()
        scene = bpy.context.scene
        scene.cycles.samples = 4
        scene.cycles.use_denoising = False
        scene.world.color = (0.05, 0.05, 0.05)
        scene.render.engine = 'BLENDER_EEVEE'

        with tempfile.TemporaryDirectory(prefix="pm_lightmap_export_") as folder:
            source, result, light = _test_receiver_bake(scene, folder)
            _test_skip_and_fallback(scene, result, light)
            assert source.hide_render and source.hide_viewport
            _assert_no_temporary_resources()
        print("PM_LIGHTMAP_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
