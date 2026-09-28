"""Flatten selected shader nodes into one Image Texture (Shader Editor).

Colour corrections on PBR textures (Color Ramp, Hue/Saturation, Curves,
Math, Mix) do not reach USD. The selection is rendered texel by texel into
an image and replaced by a single Image Texture node. Coordinates left
outside the selection (UV Map, Mapping) stay connected to the new node, so
tiling and resolution are kept; a selected Mapping is baked in.
"""

import os
import uuid

import bpy
import numpy

from .pipeline.bake_files import scaled_linear


OUTPUT_FOLDER = "//PMVR_Flattened"

# Nodes whose result depends on the object's surface, the view or the light
# path: a UV-space texture cannot hold them.
GEOMETRY_NODES = {
    'NEW_GEOMETRY', 'AMBIENT_OCCLUSION', 'BEVEL', 'LAYER_WEIGHT', 'FRESNEL',
    'OBJECT_INFO', 'ATTRIBUTE', 'VERTEX_COLOR', 'LIGHT_PATH', 'CAMERA',
    'WIREFRAME', 'TANGENT', 'PARTICLE_INFO', 'POINT_INFO', 'HAIR_INFO',
    'CURVES_INFO', 'NORMAL_MAP', 'BUMP', 'DISPLACEMENT', 'VECTOR_DISPLACEMENT',
    'LIGHT_FALLOFF', 'VECT_TRANSFORM', 'TEX_ENVIRONMENT', 'TEX_SKY',
    'TEX_POINTDENSITY', 'SCRIPT', 'UVALONGSTROKE', 'VOLUME_INFO',
}
# Procedural textures read Generated coordinates when their Vector is free.
PROCEDURAL_TEXTURES = {
    'TEX_NOISE', 'TEX_VORONOI', 'TEX_WAVE', 'TEX_MAGIC', 'TEX_GRADIENT',
    'TEX_CHECKER', 'TEX_BRICK', 'TEX_WHITE_NOISE', 'TEX_GABOR',
}
CONSTANT_NODES = {'VALUE', 'RGB'}
DATA_DESTINATIONS = {'NORMAL_MAP', 'BUMP', 'DISPLACEMENT', 'VECTOR_DISPLACEMENT'}


class FlattenError(RuntimeError):
    pass


def _name(node):
    return node.label or node.name


def _source(socket_link):
    """(node, socket) that really feeds a link, through reroutes."""
    node, socket = socket_link.from_node, socket_link.from_socket
    while node.type == 'REROUTE' and node.inputs[0].is_linked:
        link = node.inputs[0].links[0]
        node, socket = link.from_node, link.from_socket
    return node, socket


def _check_nodes(nodes, where=""):
    for node in nodes:
        label = f'"{_name(node)}"{where}'
        if node.type in GEOMETRY_NODES:
            raise FlattenError(f"{label} depends on the object's surface or the view; a texture cannot hold it")
        if node.type in PROCEDURAL_TEXTURES and not node.inputs["Vector"].is_linked:
            raise FlattenError(f"{label} has no Vector input and uses Generated coordinates; connect a UV Map to it")
        if node.type == 'TEX_IMAGE' and node.projection != 'FLAT':
            raise FlattenError(f"{label} uses {node.projection.title()} projection; only Flat can be flattened")
        if node.type == 'TEX_COORD':
            used = {link.from_socket.name for output in node.outputs for link in output.links}
            if used - {"UV"}:
                raise FlattenError(f"{label} uses {', '.join(sorted(used - {'UV'}))} coordinates; only UV can be flattened")
        if node.type == 'GROUP' and node.node_tree:
            _check_nodes(node.node_tree.nodes, f' (inside group "{_name(node)}")')


class Plan:
    """What the selection turns into: outputs to bake, the coordinate source
    to keep, the texture size and the image settings to copy."""

    def __init__(self, tree, selected):
        self.selected = selected
        names = {node.name for node in selected}
        self.outputs = {}
        coordinates = set()
        for link in tree.links:
            inside_from = link.from_node.name in names
            inside_to = link.to_node.name in names
            if inside_from and not inside_to:
                if link.from_socket.type == 'SHADER':
                    raise FlattenError(
                        f'"{_name(link.from_node)}" outputs a shader; select only the nodes that make a texture'
                    )
                self.outputs.setdefault(link.from_socket.as_pointer(), (link.from_node, link.from_socket, []))[2].append(link)
            elif inside_to and not inside_from:
                node, socket = _source(link)
                if node.name in names:
                    continue
                if link.to_socket.type == 'VECTOR':
                    coordinates.add((node.name, socket.identifier))
                elif node.type not in CONSTANT_NODES:
                    raise FlattenError(
                        f'"{_name(node)}" feeds the selection; select it too, or leave only '
                        f"coordinates (UV Map, Mapping) outside"
                    )
        if not self.outputs:
            raise FlattenError("The selection is not connected to anything outside it")
        _check_nodes(selected)
        images = [node for node in selected if node.type == 'TEX_IMAGE']
        if any(not node.inputs["Vector"].is_linked for node in images):
            coordinates.add(None)  # the default UV map
        uv_maps = {node.uv_map for node in selected if node.type == 'UVMAP'}
        if len(coordinates) > 1 or len(uv_maps) > 1 or (coordinates and uv_maps):
            raise FlattenError("The selected textures use different coordinates; flatten them separately")
        self.coordinate = next(iter(coordinates), None)
        self.uv_map = next(iter(uv_maps), "")
        missing = [_name(node) for node in images if not node.image]
        if missing:
            raise FlattenError(f"Image Texture without an image: {', '.join(missing)}")
        self.template = max(images, key=lambda node: node.image.size[0] * node.image.size[1], default=None)
        # (width, height) of the largest image; None for procedural only.
        self.dimensions = tuple(self.template.image.size) if self.template else None


def _destinations(links):
    """Where the links finally arrive, through reroutes."""
    found = []
    for link in links:
        if link.to_node.type == 'REROUTE' and link.to_node.outputs[0].links:
            found += _destinations(link.to_node.outputs[0].links)
        else:
            found.append(link)
    return found


def _destination_is_colour(links):
    targets = _destinations(links)
    if any(link.to_node.type in DATA_DESTINATIONS for link in targets):
        return False
    return any(link.to_socket.type == 'RGBA' for link in targets)


def _label(material, links):
    target = _destinations(links)[0]
    if target.to_node.type == 'BSDF_PRINCIPLED':
        return f"{material.name}_{target.to_socket.name}"
    return f"{material.name}_{_name(target.to_node)}"


def _output_path(label, taken):
    """A new file in PMVR_Flattened next to the .blend; never overwrites."""
    if not bpy.data.filepath:
        raise FlattenError("Save the .blend first: the texture is written next to it")
    folder = bpy.path.abspath(OUTPUT_FOLDER)
    os.makedirs(folder, exist_ok=True)
    stem = bpy.path.clean_name(label)
    path = os.path.join(folder, f"{stem}.png")
    index = 2
    while os.path.exists(path) or path in taken:
        path = os.path.join(folder, f"{stem}_{index}.png")
        index += 1
    taken.add(path)
    return path


def _relative(path):
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return path


def output_size(dimensions, long_side):
    """(width, height) with long_side on the longer side and the aspect of
    dimensions (square without them)."""
    width, height = dimensions or (1, 1)
    scale = long_side / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _bake_outputs(context, material, plan, width, height):
    """Linear RGB arrays, one per selected output, rendered on a unit plane
    whose UVs span 0-1: each pixel samples the selection at its UV."""
    scene = bpy.data.scenes.new(f"__PMVR_FLATTEN_{uuid.uuid4().hex[:8]}")
    mesh = bpy.data.meshes.new("__PMVR_FLATTEN")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    names = ["UVMap"] + sorted({plan.uv_map} - {"", "UVMap"})
    for name in names:
        layer = mesh.uv_layers.new(name=name)
        for loop in mesh.loops:
            layer.data[loop.index].uv = mesh.vertices[loop.vertex_index].co.xy
    plane = bpy.data.objects.new("__PMVR_FLATTEN", mesh)
    copy = material.copy()
    image = bpy.data.images.new(f"__PMVR_FLATTEN_{uuid.uuid4().hex[:8]}", width, height, alpha=False, float_buffer=True)
    results = []
    try:
        scene.collection.objects.link(plane)
        mesh.materials.append(copy)
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 1
        for attribute, value in (("use_texture_cache", False), ("use_simplify", False)):
            if hasattr(scene.render, attribute):
                setattr(scene.render, attribute, value)
        nodes, links = copy.node_tree.nodes, copy.node_tree.links
        for node in [node for node in nodes if node.type == 'OUTPUT_MATERIAL']:
            nodes.remove(node)
        output = nodes.new("ShaderNodeOutputMaterial")
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Strength"].default_value = 1.0
        links.new(emission.outputs[0], output.inputs["Surface"])
        output.is_active_output = True
        # The coordinates the selection received now come from the plane.
        if plan.coordinate:
            uv = nodes.new("ShaderNodeUVMap")
            uv.uv_map = "UVMap"
            for link in list(links):
                if link.to_node.name in {node.name for node in plan.selected}:
                    node, socket = _source(link)
                    if (node.name, socket.identifier) == plan.coordinate:
                        target = link.to_socket
                        links.remove(link)
                        links.new(uv.outputs["UV"], target)
        target = nodes.new("ShaderNodeTexImage")
        target.image = image
        nodes.active = target
        view_layer = scene.view_layers[0]
        plane.select_set(True, view_layer=view_layer)
        view_layer.objects.active = plane
        for from_node, from_socket, _links in plan.outputs.values():
            # Outputs are looked up by name; nodes like Mix have several
            # "Result" outputs, so match the identifier.
            source = next(
                socket for socket in nodes[from_node.name].outputs
                if socket.identifier == from_socket.identifier
            )
            links.new(source, emission.inputs["Color"])
            with context.temp_override(
                scene=scene, view_layer=view_layer, active_object=plane, object=plane,
                selected_objects=[plane], selected_editable_objects=[plane],
            ):
                result = bpy.ops.object.bake(type='EMIT', margin=0, use_clear=True, target='IMAGE_TEXTURES')
            if 'FINISHED' not in result:
                raise FlattenError("Blender did not finish the bake")
            pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
            image.pixels.foreach_get(pixels)
            results.append(pixels.reshape(height, width, 4)[..., :3].copy())
        return results
    finally:
        bpy.data.images.remove(image)
        bpy.data.scenes.remove(scene)
        bpy.data.objects.remove(plane)
        bpy.data.meshes.remove(mesh)
        bpy.data.materials.remove(copy)


def _save_png(rgb, path, colour):
    """8-bit PNG: sRGB-encoded for colour, raw values for data. Returns the
    share of values that were outside 0-1 and got clipped."""
    clipped = float(numpy.mean((rgb < -1e-4) | (rgb > 1.0 + 1e-4)))
    values = numpy.clip(rgb, 0.0, 1.0)
    if colour:
        values = numpy.where(values <= 0.0031308, values * 12.92, 1.055 * numpy.power(values, 1.0 / 2.4) - 0.055)
    height, width = rgb.shape[:2]
    pixels = numpy.ones((height, width, 4), dtype=numpy.float32)
    pixels[..., :3] = values
    image = bpy.data.images.new(f"__PMVR_FLATTEN_SAVE_{uuid.uuid4().hex[:8]}", width, height, alpha=False)
    try:
        image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw = path
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    return clipped


def plan_selection(material, selected):
    if any(node.type == 'OUTPUT_MATERIAL' for node in selected):
        raise FlattenError("Deselect the Material Output")
    return Plan(material.node_tree, selected)


def flatten_nodes(context, material, selected, long_side=0, colour_space='AUTO'):
    """Replace the selected nodes of material with baked Image Texture
    nodes, long_side px on the longer side (0: as the largest image in the
    selection, 1024 without one), in that image's aspect. Returns (created
    file descriptions, notes); raises FlattenError before anything changes."""
    tree = material.node_tree
    plan = plan_selection(material, selected)
    source = plan.dimensions
    target = output_size(source, long_side or (max(source) if source else 1024))
    # Smaller than the source: render every source texel, then average down
    # (sampling fewer points would skip texels and alias).
    render = source if source and target[0] <= source[0] and target[1] <= source[1] else target
    # Everything the new nodes need is read before the tree changes.
    jobs = []
    for _from_node, _from_socket, links in plan.outputs.values():
        colour = _destination_is_colour(links) if colour_space == 'AUTO' else colour_space == 'SRGB'
        jobs.append((colour, _label(material, links), _destinations(links)[0].to_socket.name,
                     [link.to_socket for link in links]))
    coordinate = None
    if plan.coordinate:
        source_node, identifier = plan.coordinate
        coordinate = next(s for s in tree.nodes[source_node].outputs if s.identifier == identifier)
    location = max((node.location.copy() for node in selected), key=lambda co: co.x)
    taken = set()
    paths = [_output_path(label, taken) for _colour, label, _name, _targets in jobs]
    baked = _bake_outputs(context, material, plan, *render)
    if render != target:
        baked = [numpy.stack(scaled_linear([rgb[..., c] for c in range(3)], *target), axis=-1) for rgb in baked]
    created, notes = [], []
    for index, ((colour, _label_text, socket_name, targets), rgb, path) in enumerate(zip(jobs, baked, paths)):
        clipped = _save_png(rgb, path, colour)
        if clipped > 0.001:
            notes.append(f"{os.path.basename(path)}: {clipped:.1%} of values outside 0-1 clipped")
        image = bpy.data.images.load(path, check_existing=False)
        image.filepath = _relative(path)
        image.colorspace_settings.name = 'sRGB' if colour else 'Non-Color'
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        node.label = f"Flattened {socket_name}"
        node.location = (location.x, location.y - index * 300)
        if plan.template:
            node.interpolation = plan.template.interpolation
            node.extension = plan.template.extension
        if coordinate:
            tree.links.new(coordinate, node.inputs["Vector"])
        elif plan.uv_map:
            uv = tree.nodes.new("ShaderNodeUVMap")
            uv.uv_map = plan.uv_map
            uv.location = (node.location.x - 220, node.location.y)
            tree.links.new(uv.outputs["UV"], node.inputs["Vector"])
        for socket in targets:
            tree.links.new(node.outputs["Color"], socket)
        created.append(f"{os.path.basename(path)} ({target[0]} x {target[1]}, {'sRGB' if colour else 'Non-Color'})")
    for node in selected:
        tree.nodes.remove(node)
    return created, notes


class PMVR_OT_FlattenNodes(bpy.types.Operator):
    bl_idname = "pmvr.flatten_nodes"
    bl_label = "Flatten to Texture"
    bl_description = (
        "Render the selected nodes into one image and replace them with an "
        "Image Texture node, so USD gets exactly what the material shows. "
        "Coordinates outside the selection stay connected"
    )
    bl_options = {'REGISTER', 'UNDO'}

    long_side: bpy.props.IntProperty(
        name="Long Side",
        description=(
            "Pixels on the longer side of the new texture; the other side keeps "
            "the aspect ratio. Below the original the texels are averaged down"
        ),
        default=1024,
        min=16,
        soft_max=8192,
    )
    source_width: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    source_height: bpy.props.IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    colour_space: bpy.props.EnumProperty(
        name="Colour",
        items=(
            ('AUTO', "Auto", "sRGB into colour inputs, Non-Color into values and normal maps"),
            ('SRGB', "sRGB", ""),
            ('DATA', "Non-Color", ""),
        ),
        default='AUTO',
    )

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return bool(
            space and space.type == 'NODE_EDITOR' and space.tree_type == 'ShaderNodeTree'
            and isinstance(space.id, bpy.types.Material) and space.id.node_tree
            and any(node.select for node in space.id.node_tree.nodes)
        )

    @staticmethod
    def _selection(context):
        space = context.space_data
        material = space.id
        if space.edit_tree != material.node_tree:
            raise FlattenError("Leave the node group first (Tab): flatten works on the material's top level")
        return material, [node for node in material.node_tree.nodes if node.select and node.type != 'FRAME']

    def invoke(self, context, _event):
        try:
            material, selected = self._selection(context)
            plan = plan_selection(material, selected)
        except FlattenError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.source_width, self.source_height = plan.dimensions or (0, 0)
        self.long_side = max(plan.dimensions) if plan.dimensions else 1024
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "long_side")
        source = (self.source_width, self.source_height) if self.source_width else None
        width, height = output_size(source, self.long_side)
        layout.label(
            text=(f"Original {source[0]} x {source[1]}  ->  " if source else "No image in the selection  ->  ")
            + f"{width} x {height}"
        )
        layout.prop(self, "colour_space")

    def execute(self, context):
        try:
            material, selected = self._selection(context)
        except FlattenError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        count = len(selected)
        try:
            created, notes = flatten_nodes(context, material, selected, self.long_side, self.colour_space)
        except FlattenError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        message = f"Flattened {count} node(s) into {', '.join(created)}"
        self.report({'WARNING'} if notes else {'INFO'}, message + ("; " + "; ".join(notes) if notes else ""))
        return {'FINISHED'}


def _draw_menu(self, context):
    if PMVR_OT_FlattenNodes.poll(context):
        self.layout.separator()
        self.layout.operator(PMVR_OT_FlattenNodes.bl_idname, icon='TEXTURE')


CLASSES = (PMVR_OT_FlattenNodes,)
MENUS = ("NODE_MT_context_menu", "NODE_MT_node")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu:
            menu.append(_draw_menu)


def unregister():
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu:
            menu.remove(_draw_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
