"""Colours USD cannot carry, made explicit for the length of an export.

Blender's USD (and glTF) export reads a Principled BSDF's colour inputs as
constants: a Blackbody node feeding Emission Color or Base Color arrives as
white. While a layer is written, each such link is replaced by the colour
Blender renders for that temperature (emission normalised, its peak moved
into Emission Strength, so USD gets colour x strength exactly), then put
back. Materials without a Principled or Diffuse BSDF are named in the log:
USD gets no surface for them.
"""

from contextlib import contextmanager
import uuid

import bpy
import numpy

from . import log


COLOUR_INPUTS = ("Emission Color", "Base Color")
USD_SURFACES = {'BSDF_PRINCIPLED', 'BSDF_DIFFUSE'}
_BLACKBODY = {}


def blackbody_colour(context, kelvin):
    """Linear RGB Blender renders for a Blackbody node at kelvin, from an
    emission bake of a tiny plane in a temporary scene (cached)."""
    key = round(float(kelvin), 3)
    if key in _BLACKBODY:
        return _BLACKBODY[key]
    scene = bpy.data.scenes.new(f"__PMVR_BLACKBODY_{uuid.uuid4().hex[:8]}")
    mesh = bpy.data.meshes.new("__PMVR_BLACKBODY")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        uv.data[loop.index].uv = mesh.vertices[loop.vertex_index].co.xy
    plane = bpy.data.objects.new("__PMVR_BLACKBODY", mesh)
    material = bpy.data.materials.new("__PMVR_BLACKBODY")
    image = bpy.data.images.new("__PMVR_BLACKBODY", 2, 2, alpha=False, float_buffer=True)
    try:
        scene.collection.objects.link(plane)
        mesh.materials.append(material)
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 1
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        emission = nodes.new("ShaderNodeEmission")
        blackbody = nodes.new("ShaderNodeBlackbody")
        blackbody.inputs["Temperature"].default_value = kelvin
        links.new(blackbody.outputs[0], emission.inputs["Color"])
        links.new(emission.outputs[0], output.inputs["Surface"])
        target = nodes.new("ShaderNodeTexImage")
        target.image = image
        nodes.active = target
        view_layer = scene.view_layers[0]
        plane.select_set(True, view_layer=view_layer)
        view_layer.objects.active = plane
        with context.temp_override(
            scene=scene, view_layer=view_layer, active_object=plane, object=plane,
            selected_objects=[plane], selected_editable_objects=[plane],
        ):
            bpy.ops.object.bake(type='EMIT', margin=0, use_clear=True, target='IMAGE_TEXTURES')
        pixels = numpy.empty(16, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        colour = tuple(float(v) for v in pixels.reshape(4, 4)[:, :3].mean(axis=0))
    finally:
        bpy.data.images.remove(image)
        bpy.data.scenes.remove(scene)
        bpy.data.objects.remove(plane)
        bpy.data.meshes.remove(mesh)
        bpy.data.materials.remove(material)
    _BLACKBODY[key] = colour
    return colour


def _blackbody_feeding(socket):
    """(link, Blackbody node) when the socket is fed by a Blackbody with a
    fixed temperature, through reroutes; otherwise None."""
    if not socket.is_linked:
        return None
    link = socket.links[0]
    node = link.from_node
    while node.type == 'REROUTE' and node.inputs[0].is_linked:
        node = node.inputs[0].links[0].from_node
    if node.type != 'BLACKBODY' or node.inputs["Temperature"].is_linked:
        return None
    return link, node


def _materials(objects):
    seen = {}
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            if slot.material and slot.material.node_tree:
                seen.setdefault(slot.material.name_full, slot.material)
    return list(seen.values())


@contextmanager
def explicit_colours(context, objects, label):
    changes = []
    converted = []
    try:
        for material in _materials(objects):
            nodes = material.node_tree.nodes
            if not any(node.type in USD_SURFACES for node in nodes):
                log.warning(
                    "Export",
                    f'{label}: material "{material.name}" has no Principled BSDF; '
                    "USD gets no surface for it (use a Principled BSDF with emission)",
                )
                continue
            for node in nodes:
                if node.type != 'BSDF_PRINCIPLED':
                    continue
                for name in COLOUR_INPUTS:
                    socket = node.inputs.get(name)
                    found = socket and _blackbody_feeding(socket)
                    if not found:
                        continue
                    link, blackbody = found
                    kelvin = blackbody.inputs["Temperature"].default_value
                    colour = blackbody_colour(context, kelvin)
                    peak = max(colour) or 1.0
                    strength = node.inputs.get("Emission Strength") if name == "Emission Color" else None
                    change = {
                        "tree": material.node_tree, "node": node.name, "socket": socket.identifier,
                        "from_node": link.from_node.name, "from_socket": link.from_socket.identifier,
                        "default": tuple(socket.default_value), "strength": None,
                    }
                    material.node_tree.links.remove(link)
                    socket.default_value = (*(value / peak for value in colour), 1.0)
                    if strength is not None and not strength.is_linked:
                        change["strength"] = strength.default_value
                        strength.default_value = strength.default_value * peak
                    changes.append(change)
                    converted.append(f'"{material.name}" {name} {kelvin:g}K')
        if converted:
            log.info("Export", f"{label}: Blackbody written as colour: {', '.join(converted)}")
        yield
    finally:
        for change in reversed(changes):
            try:
                tree = change["tree"]
                node = tree.nodes[change["node"]]
                socket = next(s for s in node.inputs if s.identifier == change["socket"])
                socket.default_value = change["default"]
                if change["strength"] is not None:
                    node.inputs["Emission Strength"].default_value = change["strength"]
                source = tree.nodes[change["from_node"]]
                tree.links.new(next(s for s in source.outputs if s.identifier == change["from_socket"]), socket)
            except (KeyError, ReferenceError, StopIteration) as exc:
                log.error("Export", f"Could not restore a Blackbody link after export: {exc}")
