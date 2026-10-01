"""Blackbody colours reach USD; materials come back unchanged.

Run with Blender --background --factory-startup --python this_file.py.

USD export reads a Principled BSDF's colour inputs as constants, so a
Blackbody feeding them arrived as white. For the length of an export each
such link becomes the colour Blender renders (emission normalised, its peak
moved into strength): a lamp with 3000 K x 1.3 emission and a 6500 K base,
and a night shade whose camera branch (Light Path mix) is a Principled fed
through a reroute, arrive with the right colours; a material with only an
Emission shader is named in the log (USD gets no surface for it). After the
export every link, colour and strength is as before.
"""

import os
import sys

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import blender_resolution_smoke as base  # noqa: E402
from PM_VR.modules.pipeline import export_colours  # noqa: E402

PROBLEMS = []
KNOWN_3000K = (1.7708, 0.8443, 0.2722)  # measured in Blender 5.2 (UniPlace notes)


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def close(a, b, tolerance=2e-3):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def blackbody(nodes, kelvin):
    node = nodes.new("ShaderNodeBlackbody")
    node.inputs["Temperature"].default_value = kelvin
    return node


def warm_lamp():
    mat = bpy.data.materials.new("WarmLamp")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    links.new(blackbody(nodes, 3000).outputs[0], bsdf.inputs["Emission Color"])
    links.new(blackbody(nodes, 6500).outputs[0], bsdf.inputs["Base Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.3
    return mat


def night_shade():
    mat = bpy.data.materials.new("NightShade")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    mix = nodes.new("ShaderNodeMixShader")
    light = nodes.new("ShaderNodeEmission")
    light.inputs["Strength"].default_value = 30
    links.new(blackbody(nodes, 4500).outputs[0], light.inputs["Color"])
    look = nodes.new("ShaderNodeBsdfPrincipled")
    look.name = "Look"
    look.inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1)
    look.inputs["Roughness"].default_value = 0.15
    look.inputs["Emission Strength"].default_value = 1.3
    reroute = nodes.new("NodeReroute")
    links.new(blackbody(nodes, 3000).outputs[0], reroute.inputs[0])
    links.new(reroute.outputs[0], look.inputs["Emission Color"])
    path = nodes.new("ShaderNodeLightPath")
    add = nodes.new("ShaderNodeMath")
    add.operation = 'ADD'
    links.new(path.outputs["Is Camera Ray"], add.inputs[0])
    links.new(path.outputs["Is Singular Ray"], add.inputs[1])
    links.new(add.outputs[0], mix.inputs[0])
    links.new(light.outputs[0], mix.inputs[1])
    links.new(look.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def emission_only():
    mat = bpy.data.materials.new("EmissionOnly")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    links.new(emission.outputs[0], out.inputs["Surface"])
    return mat


def light_path_factor(nodes, links):
    path = nodes.new("ShaderNodeLightPath")
    add = nodes.new("ShaderNodeMath")
    add.operation = 'ADD'
    links.new(path.outputs["Is Camera Ray"], add.inputs[0])
    links.new(path.outputs["Is Singular Ray"], add.inputs[1])
    return add.outputs[0]


def socket(node, identifier, outputs=False):
    return next(s for s in (node.outputs if outputs else node.inputs) if s.identifier == identifier)


def user_shade():
    """The UniPlace night shade: one Principled; colour and strength are Mix
    nodes driven by Light Path (camera: 3000 K x 1.3, rest: 4500 K x 30)."""
    mat = bpy.data.materials.new("UserShade")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    colour = nodes.new("ShaderNodeMix")
    colour.data_type = 'RGBA'
    links.new(light_path_factor(nodes, links), socket(colour, "Factor_Float"))
    links.new(blackbody(nodes, 4500).outputs[0], socket(colour, "A_Color"))
    links.new(blackbody(nodes, 3000).outputs[0], socket(colour, "B_Color"))
    links.new(socket(colour, "Result_Color", True), bsdf.inputs["Base Color"])
    links.new(socket(colour, "Result_Color", True), bsdf.inputs["Emission Color"])
    strength = nodes.new("ShaderNodeMix")
    strength.data_type = 'FLOAT'
    links.new(light_path_factor(nodes, links), socket(strength, "Factor_Float"))
    socket(strength, "A_Float").default_value = 30.0
    socket(strength, "B_Float").default_value = 1.3
    links.new(socket(strength, "Result_Float", True), bsdf.inputs["Emission Strength"])
    bsdf.inputs["Emission Strength"].default_value = 7.0
    return mat


def odd_strength():
    """Strength from a noise texture: unreadable, exported as 1."""
    mat = bpy.data.materials.new("OddStrength")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Emission Color"].default_value = (0.2, 0.4, 1.0, 1)
    noise = nodes.new("ShaderNodeTexNoise")
    links.new(noise.outputs["Fac"], bsdf.inputs["Emission Strength"])
    return mat


def screen():
    """Emission from an image (a display) keeps its texture."""
    mat = bpy.data.materials.new("Screen")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    image = bpy.data.images.new("ScreenImage", 8, 8)
    image.filepath_raw = os.path.join(bpy.app.tempdir or os.environ.get("TEMP", "."), "pmvr_screen.png")
    image.file_format = 'PNG'
    image.save()
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 2.0
    return mat


def snapshot(material):
    """Links and values that must survive the export."""
    tree = material.node_tree
    links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier) for l in tree.links)
    values = {
        (node.name, socket.identifier): tuple(socket.default_value) if hasattr(socket.default_value, "__len__") else socket.default_value
        for node in tree.nodes for socket in node.inputs
        if socket.name in ("Emission Color", "Base Color", "Emission Strength") and hasattr(socket, "default_value")
    }
    return links, values


def usd_surfaces(path):
    from pxr import Usd, UsdShade
    stage = Usd.Stage.Open(path)
    found = {}
    for prim in stage.Traverse():
        if prim.IsA(UsdShade.Material):
            shader = UsdShade.Material(prim).ComputeSurfaceSource()[0]
            found[prim.GetName()] = None if not shader else {
                name: ("texture" if shader.GetInput(name).HasConnectedSource() else shader.GetInput(name).Get())
                for name in ("diffuseColor", "emissiveColor", "roughness")
                if shader.GetInput(name)
            }
    return found


def main():
    base.PM_VR.register()
    project, root, output = base.build()
    layers = {layer.display_name: layer for layer in project.render_layers}
    for name, layer in layers.items():
        layer.enabled = name == "Emissive"
        layer.export_glb = False
    materials = [warm_lamp(), night_shade(), emission_only(), user_shade(), odd_strength(), screen()]
    before = {m.name: snapshot(m) for m in materials}
    project.active_render_layer_index = list(project.render_layers).index(layers["Emissive"])
    for index, material in enumerate(materials):
        plane = base.add_plane(root, f"Lamp{index}", 0.5)
        plane.location.x = index
        plane.data.materials.append(material)
        assert bpy.ops.pmvr.assign_selected_to_layer() == {'FINISHED'}
    base.activate_state(bpy.context, 'DAY')
    assert bpy.ops.pmvr.export_semantic_layers(export_format='USDZ') == {'FINISHED'}, project.last_operation_summary

    colour_3000 = export_colours.blackbody_colour(bpy.context, 3000)
    colour_6500 = export_colours.blackbody_colour(bpy.context, 6500)
    print(f"colours: Blender 3000 K {tuple(round(v, 4) for v in colour_3000)}, 6500 K {tuple(round(v, 4) for v in colour_6500)}")
    check(close(colour_3000, KNOWN_3000K), f"3000 K rendered as {colour_3000}, expected {KNOWN_3000K}")
    surfaces = usd_surfaces(os.path.join(output, "Emissive.usdz"))
    print(f"colours: USD {surfaces}")
    expected_emission = tuple(v * 1.3 for v in colour_3000)
    warm = surfaces.get("WarmLamp") or {}
    check(close(tuple(warm.get("emissiveColor", ())), expected_emission), f"WarmLamp emission {warm.get('emissiveColor')} != {expected_emission}")
    peak = max(colour_6500)
    check(close(tuple(warm.get("diffuseColor", ())), tuple(v / peak for v in colour_6500)), f"WarmLamp base {warm.get('diffuseColor')}")
    night = surfaces.get("NightShade") or {}
    check(close(tuple(night.get("emissiveColor", ())), expected_emission), f"NightShade emission {night.get('emissiveColor')}")
    check(abs(night.get("roughness", 0) - 0.15) < 1e-4 and close(tuple(night.get("diffuseColor", ())), (0.9, 0.9, 0.9)),
          f"NightShade look {night}")
    user = surfaces.get("UserShade") or {}
    check(close(tuple(user.get("emissiveColor", ())), expected_emission),
          f"UserShade emission {user.get('emissiveColor')} != camera side {expected_emission}")
    peak_3000 = max(colour_3000)
    check(close(tuple(user.get("diffuseColor", ())), tuple(v / peak_3000 for v in colour_3000)),
          f"UserShade base {user.get('diffuseColor')}")
    odd = surfaces.get("OddStrength") or {}
    check(close(tuple(odd.get("emissiveColor", ())), (0.2, 0.4, 1.0)), f"OddStrength emission {odd.get('emissiveColor')} (strength should be 1)")
    check((surfaces.get("Screen") or {}).get("emissiveColor") == "texture", f"Screen lost its texture: {surfaces.get('Screen')}")
    check('"OddStrength" strength 1 (unreadable, 1)' in bpy.data.texts["PMVR Pipeline Log"].as_string(), "log misses the unreadable strength")
    check("EmissionOnly" in surfaces and surfaces["EmissionOnly"] is None, f"EmissionOnly in USD: {surfaces.get('EmissionOnly')}")
    log_text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check('material "EmissionOnly" has no Principled BSDF' in log_text, "log misses the surface warning")
    check("written as the camera sees them" in log_text, "log misses the conversion")

    after = {m.name: snapshot(m) for m in materials}
    for name in before:
        check(before[name] == after[name], f"{name} changed by the export:\n  {before[name]}\n  {after[name]}")
    leftovers = [d.name for coll in (bpy.data.scenes, bpy.data.materials, bpy.data.images, bpy.data.objects)
                 for d in coll if d.name.startswith("__PMVR_BLACKBODY")]
    check(not leftovers, f"left behind: {leftovers}")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_EXPORT_COLOURS_SMOKE_FAILED" if PROBLEMS else "PM_VR_EXPORT_COLOURS_SMOKE_OK")
    base.PM_VR.unregister()


if __name__ == "__main__":
    main()
