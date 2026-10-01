"""Alpha layer: opacity is found in mixed and grouped foliage shaders.

Run with Blender --background --factory-startup --python this_file.py.

A leaf built like production foliage (a node group holding Principled and
Translucent, mixed with a Transparent BSDF whose factor comes from an Opacity
group input) bakes in an Alpha layer. The generated material is one
Principled with the baked colour and the source's opacity texture on UVMap,
and the GLB marks it as alpha. Also: Transparent on the other Mix input
(inverted factor), a plain Principled Alpha, a material without transparency
(clear validation error), and the messages for render-disabled unit members
and faces using a missing material slot.
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
from PM_VR.modules.pipeline import bake, validation  # noqa: E402
from PM_VR.modules.pipeline.bake_scene import PipelineBakeError, find_alpha_source  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402

PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def opacity_image(output):
    image = bpy.data.images.new("LeafOpacity", 64, 64)
    pixels = [0.0] * (64 * 64 * 4)
    for y in range(64):
        for x in range(64):
            value = 1.0 if (x - 32) ** 2 + (y - 32) ** 2 < 24 ** 2 else 0.0
            index = (y * 64 + x) * 4
            pixels[index:index + 4] = (value, value, value, 1.0)
    image.pixels = pixels
    image.filepath_raw = os.path.join(output, "leaf_opacity.png")
    image.file_format = 'PNG'
    image.save()
    return image


def leaf_group():
    group = bpy.data.node_groups.new("LeafShaderTest", "ShaderNodeTree")
    group.interface.new_socket("Base Color", in_out='INPUT', socket_type='NodeSocketColor')
    group.interface.new_socket("Opacity", in_out='INPUT', socket_type='NodeSocketFloat')
    group.interface.new_socket("Shader", in_out='OUTPUT', socket_type='NodeSocketShader')
    nodes, links = group.nodes, group.links
    group_in = nodes.new("NodeGroupInput")
    group_out = nodes.new("NodeGroupOutput")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    translucent = nodes.new("ShaderNodeBsdfTranslucent")
    add = nodes.new("ShaderNodeAddShader")
    leaf_mix = nodes.new("ShaderNodeMixShader")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    cutout = nodes.new("ShaderNodeMixShader")
    reroute = nodes.new("NodeReroute")
    links.new(group_in.outputs["Base Color"], principled.inputs["Base Color"])
    links.new(group_in.outputs["Base Color"], translucent.inputs["Color"])
    links.new(translucent.outputs[0], add.inputs[0])
    links.new(principled.outputs[0], add.inputs[1])
    links.new(principled.outputs[0], leaf_mix.inputs[1])
    links.new(add.outputs[0], leaf_mix.inputs[2])
    leaf_mix.inputs[0].default_value = 0.3
    links.new(group_in.outputs["Opacity"], reroute.inputs[0])
    links.new(reroute.outputs[0], cutout.inputs[0])
    links.new(transparent.outputs[0], cutout.inputs[1])
    links.new(leaf_mix.outputs[0], cutout.inputs[2])
    links.new(cutout.outputs[0], group_out.inputs["Shader"])
    return group


def leaf_material(name, opacity, group):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        if node.type != 'OUTPUT_MATERIAL':
            tree.nodes.remove(node)
    output = next(node for node in tree.nodes if node.type == 'OUTPUT_MATERIAL')
    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    color = tree.nodes.new("ShaderNodeRGB")
    color.outputs[0].default_value = (0.1, 0.6, 0.1, 1.0)
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.name = "Opacity Texture"
    texture.image = opacity
    shader = tree.nodes.new("ShaderNodeGroup")
    shader.node_tree = group
    tree.links.new(uv.outputs["UV"], texture.inputs["Vector"])
    tree.links.new(color.outputs[0], shader.inputs["Base Color"])
    tree.links.new(texture.outputs["Color"], shader.inputs["Opacity"])
    tree.links.new(shader.outputs["Shader"], output.inputs["Surface"])
    return material


def add_plane(root, name, material, x):
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(x, 0, 0))
    plane = bpy.context.object
    plane.name = name
    for owner in list(plane.users_collection):
        owner.objects.unlink(plane)
    root.objects.link(plane)
    plane.data.uv_layers[0].name = "UVMap"
    bake_uv = plane.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    plane.data.materials.append(material)
    return plane


def glb_materials(path):
    with open(path, "rb") as handle:
        data = handle.read()
    length, _chunk_type = struct.unpack_from("<II", data, 12)
    return json.loads(data[20:20 + length].decode("utf-8")).get("materials", [])


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    output = tempfile.mkdtemp(prefix="pmvr_alpha_")
    for attribute in ("beauty_output_directory", "usdz_output_directory", "glb_output_directory"):
        setattr(project, attribute, output + os.sep)
    root = bpy.data.collections.new("AlphaRoot")
    scene.collection.children.link(root)
    day, evening = bpy.data.collections.new("AlphaDay"), bpy.data.collections.new("AlphaEvening")
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection, project.evening_lighting_collection = day, evening
    project.day_world, project.evening_world = bpy.data.worlds.new("AD"), bpy.data.worlds.new("AE")
    day.objects.link(bpy.data.objects.new("AlphaSun", bpy.data.lights.new("AlphaSun", 'SUN')))
    bpy.ops.pmvr.initialize_project()
    project.cycles_samples = 2
    project.bake_resolution = '256'
    project.uv_padding = 0.008
    opacity = opacity_image(output)
    group = leaf_group()

    # The production structure: grouped, Transparent on the first Mix input.
    leaf = leaf_material("LeafTest", opacity, group)
    kind, socket, invert = find_alpha_source(leaf)
    check(kind == 'SOCKET' and socket.node.name == "Opacity Texture" and not invert, f"leaf source {kind} {socket} {invert}")

    # Transparent on the second input: the factor is inverted.
    flipped = leaf_material("LeafFlipped", opacity, group.copy())
    cutout = next(n for n in flipped.node_tree.nodes if n.type == 'GROUP').node_tree
    mix = next(n for n in cutout.nodes if n.type == 'MIX_SHADER' and any(
        link.from_node.type == 'BSDF_TRANSPARENT' for link in n.inputs[1].links))
    transparent_link, leaf_link = mix.inputs[1].links[0], mix.inputs[2].links[0]
    transparent_node, leaf_socket = transparent_link.from_node, leaf_link.from_socket
    cutout.links.remove(transparent_link)
    cutout.links.remove(leaf_link)
    cutout.links.new(leaf_socket, mix.inputs[1])
    cutout.links.new(transparent_node.outputs[0], mix.inputs[2])
    check(find_alpha_source(flipped)[2] is True, "Transparent on the second input must invert")

    # Plain Principled Alpha still works; an opaque material is refused.
    plain = bpy.data.materials.new("PlainAlpha")
    plain.use_nodes = True
    principled = next(n for n in plain.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    texture = plain.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = opacity
    plain.node_tree.links.new(texture.outputs["Color"], principled.inputs["Alpha"])
    check(find_alpha_source(plain)[1] == texture.outputs["Color"], "plain Principled Alpha not found")
    # One Principled reached twice (Mix and Add with Translucent), as in the
    # production tree leaves: a single source, not "several".
    twice = plain.copy()
    tree = twice.node_tree
    shared = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    translucent = tree.nodes.new("ShaderNodeBsdfTranslucent")
    add = tree.nodes.new("ShaderNodeAddShader")
    mix = tree.nodes.new("ShaderNodeMixShader")
    material_output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    tree.links.new(shared.outputs[0], add.inputs[0])
    tree.links.new(translucent.outputs[0], add.inputs[1])
    tree.links.new(shared.outputs[0], mix.inputs[1])
    tree.links.new(add.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], material_output.inputs["Surface"])
    source = find_alpha_source(twice)
    check(source[0] == 'SOCKET' and source[1].node.type == 'TEX_IMAGE', f"shared Principled: {source}")
    opaque = bpy.data.materials.new("Opaque")
    opaque.use_nodes = True
    try:
        find_alpha_source(opaque)
        PROBLEMS.append("opaque material accepted as Alpha")
    except PipelineBakeError as exc:
        check("no transparency" in str(exc), str(exc))

    # A real bake of the grouped leaf in an Alpha layer.
    layer_index = next(i for i, layer in enumerate(project.render_layers) if layer.layer_type == 'ALPHA')
    project.active_render_layer_index = layer_index
    alpha_layer = project.render_layers[layer_index]
    alpha_layer.export_glb = True
    for layer in project.render_layers:
        layer.enabled = layer == alpha_layer
    plane = add_plane(root, "Leaf", leaf, 0)
    flipped_plane = add_plane(root, "LeafFlip", flipped, 2)
    bpy.ops.object.select_all(action='DESELECT')
    plane.select_set(True)
    flipped_plane.select_set(True)
    bpy.context.view_layer.objects.active = plane
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    errors = [i.message for i in validation.validate_all(bpy.context) if i.severity == 'ERROR']
    check(not [m for m in errors if "Alpha" in m or "Principled" in m], f"validation: {errors}")
    activate_state(bpy.context, 'DAY')
    for unit in list(project.bake_units):
        unit.resolution = '256'
        runtime = bake.BeautyBakeRuntime(bpy.context, unit)
        assert runtime.prepare() == "READY"
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()
        check(unit.day_status == "Ready", f"{unit.display_name}: {unit.day_status}")

    for source_name, expect_flip in (("Leaf", False), ("LeafFlip", True)):
        source_id = bpy.data.objects[source_name].pm_vr_pipeline.source_id
        generated = next(
            o for o in bpy.data.objects
            if o.get("pmvr_generated") and o.get("pmvr_source_id") == source_id
        )
        tree = generated.material_slots[0].material.node_tree
        kinds = sorted(n.type for n in tree.nodes)
        check("GROUP" not in kinds and "MIX_SHADER" not in kinds, f"{source_name}: shader graph kept {kinds}")
        principled = [n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED']
        check(len(principled) == 1, f"{source_name}: {len(principled)} Principled")
        alpha_from = principled[0].inputs["Alpha"].links[0].from_node if principled[0].inputs["Alpha"].links else None
        if expect_flip:
            check(alpha_from is not None and alpha_from.type == 'MATH', f"{source_name}: alpha not inverted")
            alpha_from = alpha_from.inputs[1].links[0].from_node if alpha_from else None
        check(alpha_from is not None and alpha_from.type == 'TEX_IMAGE' and alpha_from.image == opacity,
              f"{source_name}: alpha not from the opacity texture")
        vector = alpha_from.inputs["Vector"].links[0].from_node if alpha_from else None
        check(vector is not None and vector.uv_map == "UVMap", f"{source_name}: opacity not on UVMap")
        base = principled[0].inputs["Base Color"].links[0].from_node
        check(base.image and "Beauty" in base.image.name, f"{source_name}: base colour not baked")

    bpy.ops.pmvr.export_semantic_layers(export_format='GLB')
    glb = os.path.join(output, f"{alpha_layer.display_name}.glb")
    check(os.path.exists(glb), f"GLB missing: {project.last_operation_summary}")
    if os.path.exists(glb):
        modes = [m.get("alphaMode") for m in glb_materials(glb)]
        check(modes and all(mode in ("BLEND", "MASK") for mode in modes), f"GLB alpha modes {modes}")

    # PBR: the old Base Color texture is not left behind in the baked material.
    pbr_layer = next(layer for layer in project.render_layers if layer.layer_type == 'PBR')
    pbr_layer.enabled = True
    pbr_material = bpy.data.materials.new("PbrWood")
    pbr_material.use_nodes = True
    tree = pbr_material.node_tree
    principled = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    base_texture = tree.nodes.new("ShaderNodeTexImage")
    base_texture.name = "Wood Colour"
    base_texture.image = opacity
    rough_texture = tree.nodes.new("ShaderNodeTexImage")
    rough_texture.name = "Wood Roughness"
    rough_texture.image = opacity
    tree.links.new(base_texture.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(rough_texture.outputs["Color"], principled.inputs["Roughness"])
    pbr_plane = add_plane(root, "PbrPlane", pbr_material, 4)
    project.active_render_layer_index = list(project.render_layers).index(pbr_layer)
    bpy.ops.object.select_all(action='DESELECT')
    pbr_plane.select_set(True)
    bpy.context.view_layer.objects.active = pbr_plane
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    pbr_unit = project.bake_units[-1]
    pbr_unit.resolution = '256'
    runtime = bake.BeautyBakeRuntime(bpy.context, pbr_unit)
    assert runtime.prepare() == "READY"
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    runtime.finish()
    source_id = pbr_plane.pm_vr_pipeline.source_id
    generated = next(o for o in bpy.data.objects if o.get("pmvr_generated") and o.get("pmvr_source_id") == source_id)
    textures = sorted(n.name for n in generated.material_slots[0].material.node_tree.nodes if n.type == 'TEX_IMAGE')
    check(textures == ["PMVR Baked Beauty", "Wood Roughness"], f"PBR material textures {textures}")

    # Clear messages for the two production data problems.
    unit = project.bake_units[0]
    bpy.data.objects["LeafFlip"].pm_vr_pipeline.bake_unit_id = unit.unit_id
    bpy.data.objects["LeafFlip"].hide_render = True
    messages = [i.message for i in validation.validate_unit(bpy.context, unit) if i.severity == 'ERROR']
    check(any('"LeafFlip" (render disabled' in m for m in messages), f"partial message: {messages}")
    bpy.data.objects["LeafFlip"].hide_render = False
    mesh = bpy.data.objects["Leaf"].data
    mesh.polygons[0].material_index = 19464
    messages = [i.message for i in validation.validate_unit(bpy.context, unit) if i.severity == 'ERROR']
    check(any("1 face(s) use a material slot the object does not have" in m for m in messages), f"slot message: {messages}")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_ALPHA_SOURCES_SMOKE_FAILED" if PROBLEMS else "PM_VR_ALPHA_SOURCES_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
