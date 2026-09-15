"""Prepare rebaked reflect and alpha materials for the VR export pipeline."""

import bpy


UI_CATEGORY = "VR_PROJECT"
BAKED_SUFFIX = "_Baked"
OUTPUT_SUFFIX = "_M"
PRIMARY_UV_NAME = "UVMap"
BAKED_UV_NAME = "SimpleBake"
TAG_GENERATED = "pm_vr_material_rebuild_generated"
TAG_SOURCE = "pm_vr_material_rebuild_source"
NODE_PRIMARY_UV = "PM VR UVMap"
NODE_BAKED_UV = "PM VR SimpleBake"
NODE_BAKED_IMAGE = "PM VR Baked Base Color"
ALPHA_ONLY_KEYWORDS = ("leaf", "alpha")


class MaterialRebuildError(RuntimeError):
    pass


def get_single_material(obj, role):
    materials = []
    seen = set()
    for slot in obj.material_slots:
        material = slot.material
        if material and material.as_pointer() not in seen:
            seen.add(material.as_pointer())
            materials.append(material)
    if len(materials) != 1:
        raise MaterialRebuildError(
            f"{role} object must use exactly one material; found {len(materials)}"
        )
    return materials[0]


def get_single_principled(material, role):
    if not material.use_nodes or not material.node_tree:
        raise MaterialRebuildError(f"{role} material does not use nodes")
    nodes = [node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']
    if len(nodes) != 1:
        raise MaterialRebuildError(
            f"{role} material must contain exactly one Principled BSDF; found {len(nodes)}"
        )
    return nodes[0]


def upstream_image_nodes(input_socket):
    found = []
    found_keys = set()
    visited_nodes = set()

    def visit_socket(socket):
        for link in socket.links:
            node = link.from_node
            key = node.as_pointer()
            if node.type == 'TEX_IMAGE' and node.image:
                if key not in found_keys:
                    found_keys.add(key)
                    found.append(node)
                continue
            if key in visited_nodes:
                continue
            visited_nodes.add(key)
            for node_input in node.inputs:
                if node_input.is_linked:
                    visit_socket(node_input)

    visit_socket(input_socket)
    return found


def upstream_nodes(input_sockets):
    found = set()

    def visit_socket(socket):
        for link in socket.links:
            node = link.from_node
            key = node.as_pointer()
            if key in found:
                continue
            found.add(key)
            for node_input in node.inputs:
                if node_input.is_linked:
                    visit_socket(node_input)

    for input_socket in input_sockets:
        if input_socket.is_linked:
            visit_socket(input_socket)
    return found


def prune_unreachable_nodes(node_tree):
    outputs = [
        node for node in node_tree.nodes
        if node.type == 'OUTPUT_MATERIAL' and node.is_active_output
    ]
    if not outputs:
        raise MaterialRebuildError("material has no active Material Output")

    reachable_keys = {node.as_pointer() for node in outputs}
    reachable_keys.update(
        upstream_nodes(
            socket
            for output in outputs
            for socket in output.inputs
        )
    )
    for node in list(node_tree.nodes):
        if node.as_pointer() not in reachable_keys:
            node_tree.nodes.remove(node)


def get_baked_image(material):
    principled = get_single_principled(material, "Baked")
    base_color = principled.inputs.get("Base Color")
    if not base_color or not base_color.is_linked:
        raise MaterialRebuildError("baked material Base Color is not linked")
    images = upstream_image_nodes(base_color)
    if len(images) != 1:
        raise MaterialRebuildError(
            f"baked Base Color must resolve to one image texture; found {len(images)}"
        )
    return images[0].image


def copy_required_uv_layers(source_mesh, target_mesh):
    if len(source_mesh.loops) != len(target_mesh.loops):
        raise MaterialRebuildError(
            f"mesh topology differs ({len(source_mesh.loops)} vs {len(target_mesh.loops)} loops)"
        )

    source_layers = []
    for name in (PRIMARY_UV_NAME, BAKED_UV_NAME):
        layer = source_mesh.uv_layers.get(name)
        if not layer:
            raise MaterialRebuildError(f'original mesh is missing UV layer "{name}"')
        source_layers.append((name, layer))

    while target_mesh.uv_layers:
        target_mesh.uv_layers.remove(target_mesh.uv_layers[0])

    for name, source_layer in source_layers:
        target_layer = target_mesh.uv_layers.new(name=name)
        coordinates = [0.0] * (len(source_layer.data) * 2)
        source_layer.data.foreach_get("uv", coordinates)
        target_layer.data.foreach_set("uv", coordinates)

    target_mesh.uv_layers.active = target_mesh.uv_layers[PRIMARY_UV_NAME]
    for layer in target_mesh.uv_layers:
        layer.active_render = layer.name == BAKED_UV_NAME


def configure_material(original_material, baked_image, output_name, alpha_only=False):
    material = original_material.copy()
    material.name = f"{output_name}.__PMVR_NEW__"
    tree = material.node_tree
    principled = get_single_principled(material, "Original")

    base_color = principled.inputs.get("Base Color")
    if not base_color:
        raise MaterialRebuildError("Original Principled BSDF has no Base Color input")
    for link in list(base_color.links):
        tree.links.remove(link)

    if alpha_only:
        alpha_input = principled.inputs.get("Alpha")
        if not alpha_input or not alpha_input.is_linked:
            raise MaterialRebuildError(
                "Leaf/Alpha material must have a texture branch connected to Alpha"
            )
        for socket in principled.inputs:
            if socket != alpha_input:
                for link in list(socket.links):
                    tree.links.remove(link)

    preserved_node_keys = upstream_nodes(principled.inputs)
    for node in list(tree.nodes):
        if (
            node.type == 'TEX_IMAGE'
            and node.image
            and node.as_pointer() not in preserved_node_keys
        ):
            tree.nodes.remove(node)

    existing_image_nodes = [
        node for node in tree.nodes
        if node.type == 'TEX_IMAGE' and node.image
    ]

    if existing_image_nodes:
        primary_uv = tree.nodes.new("ShaderNodeUVMap")
        primary_uv.name = NODE_PRIMARY_UV
        primary_uv.label = PRIMARY_UV_NAME
        primary_uv.uv_map = PRIMARY_UV_NAME

        for image_node in existing_image_nodes:
            vector_input = image_node.inputs.get("Vector")
            if not vector_input:
                continue
            for link in list(vector_input.links):
                tree.links.remove(link)
            tree.links.new(primary_uv.outputs["UV"], vector_input)
        primary_uv.location = (principled.location.x - 900, principled.location.y + 200)

    baked_uv = tree.nodes.new("ShaderNodeUVMap")
    baked_uv.name = NODE_BAKED_UV
    baked_uv.label = BAKED_UV_NAME
    baked_uv.uv_map = BAKED_UV_NAME

    baked_texture = tree.nodes.new("ShaderNodeTexImage")
    baked_texture.name = NODE_BAKED_IMAGE
    baked_texture.label = "Baked Base Color"
    baked_texture.image = baked_image
    baked_texture.interpolation = 'Linear'
    tree.links.new(baked_uv.outputs["UV"], baked_texture.inputs["Vector"])

    tree.links.new(baked_texture.outputs["Color"], base_color)

    baked_uv.location = (principled.location.x - 900, principled.location.y - 350)
    baked_texture.location = (principled.location.x - 600, principled.location.y - 350)
    prune_unreachable_nodes(tree)
    return material


def is_owned(id_block, source_name):
    return bool(
        id_block
        and id_block.get(TAG_GENERATED)
        and id_block.get(TAG_SOURCE) == source_name
    )


def validate_output_names(source_name, output_name):
    collisions = (
        ("object", bpy.data.objects.get(output_name)),
        ("mesh", bpy.data.meshes.get(output_name)),
        ("material", bpy.data.materials.get(output_name)),
    )
    for kind, id_block in collisions:
        if id_block and not is_owned(id_block, source_name):
            raise MaterialRebuildError(
                f'untagged {kind} datablock "{output_name}" already exists'
            )
        if id_block and kind in {"mesh", "material"} and id_block.users > 1:
            raise MaterialRebuildError(
                f'previous generated {kind} "{output_name}" has multiple users'
            )


def remove_previous_output(source_name, output_name):
    old_object = bpy.data.objects.get(output_name)
    old_mesh = bpy.data.meshes.get(output_name)
    old_material = bpy.data.materials.get(output_name)

    if is_owned(old_object, source_name):
        bpy.data.objects.remove(old_object, do_unlink=True)
    if is_owned(old_mesh, source_name) and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    if is_owned(old_material, source_name) and old_material.users == 0:
        bpy.data.materials.remove(old_material)


def cleanup_new_output(obj, mesh, material):
    if obj and obj.name in bpy.data.objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.name in bpy.data.meshes and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    if material and material.name in bpy.data.materials and material.users == 0:
        bpy.data.materials.remove(material)


def rebuild_pair(scene, original, baked):
    source_name = original.name
    output_name = f"{source_name}{OUTPUT_SUFFIX}"
    validate_output_names(source_name, output_name)

    original_material = get_single_material(original, "Original")
    baked_material = get_single_material(baked, "Baked")
    baked_image = get_baked_image(baked_material)

    new_object = None
    new_mesh = None
    new_material = None
    try:
        new_object = baked.copy()
        new_object.name = f"{output_name}.__PMVR_NEW__"
        new_mesh = baked.data.copy()
        new_mesh.name = f"{output_name}.__PMVR_NEW__"
        new_object.data = new_mesh

        copy_required_uv_layers(original.data, new_mesh)
        alpha_only = any(keyword in source_name.casefold() for keyword in ALPHA_ONLY_KEYWORDS)
        new_material = configure_material(
            original_material,
            baked_image,
            output_name,
            alpha_only=alpha_only,
        )
        new_mesh.materials.clear()
        new_mesh.materials.append(new_material)

        for id_block in (new_object, new_mesh, new_material):
            id_block[TAG_GENERATED] = True
            id_block[TAG_SOURCE] = source_name

        scene.collection.objects.link(new_object)
        new_object.select_set(False)
        remove_previous_output(source_name, output_name)
        new_object.name = output_name
        new_mesh.name = output_name
        new_material.name = output_name
        return new_object
    except Exception:
        cleanup_new_output(new_object, new_mesh, new_material)
        raise


def selected_pairs(context):
    selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
    by_name = {obj.name: obj for obj in selected}
    pairs = []
    unmatched = []
    for baked in selected:
        if not baked.name.endswith(BAKED_SUFFIX):
            continue
        original_name = baked.name[:-len(BAKED_SUFFIX)]
        original = by_name.get(original_name)
        if original:
            pairs.append((original, baked))
        else:
            unmatched.append(baked.name)
    return pairs, unmatched


class PMVR_OT_RebuildBakedMaterials(bpy.types.Operator):
    bl_idname = "pm_vr.rebuild_baked_materials"
    bl_label = "Prepare Baked Material Pairs"
    bl_description = (
        "Pair selected Original and Original_Baked meshes, restore UVMap and SimpleBake, "
        "then create protected Original_M objects with baked Base Color and original PBR textures"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len([obj for obj in context.selected_objects if obj.type == 'MESH']) >= 2

    def execute(self, context):
        pairs, unmatched = selected_pairs(context)
        if not pairs:
            self.report({'ERROR'}, 'Select matching "Name" and "Name_Baked" mesh objects')
            return {'CANCELLED'}

        succeeded = []
        failed = []
        for original, baked in pairs:
            try:
                succeeded.append(rebuild_pair(context.scene, original, baked))
            except Exception as exc:
                failed.append((baked.name, str(exc)))
                print(f'[PM VR][Material Rebuild] Failed "{baked.name}": {exc}')

        for name in unmatched:
            print(f'[PM VR][Material Rebuild] Skipped "{name}": matching original not selected')

        skipped_count = len(failed) + len(unmatched)
        if skipped_count:
            self.report(
                {'WARNING'},
                f"Prepared {len(succeeded)} pair(s), skipped {skipped_count}; see the console",
            )
        else:
            self.report({'INFO'}, f"Prepared {len(succeeded)} baked material pair(s)")
        return {'FINISHED'} if succeeded else {'CANCELLED'}


def draw_ui(layout, _context):
    box = layout.box()
    box.label(text="Baked PBR Materials", icon='MATERIAL')
    box.operator(PMVR_OT_RebuildBakedMaterials.bl_idname, icon='NODE_MATERIAL')


CLASSES = (PMVR_OT_RebuildBakedMaterials,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
