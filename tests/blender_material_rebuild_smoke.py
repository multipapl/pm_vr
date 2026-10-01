"""Smoke-test rebuilding selected baked objects using hidden originals."""

from pathlib import Path
import sys

import bpy


ADDONS_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDONS_DIRECTORY))

import PM_VR  # noqa: E402
from PM_VR.modules import material_rebuild  # noqa: E402


def make_image(name, color):
    image = bpy.data.images.new(name, width=4, height=4)
    image.generated_color = color
    return image


def make_original():
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    obj = bpy.context.object
    obj.name = "4K_LeafIvy01"
    obj.data.name = obj.name
    primary = obj.data.uv_layers.active
    primary.name = "UVMap"
    simple_bake = obj.data.uv_layers.new(name="SimpleBake")
    coordinates = [0.0] * (len(primary.data) * 2)
    primary.data.foreach_get("uv", coordinates)
    simple_bake.data.foreach_set("uv", coordinates)

    material = bpy.data.materials.new("LeafIvy01")
    material.use_nodes = True
    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    base = tree.nodes.new("ShaderNodeTexImage")
    base.name = "Original Base"
    base.image = make_image("Original Base", (0.1, 0.8, 0.2, 0.5))
    roughness = tree.nodes.new("ShaderNodeTexImage")
    roughness.name = "Original Roughness"
    roughness.image = make_image("Original Roughness", (0.4, 0.4, 0.4, 1.0))
    normal = tree.nodes.new("ShaderNodeTexImage")
    normal.name = "Original Normal"
    normal.image = make_image("Original Normal", (0.5, 0.5, 1.0, 1.0))
    normal_map = tree.nodes.new("ShaderNodeNormalMap")
    floating_mix = tree.nodes.new("ShaderNodeMix")
    floating_mix.name = "Floating Mix Color"
    tree.links.new(base.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(base.outputs["Alpha"], principled.inputs["Alpha"])
    tree.links.new(roughness.outputs["Color"], principled.inputs["Roughness"])
    tree.links.new(normal.outputs["Color"], normal_map.inputs["Color"])
    tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    obj.data.materials.append(material)
    return obj


def make_baked(original, baked_image):
    obj = original.copy()
    obj.name = f"{original.name}_Baked"
    obj.data = original.data.copy()
    obj.data.name = obj.name
    bpy.context.scene.collection.objects.link(obj)
    uv_map = obj.data.uv_layers.get("UVMap")
    obj.data.uv_layers.remove(uv_map)

    material = bpy.data.materials.new(f"{obj.name}_Material")
    material.use_nodes = True
    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = baked_image
    tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return obj, texture


def select_baked(baked):
    bpy.ops.object.select_all(action='DESELECT')
    baked.select_set(True)
    bpy.context.view_layer.objects.active = baked


def uv_signature(mesh):
    signature = []
    for layer in mesh.uv_layers:
        coordinates = [0.0] * (len(layer.data) * 2)
        layer.data.foreach_get("uv", coordinates)
        signature.append((layer.name, tuple(coordinates)))
    return tuple(signature)


def material_signature(material):
    tree = material.node_tree
    nodes = tuple(sorted(
        (
            node.name,
            node.bl_idname,
            node.image.name if node.type == 'TEX_IMAGE' and node.image else None,
        )
        for node in tree.nodes
    ))
    links = tuple(sorted(
        (
            link.from_node.name,
            link.from_socket.name,
            link.to_node.name,
            link.to_socket.name,
        )
        for link in tree.links
    ))
    return nodes, links


def original_signature(obj):
    return (
        obj.name,
        obj.data.as_pointer(),
        obj.data.materials[0].as_pointer(),
        tuple(value for row in obj.matrix_world for value in row),
        tuple(sorted(collection.name for collection in obj.users_collection)),
        uv_signature(obj.data),
        material_signature(obj.data.materials[0]),
    )


def assert_general_base_color_is_pruned():
    material = bpy.data.materials.new("General Reflect")
    material.use_nodes = True
    tree = material.node_tree
    principled = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    base = tree.nodes.new("ShaderNodeTexImage")
    base.name = "Discarded Base"
    base.image = make_image("Discarded Base", (0.2, 0.2, 0.2, 1.0))
    roughness = tree.nodes.new("ShaderNodeTexImage")
    roughness.name = "Preserved Roughness"
    roughness.image = make_image("Preserved Roughness", (0.5, 0.5, 0.5, 1.0))
    mix = tree.nodes.new("ShaderNodeMixRGB")
    mix.name = "Discarded Base Mix"
    tree.links.new(base.outputs["Color"], mix.inputs[1])
    tree.links.new(mix.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(roughness.outputs["Color"], principled.inputs["Roughness"])

    baked_image = make_image("General Bake", (0.7, 0.7, 0.7, 1.0))
    rebuilt = material_rebuild.configure_material(
        material,
        baked_image,
        "General Reflect_M",
    )
    rebuilt_tree = rebuilt.node_tree
    assert rebuilt_tree.nodes.get("Discarded Base") is None
    assert rebuilt_tree.nodes.get("Discarded Base Mix") is None
    assert rebuilt_tree.nodes.get("Preserved Roughness") is not None
    assert rebuilt_tree.nodes[material_rebuild.NODE_BAKED_IMAGE].image == baked_image


def assert_output(output, baked_image):
    assert output.name == "4K_LeafIvy01_M"
    assert output.data.name == output.name
    assert output.data.materials[0].name == output.name
    assert [layer.name for layer in output.data.uv_layers] == ["UVMap", "SimpleBake"]
    assert output.name in bpy.context.scene.collection.objects

    material = output.data.materials[0]
    tree = material.node_tree
    baked_node = tree.nodes[material_rebuild.NODE_BAKED_IMAGE]
    assert baked_node.image == baked_image
    assert baked_node.inputs["Vector"].links[0].from_node.uv_map == "SimpleBake"
    principled = next(node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED')
    assert principled.inputs["Base Color"].links[0].from_node == baked_node
    assert principled.inputs["Alpha"].is_linked

    for node in tree.nodes:
        if node.type == 'TEX_IMAGE' and node != baked_node:
            assert node.inputs["Vector"].links[0].from_node.uv_map == "UVMap"
    original_images = {
        node.image.name
        for node in tree.nodes
        if node.type == 'TEX_IMAGE' and node != baked_node
    }
    assert original_images == {"Original Base"}
    assert tree.nodes.get("Original Roughness") is None
    assert tree.nodes.get("Original Normal") is None
    assert tree.nodes.get("Floating Mix Color") is None
    assert not any(node.type == 'NORMAL_MAP' for node in tree.nodes)


def main():
    PM_VR.register()
    try:
        assert_general_base_color_is_pruned()
        original = make_original()
        first_bake = make_image("First Bake", (0.3, 0.3, 0.3, 1.0))
        baked, baked_texture = make_baked(original, first_bake)
        hidden_collection = bpy.data.collections.new("Hidden Originals")
        bpy.context.scene.collection.children.link(hidden_collection)
        for collection in list(original.users_collection):
            collection.objects.unlink(original)
        hidden_collection.objects.link(original)
        hidden_collection.hide_viewport = True
        hidden_collection.hide_render = True

        select_baked(baked)
        original_before = original_signature(original)
        selected_before = set(bpy.context.selected_objects)
        active_before = bpy.context.view_layer.objects.active
        assert bpy.ops.pm_vr.rebuild_baked_materials() == {'FINISHED'}
        assert_output(bpy.data.objects["4K_LeafIvy01_M"], first_bake)
        assert original_signature(original) == original_before
        assert set(bpy.context.selected_objects) == selected_before
        assert bpy.context.view_layer.objects.active == active_before

        second_bake = make_image("Second Bake", (0.8, 0.8, 0.8, 1.0))
        baked_texture.image = second_bake
        select_baked(baked)
        assert bpy.ops.pm_vr.rebuild_baked_materials() == {'FINISHED'}
        output = bpy.data.objects["4K_LeafIvy01_M"]
        assert_output(output, second_bake)
        assert original_signature(original) == original_before
        assert set(bpy.context.selected_objects) == selected_before
        assert bpy.context.view_layer.objects.active == active_before
        assert not any(obj.name.startswith("4K_LeafIvy01_M.") for obj in bpy.data.objects)
        print("PM_VR_MATERIAL_REBUILD_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
