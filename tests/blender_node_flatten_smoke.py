"""Flatten to Texture: selected colour corrections become one exact image.

Run with Blender --background --factory-startup --python this_file.py.

A material tiles two textures 4x through Mapping: a colour texture through
Color Ramp and Hue/Saturation into Base Color, a 32 px data texture through
Math (power) into Roughness. Flattening the textures and corrections gives
two images at the largest texture size, still driven by the Mapping, and the
material bakes to the same Base Color and Roughness as before (within 8-bit
rounding). Selections that a texture cannot hold are refused with a reason.
"""

import os
import sys
import tempfile

import bpy
import numpy as np


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules import node_flatten  # noqa: E402

PROBLEMS = []
BAKE = 256


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def srgb(values):
    values = np.clip(values, 0.0, None)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * values ** (1 / 2.4) - 0.055)


def texture(folder, name, size, rgb, colour):
    image = bpy.data.images.new(name, size, size, alpha=False)
    pixels = np.ones((size, size, 4), dtype=np.float32)
    pixels[..., :3] = rgb
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw = os.path.join(folder, f"{name}.png")
    image.file_format = 'PNG'
    image.save()
    image.source = 'FILE'
    image.reload()
    image.colorspace_settings.name = 'sRGB' if colour else 'Non-Color'
    return image


def build_material(folder):
    rng = np.random.default_rng(3)
    base = texture(folder, "base", 64, rng.random((64, 64, 3)), True)
    ramp = np.linspace(0, 1, 32)
    rough = texture(folder, "rough", 32, np.repeat(np.add.outer(ramp, ramp)[..., None] / 2, 3, axis=2), False)
    material = bpy.data.materials.new("Wood")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (4, 4, 1)
    links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    base_node, rough_node = nodes.new("ShaderNodeTexImage"), nodes.new("ShaderNodeTexImage")
    base_node.image, rough_node.image = base, rough
    for node in (base_node, rough_node):
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
    color_ramp = nodes.new("ShaderNodeValToRGB")
    color_ramp.color_ramp.elements[0].color = (0.1, 0.05, 0.02, 1)
    color_ramp.color_ramp.elements[1].position = 0.6
    color_ramp.color_ramp.elements[1].color = (0.9, 0.6, 0.3, 1)
    hsv = nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Hue"].default_value = 0.55
    hsv.inputs["Saturation"].default_value = 1.3
    hsv.inputs["Value"].default_value = 0.9
    power = nodes.new("ShaderNodeMath")
    power.operation = 'POWER'
    power.inputs[1].default_value = 2.2
    # A colour Mix: its outputs share the name "Result" (Float/Vector/Color).
    tint = nodes.new("ShaderNodeMix")
    tint.data_type = 'RGBA'
    tint.blend_type = 'MULTIPLY'
    tint.inputs["Factor"].default_value = 0.7
    tint_colour = next(socket for socket in tint.inputs if socket.identifier == "B_Color")
    tint_colour.default_value = (1.0, 0.8, 0.6, 1.0)
    links.new(base_node.outputs["Color"], color_ramp.inputs["Fac"])
    links.new(color_ramp.outputs["Color"], hsv.inputs["Color"])
    links.new(hsv.outputs["Color"], next(s for s in tint.inputs if s.identifier == "A_Color"))
    links.new(next(s for s in tint.outputs if s.identifier == "Result_Color"), bsdf.inputs["Base Color"])
    links.new(rough_node.outputs["Color"], power.inputs[0])
    links.new(power.outputs[0], bsdf.inputs["Roughness"])
    return material, {
        "uv": uv, "mapping": mapping, "base": base_node, "rough": rough_node,
        "ramp": color_ramp, "hsv": hsv, "tint": tint, "power": power, "bsdf": bsdf,
    }


def plane_with(material):
    mesh = bpy.data.meshes.new("Board")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        layer.data[loop.index].uv = mesh.vertices[loop.vertex_index].co.xy
    mesh.materials.append(material)
    obj = bpy.data.objects.new("Board", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def bake_pass(material, bake_type, **kwargs):
    image = bpy.data.images.new(f"bake_{bake_type}", BAKE, BAKE, alpha=False, float_buffer=True)
    target = material.node_tree.nodes.new("ShaderNodeTexImage")
    target.image = image
    material.node_tree.nodes.active = target
    try:
        assert bpy.ops.object.bake(type=bake_type, margin=0, use_clear=True, **kwargs) == {'FINISHED'}
        pixels = np.empty(BAKE * BAKE * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return pixels.reshape(BAKE, BAKE, 4)[..., :3].astype(np.float64)
    finally:
        material.node_tree.nodes.remove(target)
        bpy.data.images.remove(image)


def refused(material, nodes, expected):
    for node in material.node_tree.nodes:
        node.select = False
    try:
        node_flatten.flatten_nodes(bpy.context, material, nodes)
    except node_flatten.FlattenError as exc:
        check(expected in str(exc), f'refusal "{exc}" misses "{expected}"')
        return
    PROBLEMS.append(f"not refused: {[node.name for node in nodes]} (expected {expected})")


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    folder = tempfile.mkdtemp(prefix="pmvr_flatten_")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "flatten.blend"))
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1

    material, n = build_material(folder)
    plane_with(material)
    before_colour = bake_pass(material, 'DIFFUSE', pass_filter={'COLOR'})
    before_rough = bake_pass(material, 'ROUGHNESS')

    # Refusals leave the material untouched.
    count = len(material.node_tree.nodes)
    refused(material, [n["ramp"], n["hsv"]], "feeds the selection")
    emission = material.node_tree.nodes.new("ShaderNodeEmission")
    add_shader = material.node_tree.nodes.new("ShaderNodeAddShader")
    material.node_tree.links.new(emission.outputs[0], add_shader.inputs[0])
    refused(material, [emission], "outputs a shader")
    material.node_tree.nodes.remove(emission)
    material.node_tree.nodes.remove(add_shader)
    geometry = material.node_tree.nodes.new("ShaderNodeNewGeometry")
    mix = material.node_tree.nodes.new("ShaderNodeMix")
    material.node_tree.links.new(geometry.outputs["Pointiness"], mix.inputs["Factor"])
    material.node_tree.links.new(mix.outputs["Result"], n["bsdf"].inputs["Metallic"])
    refused(material, [geometry, mix], "depends on the object's surface")
    noise = material.node_tree.nodes.new("ShaderNodeTexNoise")
    material.node_tree.links.new(noise.outputs["Fac"], mix.inputs["Factor"])
    refused(material, [noise, mix], "Generated coordinates")
    lone = material.node_tree.nodes.new("ShaderNodeTexImage")
    lone.image = n["base"].image
    material.node_tree.links.new(lone.outputs["Color"], mix.inputs["Factor"])
    other = material.node_tree.nodes.new("ShaderNodeTexImage")
    other.image = n["rough"].image
    material.node_tree.links.new(n["mapping"].outputs["Vector"], other.inputs["Vector"])
    material.node_tree.links.new(other.outputs["Color"], mix.inputs["A"])
    refused(material, [lone, other, mix], "different coordinates")
    for node in (geometry, mix, noise, lone, other):
        material.node_tree.nodes.remove(node)
    check(len(material.node_tree.nodes) == count, "a refusal changed the material")
    check(not os.path.isdir(os.path.join(folder, "PMVR_Flattened"))
          or not os.listdir(os.path.join(folder, "PMVR_Flattened")), "a refusal wrote files")

    # Flatten both chains in one go.
    selected = [n["base"], n["ramp"], n["hsv"], n["tint"], n["rough"], n["power"]]
    created, notes = node_flatten.flatten_nodes(bpy.context, material, selected)
    print(f"flatten: created {created}; notes {notes}")
    nodes = material.node_tree.nodes
    check(not any(node.type in {'VALTORGB', 'HUE_SAT', 'MATH', 'MIX'} for node in nodes), "corrections still in the material")
    images = [node for node in nodes if node.type == 'TEX_IMAGE']
    check(len(images) == 2, f"{len(images)} image nodes after flatten")
    bsdf = n["bsdf"]
    base_link = bsdf.inputs["Base Color"].links[0].from_node
    rough_link = bsdf.inputs["Roughness"].links[0].from_node
    check(base_link.type == 'TEX_IMAGE' and base_link.image.colorspace_settings.name == 'sRGB',
          "Base Color is not fed by an sRGB flattened image")
    check(rough_link.type == 'TEX_IMAGE' and rough_link.image.colorspace_settings.name == 'Non-Color',
          "Roughness is not fed by a Non-Color flattened image")
    for node in (base_link, rough_link):
        check(tuple(node.image.size) == (64, 64), f"{node.image.name} is {tuple(node.image.size)}, expected 64")
        check(node.inputs["Vector"].links and node.inputs["Vector"].links[0].from_node == n["mapping"],
              f"{node.image.name} lost the Mapping (tiling)")
        check(node.image.filepath.startswith("//PMVR_Flattened"), f"image path {node.image.filepath}")

    after_colour = bake_pass(material, 'DIFFUSE', pass_filter={'COLOR'})
    after_rough = bake_pass(material, 'ROUGHNESS')
    colour_diff = np.abs(srgb(after_colour) - srgb(before_colour)) * 255
    rough_diff = np.abs(after_rough - before_rough) * 255
    print(
        f"flatten: Base Color differs by {colour_diff.max():.2f} of an 8-bit step at most "
        f"({colour_diff.mean():.2f} on average), Roughness by {rough_diff.max():.2f} ({rough_diff.mean():.2f})"
    )
    # Storing 8 bits rounds by up to half a step; sampling a noise texture a
    # hair off the texel centre adds a little. A real change is many steps.
    check(colour_diff.max() <= 1.0 and colour_diff.mean() <= 0.3, f"Base Color changed by {colour_diff.max():.2f} codes")
    check(rough_diff.max() <= 1.0 and rough_diff.mean() <= 0.3, f"Roughness changed by {rough_diff.max():.2f} codes")
    check(abs(srgb(before_colour).mean() - srgb(after_colour).mean()) * 255 < 0.2, "Base Color mean moved")

    # An oversized, non-square data texture flattened smaller: 64 x 32 through
    # Math into Metallic, long side 16 -> 16 x 8. Every 4th column is white:
    # averaged that is 0.25 everywhere, sampled at 16 points it would be black.
    pixels = np.ones((32, 64, 4), dtype=np.float32)
    pixels[..., :3] = 0.0
    pixels[:, ::4, :3] = 1.0
    wide = bpy.data.images.new("wide", 64, 32, alpha=False)
    wide.pixels.foreach_set(pixels.ravel())
    wide.filepath_raw = os.path.join(folder, "wide.png")
    wide.file_format = 'PNG'
    wide.save()
    wide.source = 'FILE'
    wide.reload()
    wide.colorspace_settings.name = 'Non-Color'
    wide_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    wide_node.image = wide
    scale = material.node_tree.nodes.new("ShaderNodeMath")
    scale.operation = 'MULTIPLY'
    scale.inputs[1].default_value = 0.8
    material.node_tree.links.new(wide_node.outputs["Color"], scale.inputs[0])
    material.node_tree.links.new(scale.outputs[0], n["bsdf"].inputs["Metallic"])
    created, _notes = node_flatten.flatten_nodes(bpy.context, material, [wide_node, scale], long_side=16)
    print(f"flatten: oversized texture -> {created}")
    metallic = n["bsdf"].inputs["Metallic"].links[0].from_node
    check(tuple(metallic.image.size) == (16, 8), f"long side 16 of 64 x 32 gave {tuple(metallic.image.size)}")
    values = np.empty(16 * 8 * 4, dtype=np.float32)
    metallic.image.pixels.foreach_get(values)
    values = values.reshape(8, 16, 4)[..., 0]
    check(abs(values.mean() - 0.2) < 1.5 / 255 and values.std() < 1.5 / 255,
          f"stripes x 0.8 averaged to {values.mean():.3f} +- {values.std():.3f}, expected 0.2 flat")
    check(node_flatten.output_size((4096, 1024), 1024) == (1024, 256), "aspect of 4096 x 1024 not kept")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_NODE_FLATTEN_SMOKE_FAILED" if PROBLEMS else "PM_VR_NODE_FLATTEN_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
