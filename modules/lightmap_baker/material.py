"""Material-copy node setup for generated lightmap objects."""

from .constants import (
    BAKE_UV_NAME,
    NODE_IMAGE_NAME,
    NODE_MIX_NAME,
    NODE_UV_NAME,
)
from .images import set_scene_linear_colorspace


def _active_material_output(nodes):
    outputs = [
        node
        for node in nodes
        if node.type == 'OUTPUT_MATERIAL'
    ]
    active = [node for node in outputs if node.is_active_output]
    if len(active) == 1:
        return active[0]
    return outputs[0] if len(outputs) == 1 else None


def _principled_nodes_reaching_output(tree, output):
    if not output:
        return []

    surface = output.inputs.get("Surface")
    if not surface or not surface.is_linked:
        return []

    found = []
    visited = set()
    stack = [link.from_node for link in surface.links]
    while stack:
        node = stack.pop()
        pointer = node.as_pointer()
        if pointer in visited:
            continue
        visited.add(pointer)
        if node.type == 'BSDF_PRINCIPLED':
            found.append(node)
        for socket in node.inputs:
            stack.extend(link.from_node for link in socket.links)
    return found


def _find_principled(tree, allow_automatic_hookup):
    if not allow_automatic_hookup:
        return None

    nodes = tree.nodes
    all_principled = [
        node
        for node in nodes
        if node.type == 'BSDF_PRINCIPLED'
    ]
    if len(all_principled) == 1:
        return all_principled[0]

    output = _active_material_output(nodes)
    connected = _principled_nodes_reaching_output(tree, output)
    return connected[0] if len(connected) == 1 else None


def _create_texture_nodes(tree, image, location):
    uv_node = tree.nodes.new("ShaderNodeUVMap")
    uv_node.name = NODE_UV_NAME
    uv_node.label = 'Lightmap UV: "SimpleBake"'
    uv_node.uv_map = BAKE_UV_NAME
    uv_node.location = location

    image_node = tree.nodes.new("ShaderNodeTexImage")
    image_node.name = NODE_IMAGE_NAME
    image_node.label = "PM Lightmap (Scene Linear)"
    image_node.image = image
    image_node.interpolation = 'Linear'
    image_node.extension = 'EXTEND'
    image_node.location = (location[0] + 220.0, location[1])
    tree.links.new(uv_node.outputs["UV"], image_node.inputs["Vector"])
    set_scene_linear_colorspace(image)
    return uv_node, image_node


def _mix_socket(node, name):
    sockets = [
        socket
        for socket in node.inputs
        if socket.name == name and not socket.is_unavailable
    ]
    return sockets[0] if sockets else node.inputs.get(name)


def _mix_output(node, name):
    sockets = [
        socket
        for socket in node.outputs
        if socket.name == name and not socket.is_unavailable
    ]
    return sockets[0] if sockets else node.outputs.get(name)


def _connect_before_base_color(tree, principled, image_node):
    base_color = principled.inputs.get("Base Color")
    if not base_color:
        raise RuntimeError("Principled BSDF has no Base Color input")

    old_link = base_color.links[0] if base_color.is_linked else None
    old_from_socket = old_link.from_socket if old_link else None
    old_default = tuple(base_color.default_value)

    mix = tree.nodes.new("ShaderNodeMix")
    mix.name = NODE_MIX_NAME
    mix.label = "Base Color × Lightmap"
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.location = (
        principled.location.x - 240.0,
        principled.location.y + 30.0,
    )
    if hasattr(mix, "clamp_result"):
        mix.clamp_result = False
    if hasattr(mix, "factor_mode"):
        mix.factor_mode = 'UNIFORM'

    factor = _mix_socket(mix, "Factor")
    input_a = _mix_socket(mix, "A")
    input_b = _mix_socket(mix, "B")
    result = _mix_output(mix, "Result")
    if not all((factor, input_a, input_b, result)):
        tree.nodes.remove(mix)
        raise RuntimeError("Mix Color sockets are unavailable")

    factor.default_value = 1.0
    if old_link:
        tree.links.remove(old_link)
        tree.links.new(old_from_socket, input_a)
    else:
        input_a.default_value = old_default

    tree.links.new(image_node.outputs["Color"], input_b)
    tree.links.new(result, base_color)
    return mix


def add_lightmap_nodes(material, image, source_used_nodes):
    """
    Add the lightmap nodes to a copied material.

    Returns True when the lightmap was connected to a unique Principled BSDF,
    otherwise leaves an explicit unconnected UV -> Image pair and returns False.
    """
    if not material.use_nodes:
        material.use_nodes = True

    tree = material.node_tree
    principled = _find_principled(tree, source_used_nodes)
    if principled:
        uv_location = (
            principled.location.x - 680.0,
            principled.location.y - 170.0,
        )
        _, image_node = _create_texture_nodes(tree, image, uv_location)
        _connect_before_base_color(tree, principled, image_node)
        return True

    output = _active_material_output(tree.nodes)
    if output:
        location = (
            output.location.x - 520.0,
            output.location.y - 220.0,
        )
    else:
        location = (-420.0, -180.0)
    _create_texture_nodes(tree, image, location)
    return False
