"""Colours USD cannot carry, made explicit for the length of an export.

Blender's USD (and glTF) export reads a Principled BSDF's colour and
strength inputs as constants: whatever feeds them from a Blackbody, or a Mix
driven by Light Path (a lamp that looks one way to the camera and lights the
room another way), arrives as the socket's unlinked value, often white x 1.
While a layer is written, such inputs are set to what the camera sees:
Blackbody as the colour Blender renders, a Light Path Mix as its camera side,
Math and constants evaluated. Emission colour is normalised and its peak
moved into Emission Strength, so USD gets colour x strength exactly. Inputs
that cannot be read (a texture, another blend mode) keep their link; an
unreadable Emission Strength becomes 1. Everything is put back afterwards.
Materials without a Principled or Diffuse BSDF are named in the log: USD gets
no surface for them.
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


# Light Path outputs as a camera ray sees them.
_CAMERA_RAY = {"Is Camera Ray": 1.0}


def _math(operation, a, b):
    table = {
        'ADD': lambda: a + b, 'SUBTRACT': lambda: a - b, 'MULTIPLY': lambda: a * b,
        'DIVIDE': lambda: a / b if b else 0.0, 'MAXIMUM': lambda: max(a, b), 'MINIMUM': lambda: min(a, b),
        'GREATER_THAN': lambda: 1.0 if a > b else 0.0, 'LESS_THAN': lambda: 1.0 if a < b else 0.0,
        'POWER': lambda: a ** b if a >= 0 else 0.0,
    }
    return table[operation]() if operation in table else None


def _as_float(value):
    return value if isinstance(value, float) else sum(value[:3]) / 3.0


def _as_colour(value):
    return (value, value, value) if isinstance(value, float) else tuple(value[:3])


def _mix_sockets(node):
    """(factor, a, b, clamp) of a plain Mix node, or None."""
    if node.type == 'MIX_RGB' and node.blend_type == 'MIX':
        return node.inputs["Fac"], node.inputs["Color1"], node.inputs["Color2"], True
    if node.type == 'MIX' and node.data_type in {'FLOAT', 'RGBA'} and (node.data_type == 'FLOAT' or node.blend_type == 'MIX'):
        suffix = "Float" if node.data_type == 'FLOAT' else "Color"
        pick = {socket.identifier: socket for socket in node.inputs}
        factor = pick.get("Factor_Float")
        return factor, pick.get(f"A_{suffix}"), pick.get(f"B_{suffix}"), node.clamp_factor
    return None


def camera_value(context, socket, depth=0):
    """What a camera ray reads from an input socket: a float or an (r, g, b)
    tuple in linear light; None when it cannot be read (a texture, a blend
    mode other than Mix, anything else)."""
    if depth > 32:
        return None
    if not socket.is_linked:
        value = socket.default_value
        return float(value) if isinstance(value, (int, float)) else tuple(value)[:3]
    link = socket.links[0]
    node, output = link.from_node, link.from_socket
    if node.type == 'REROUTE':
        return camera_value(context, node.inputs[0], depth + 1)
    if node.type == 'BLACKBODY':
        kelvin = camera_value(context, node.inputs["Temperature"], depth + 1)
        return blackbody_colour(context, kelvin) if isinstance(kelvin, float) else None
    if node.type in {'RGB', 'VALUE'}:
        value = output.default_value
        return float(value) if isinstance(value, (int, float)) else tuple(value)[:3]
    if node.type == 'LIGHT_PATH':
        return _CAMERA_RAY.get(output.name, 0.0)
    if node.type == 'MATH':
        a = camera_value(context, node.inputs[0], depth + 1)
        b = camera_value(context, node.inputs[1], depth + 1)
        if a is None or b is None:
            return None
        result = _math(node.operation, _as_float(a), _as_float(b))
        if result is not None and node.use_clamp:
            result = min(1.0, max(0.0, result))
        return result
    mix = _mix_sockets(node)
    if mix and all(mix[:3]):
        factor, a, b, clamp = mix
        f = camera_value(context, factor, depth + 1)
        if f is None:
            return None
        f = _as_float(f)
        if clamp:
            f = min(1.0, max(0.0, f))
        # Only the side the camera sees has to be readable.
        if f <= 0.0:
            return camera_value(context, a, depth + 1)
        if f >= 1.0:
            return camera_value(context, b, depth + 1)
        va, vb = camera_value(context, a, depth + 1), camera_value(context, b, depth + 1)
        if va is None or vb is None:
            return None
        if isinstance(va, float) and isinstance(vb, float):
            return va * (1 - f) + vb * f
        ca, cb = _as_colour(va), _as_colour(vb)
        return tuple(x * (1 - f) + y * f for x, y in zip(ca, cb))
    return None


def _materials(objects):
    seen = {}
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            if slot.material and slot.material.node_tree:
                seen.setdefault(slot.material.name_full, slot.material)
    return list(seen.values())


def _set(change_list, tree, node, socket, value):
    """Unlink socket (remembering the link) and give it value."""
    link = socket.links[0] if socket.is_linked else None
    change_list.append({
        "tree": tree, "node": node.name, "socket": socket.identifier,
        "from_node": link.from_node.name if link else None,
        "from_socket": link.from_socket.identifier if link else None,
        "default": tuple(socket.default_value) if hasattr(socket.default_value, "__len__") else socket.default_value,
    })
    if link:
        tree.links.remove(link)
    socket.default_value = value


@contextmanager
def explicit_colours(context, objects, label):
    changes = []
    notes = []
    try:
        for material in _materials(objects):
            tree = material.node_tree
            nodes = tree.nodes
            if not any(node.type in USD_SURFACES for node in nodes):
                log.warning(
                    "Export",
                    f'{label}: material "{material.name}" has no Principled BSDF; '
                    "USD gets no surface for it (use a Principled BSDF with emission)",
                )
                continue
            for node in [n for n in nodes if n.type == 'BSDF_PRINCIPLED']:
                colours = {}
                for name in COLOUR_INPUTS:
                    socket = node.inputs.get(name)
                    if socket and socket.is_linked:
                        value = camera_value(context, socket)
                        if value is not None:
                            colours[name] = _as_colour(value)
                strength_socket = node.inputs.get("Emission Strength")
                strength = None
                if strength_socket is not None and strength_socket.is_linked:
                    value = camera_value(context, strength_socket)
                    strength = _as_float(value) if value is not None else 1.0
                    notes.append(f'"{material.name}" strength {strength:g}' + ("" if value is not None else " (unreadable, 1)"))
                elif strength_socket is not None:
                    strength = strength_socket.default_value
                for name, colour in colours.items():
                    socket = node.inputs[name]
                    if name == "Emission Color":
                        peak = max(colour) or 1.0
                        _set(changes, tree, node, socket, (*(c / peak for c in colour), 1.0))
                        if strength is not None:
                            strength *= peak
                    else:
                        peak = max(1.0, max(colour))
                        _set(changes, tree, node, socket, (*(c / peak for c in colour), 1.0))
                    notes.append(f'"{material.name}" {name} {tuple(round(c, 3) for c in colour)}')
                if strength_socket is not None and strength is not None and (
                    strength_socket.is_linked or "Emission Color" in colours
                ):
                    _set(changes, tree, node, strength_socket, strength)
        if notes:
            log.info("Export", f"{label}: written as the camera sees them: {'; '.join(notes)}")
        yield
    finally:
        for change in reversed(changes):
            try:
                tree = change["tree"]
                node = tree.nodes[change["node"]]
                socket = next(s for s in node.inputs if s.identifier == change["socket"])
                socket.default_value = change["default"]
                if change["from_node"]:
                    source = tree.nodes[change["from_node"]]
                    tree.links.new(next(s for s in source.outputs if s.identifier == change["from_socket"]), socket)
            except (KeyError, ReferenceError, StopIteration) as exc:
                log.error("Export", f"Could not restore a material input after export: {exc}")
