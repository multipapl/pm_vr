"""Generated objects and materials: ownership, processors, state binding and commit."""

import bpy

from ..lightmap_baker.material import add_lightmap_nodes
from .bake_scene import (
    PipelineBakeError,
    ensure_scene_collection,
    make_fallback_material,
    pipeline_collection,
    principled_nodes,
)
from .constants import (
    BAKE_UV_NAME,
    GENERATED_COLLECTION,
    PRIMARY_UV_NAME,
    SCHEMA_VERSION,
    TAG_GENERATED,
    TAG_LAYER_ID,
    TAG_LAYER_TYPE,
    TAG_MATERIAL_SLOT,
    TAG_MODE,
    TAG_NAME,
    TAG_SCHEMA,
    TAG_SOURCE_ID,
    TAG_STATE,
    TAG_UNIT_ID,
    UNLIT_LAYER_TYPES,
)
from .identity import claim_identity, safe_stem


def live_generated_objects(unit_id, mode='BEAUTY'):
    return [
        obj for obj in bpy.data.objects
        if obj.get(TAG_GENERATED)
        and obj.get(TAG_UNIT_ID) == unit_id
        and obj.get(TAG_MODE) == mode
    ]


def find_generated(unit_id, source_id, mode='BEAUTY'):
    return next((
        obj for obj in live_generated_objects(unit_id, mode)
        if obj.get(TAG_SOURCE_ID) == source_id
    ), None)


def _new_uv_and_image_nodes(material, image, principled):
    tree = material.node_tree
    primary_uv = tree.nodes.new("ShaderNodeUVMap")
    primary_uv.name = "PMVR UVMap"
    primary_uv.uv_map = PRIMARY_UV_NAME
    baked_uv = tree.nodes.new("ShaderNodeUVMap")
    baked_uv.name = "PMVR SimpleBake"
    baked_uv.uv_map = BAKE_UV_NAME
    baked_image = tree.nodes.new("ShaderNodeTexImage")
    baked_image.name = "PMVR Baked Beauty"
    baked_image.image = image
    baked_image.interpolation = 'Linear'
    tree.links.new(baked_uv.outputs["UV"], baked_image.inputs["Vector"])
    base = principled.inputs.get("Base Color")
    if not base:
        raise PipelineBakeError("Principled BSDF has no Base Color input")
    for link in list(base.links):
        tree.links.remove(link)
    tree.links.new(baked_image.outputs["Color"], base)
    for node in tree.nodes:
        if node.type == 'TEX_IMAGE' and node != baked_image:
            vector = node.inputs.get("Vector")
            if vector:
                for link in list(vector.links):
                    tree.links.remove(link)
                tree.links.new(primary_uv.outputs["UV"], vector)


def _prune_unreachable_material_nodes(material):
    tree = material.node_tree
    outputs = [
        node for node in tree.nodes
        if node.type == 'OUTPUT_MATERIAL' and node.is_active_output
    ]
    if not outputs:
        return
    reachable = {node.as_pointer() for node in outputs}
    stack = [link.from_node for output in outputs for socket in output.inputs for link in socket.links]
    while stack:
        node = stack.pop()
        pointer = node.as_pointer()
        if pointer in reachable:
            continue
        reachable.add(pointer)
        stack.extend(link.from_node for socket in node.inputs for link in socket.links)
    for node in list(tree.nodes):
        if node.as_pointer() not in reachable:
            tree.nodes.remove(node)


def _tag_material(material, unit, source_id, layer, state, slot, mode='BEAUTY'):
    material[TAG_NAME] = material.name.rsplit(".", 1)[0] if _has_number_suffix(material.name) else material.name
    material[TAG_GENERATED] = True
    material[TAG_UNIT_ID] = unit.unit_id
    material[TAG_SOURCE_ID] = source_id
    material[TAG_LAYER_ID] = layer.layer_id
    material[TAG_LAYER_TYPE] = layer.layer_type
    material[TAG_MODE] = mode
    material[TAG_STATE] = state
    material[TAG_MATERIAL_SLOT] = slot
    material[TAG_SCHEMA] = SCHEMA_VERSION


def _has_number_suffix(name):
    head, _dot, tail = name.rpartition(".")
    return bool(head) and len(tail) == 3 and tail.isdigit()


def tag_image(image, unit, layer, state, mode):
    image[TAG_GENERATED] = True
    image[TAG_UNIT_ID] = unit.unit_id
    image[TAG_LAYER_ID] = layer.layer_id
    image[TAG_LAYER_TYPE] = layer.layer_type
    image[TAG_MODE] = mode
    image[TAG_STATE] = state
    image[TAG_SCHEMA] = SCHEMA_VERSION


def _scene_material(unit, layer, state, image):
    material = bpy.data.materials.new(
        f"PMVR_{safe_stem(unit.display_name)}_{state}_{unit.unit_id[:8]}"
    )
    material.use_nodes = True
    principled = principled_nodes(material)[0]
    principled.inputs["Metallic"].default_value = 0.0
    _new_uv_and_image_nodes(material, image, principled)
    # Unlit and Translucent members share one atlas and one unit material for the
    # whole bake unit, including units containing several source objects.
    _tag_material(material, unit, "", layer, state, 0)
    return [material]


def _copied_materials(unit, source, layer, state, image):
    results = []
    source_materials = [slot.material for slot in source.material_slots]
    if not source_materials:
        source_materials = [None]
    try:
        for slot, original in enumerate(source_materials):
            material = original.copy() if original else make_fallback_material(f"PMVR_{source.name}_{slot}")
            results.append(material)
            material.name = f"PMVR_{safe_stem(source.name)}_{state}_{slot}_{unit.unit_id[:8]}"
            principled = principled_nodes(material)
            if len(principled) != 1:
                raise PipelineBakeError(f'{source.name}: material slot {slot} must resolve to exactly one Principled BSDF')
            if layer.layer_type == 'ALPHA':
                alpha = principled[0].inputs.get("Alpha")
                if not alpha or (not alpha.is_linked and alpha.default_value >= 1.0):
                    raise PipelineBakeError(f'{source.name}: Alpha material slot {slot} has no Alpha branch/value')
                base = principled[0].inputs.get("Base Color")
                for socket in principled[0].inputs:
                    if socket != alpha and socket != base:
                        for link in list(socket.links):
                            material.node_tree.links.remove(link)
            _new_uv_and_image_nodes(material, image, principled[0])
            if layer.layer_type == 'ALPHA':
                _prune_unreachable_material_nodes(material)
                try:
                    material.surface_render_method = 'DITHERED'
                except (AttributeError, TypeError, ValueError):
                    pass
            _tag_material(material, unit, source.pm_vr_pipeline.source_id, layer, state, slot)
        return results
    except Exception:
        discard_unused_materials(results)
        raise


def _state_materials(unit_id, source_id, state, mode='BEAUTY'):
    materials = [
        material for material in bpy.data.materials
        if material.get(TAG_GENERATED)
        and material.get(TAG_UNIT_ID) == unit_id
        and material.get(TAG_SOURCE_ID) == source_id
        and material.get(TAG_MODE) == mode
        and material.get(TAG_STATE) == state
    ]
    if not materials and mode == 'BEAUTY':
        materials = [
            material for material in bpy.data.materials
            if material.get(TAG_GENERATED)
            and material.get(TAG_UNIT_ID) == unit_id
            and not material.get(TAG_SOURCE_ID)
            and material.get(TAG_MODE) == mode
            and material.get(TAG_STATE) == state
        ]
    return sorted(materials, key=lambda item: int(item.get(TAG_MATERIAL_SLOT, 0)))


def discard_unused_materials(materials):
    for material in materials:
        if material.users == 0:
            bpy.data.materials.remove(material)


def protect_generated_material(material):
    # A canonical generated object shows one state at a time. The other state's
    # material has no object user and Blender would drop it on save.
    if material.get(TAG_GENERATED) and not material.use_fake_user:
        material.use_fake_user = True


def release_generated_material(material):
    """Remove a PM VR-owned material once nothing but its fake user holds it."""
    try:
        if material.get(TAG_GENERATED):
            material.use_fake_user = False
        if material.users == 0:
            bpy.data.materials.remove(material)
    except ReferenceError:
        pass


def protect_all_generated_materials():
    for material in bpy.data.materials:
        protect_generated_material(material)


def _assign_materials_in_place(mesh, materials):
    # Assignment by index preserves polygon.material_index; clear() would
    # silently collapse every polygon to slot 0.
    if len(mesh.materials) != len(materials):
        raise PipelineBakeError(
            f"generated mesh has {len(mesh.materials)} material slot(s) "
            f"but {len(materials)} state material(s) exist"
        )
    for index, material in enumerate(materials):
        if mesh.materials[index] != material:
            mesh.materials[index] = material


def bind_generated_state(unit, state, mode='BEAUTY', strict=False):
    """Show one state's generated materials; strict mode refuses partial binds."""
    bound = 0
    for obj in live_generated_objects(unit.unit_id, mode):
        source_id = obj.get(TAG_SOURCE_ID, "")
        materials = _state_materials(unit.unit_id, source_id, state, mode)
        try:
            if not materials:
                raise PipelineBakeError(
                    f"{state.title()} {mode.title()} materials are missing; "
                    f"rebake {state.title()}"
                )
            _assign_materials_in_place(obj.data, materials)
        except PipelineBakeError as exc:
            if strict:
                raise PipelineBakeError(f"{obj.name}: {exc}") from exc
            continue
        obj[TAG_STATE] = state
        bound += 1
    return bound


def snapshot_generated_bindings(mode='BEAUTY'):
    return [
        (obj, list(obj.data.materials), obj.get(TAG_STATE))
        for obj in bpy.data.objects
        if obj.get(TAG_GENERATED) and obj.get(TAG_MODE) == mode and obj.type == 'MESH'
    ]


def restore_generated_bindings(snapshot):
    for obj, materials, state in snapshot:
        try:
            _assign_materials_in_place(obj.data, materials)
            if state is not None:
                obj[TAG_STATE] = state
        except (PipelineBakeError, ReferenceError):
            pass


def _previous_signature(unit, mode):
    return (
        (unit.day_signature or unit.evening_signature)
        if mode == 'BEAUTY'
        else (unit.day_lightmap_signature or unit.evening_lightmap_signature)
    )


def check_commit(unit, layer, receivers, staged, signature, mode='BEAUTY'):
    """Reject an uncommittable result before any artifact is replaced."""
    previous_signature = _previous_signature(unit, mode)
    compatible = not previous_signature or previous_signature == signature
    collapse_to_one = mode == 'BEAUTY' and layer.layer_type in UNLIT_LAYER_TYPES
    for receiver in receivers:
        source = receiver["source"]
        source_id = source.pm_vr_pipeline.source_id
        materials = staged.get(source_id)
        if not materials:
            raise PipelineBakeError(f'{source.name}: no output materials were prepared')
        generated = find_generated(unit.unit_id, source_id, mode)
        mesh = generated.data if generated and compatible else receiver["mesh"]
        if not collapse_to_one and len(mesh.materials) != len(materials):
            raise PipelineBakeError(
                f'{source.name}: evaluated mesh has {len(mesh.materials)} '
                f'material slot(s) but the source object has {len(materials)}; '
                f'a modifier probably adds or removes materials'
            )


def remove_generated_object(obj):
    """Delete a generated object without shifting generated children."""
    for child in list(obj.children):
        world = child.matrix_world.copy()
        child.parent = None
        child.matrix_world = world
    mesh = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _generated_parent(source, unit, layer, generated_by_source, mode):
    """Mirror the source parent inside the same render layer."""
    parent = source.parent
    if not parent or not hasattr(parent, "pm_vr_pipeline"):
        return None
    parent_meta = parent.pm_vr_pipeline
    if parent_meta.processing_role == 'BAKE' and parent_meta.bake_unit_id == unit.unit_id:
        return generated_by_source.get(parent_meta.source_id)
    if parent_meta.render_layer_id != layer.layer_id:
        return None
    if parent_meta.processing_role == 'BAKE':
        # Baked in another unit of this layer; linked once that unit exists.
        return find_generated(parent_meta.bake_unit_id, parent_meta.source_id, mode)
    if parent_meta.processing_role == 'EXPORT_ORIGINAL':
        return parent
    return None


def _relink_generated_children(unit, layer, generated_by_source, mode):
    """Attach generated children from other units that were baked earlier."""
    parents_by_source_object = {}
    for obj in bpy.data.objects:
        metadata = getattr(obj, "pm_vr_pipeline", None)
        if (
            metadata
            and metadata.is_registered_source
            and metadata.processing_role == 'BAKE'
            and metadata.bake_unit_id == unit.unit_id
        ):
            target = generated_by_source.get(metadata.source_id)
            if target:
                parents_by_source_object[obj.as_pointer()] = target
    if not parents_by_source_object:
        return
    for child_source in bpy.data.objects:
        parent = child_source.parent
        if not parent or parent.as_pointer() not in parents_by_source_object:
            continue
        metadata = getattr(child_source, "pm_vr_pipeline", None)
        if (
            not metadata
            or metadata.processing_role != 'BAKE'
            or metadata.bake_unit_id == unit.unit_id
            or metadata.render_layer_id != layer.layer_id
        ):
            continue
        child = find_generated(metadata.bake_unit_id, metadata.source_id, mode)
        target = parents_by_source_object[parent.as_pointer()]
        if not child or child.parent == target:
            continue
        world = child.matrix_world.copy()
        child.parent = target
        child.parent_type = child_source.parent_type
        child.parent_bone = child_source.parent_bone if child_source.parent_type == 'BONE' else ""
        child.matrix_world = world


def commit_generated_geometry(context, unit, layer, receivers, signature, mode='BEAUTY'):
    generated_collection = pipeline_collection(GENERATED_COLLECTION)
    ensure_scene_collection(context.scene, generated_collection)
    previous_signature = _previous_signature(unit, mode)
    compatible = not previous_signature or previous_signature == signature
    for receiver in receivers:
        source = receiver["source"]
        source_id = source.pm_vr_pipeline.source_id
        generated = find_generated(unit.unit_id, source_id, mode)
        if generated and compatible:
            continue
        if generated:
            old_mesh = generated.data
            new_mesh = receiver["mesh"].copy()
            new_mesh.name = f"PMVR_{safe_stem(source.name)}_{unit.unit_id[:8]}"
            generated.data = new_mesh
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
        else:
            new_mesh = receiver["mesh"].copy()
            new_mesh.name = f"PMVR_{safe_stem(source.name)}_{unit.unit_id[:8]}"
            generated = bpy.data.objects.new(source.name, new_mesh)
            generated_collection.objects.link(generated)
        generated.matrix_world = source.matrix_world.copy()
        generated[TAG_GENERATED] = True
        generated[TAG_SOURCE_ID] = source_id
        generated[TAG_UNIT_ID] = unit.unit_id
        generated[TAG_LAYER_ID] = layer.layer_id
        generated[TAG_LAYER_TYPE] = layer.layer_type
        generated[TAG_MODE] = mode
        generated[TAG_SCHEMA] = SCHEMA_VERSION
        claim_identity(generated)
        generated.hide_render = False
        generated.hide_set(False)
    live_source_ids = {receiver["source"].pm_vr_pipeline.source_id for receiver in receivers}
    for generated in live_generated_objects(unit.unit_id, mode):
        stale_source_id = generated.get(TAG_SOURCE_ID)
        if stale_source_id not in live_source_ids:
            remove_generated_object(generated)
            for material in list(bpy.data.materials):
                if (
                    material.get(TAG_GENERATED)
                    and material.get(TAG_UNIT_ID) == unit.unit_id
                    and material.get(TAG_MODE) == mode
                    and stale_source_id
                    and material.get(TAG_SOURCE_ID) == stale_source_id
                ):
                    release_generated_material(material)
    generated_by_source = {
        obj.get(TAG_SOURCE_ID): obj for obj in live_generated_objects(unit.unit_id, mode)
    }
    for receiver in receivers:
        source = receiver["source"]
        generated = generated_by_source.get(source.pm_vr_pipeline.source_id)
        target_parent = _generated_parent(source, unit, layer, generated_by_source, mode)
        generated.parent = target_parent
        generated.parent_type = source.parent_type if target_parent else 'OBJECT'
        generated.parent_bone = source.parent_bone if target_parent and source.parent_type == 'BONE' else ""
        # Generated geometry lives in the source's object space. Re-derive the
        # world transform from the source so removing or replacing a generated
        # parent above can never shift this object.
        generated.matrix_world = source.matrix_world.copy()
    _relink_generated_children(unit, layer, generated_by_source, mode)
    if mode == 'BEAUTY' and not compatible:
        if unit.day_signature and unit.day_signature != signature:
            unit.day_status = "Structurally incompatible — rebake required"
        if unit.evening_signature and unit.evening_signature != signature:
            unit.evening_status = "Structurally incompatible — rebake required"


def prepare_materials(unit, layer, members, state, image):
    created = []
    staged = {}
    try:
        if layer.layer_type in UNLIT_LAYER_TYPES:
            materials = _scene_material(unit, layer, state, image)
            created.extend(materials)
            for source in members:
                staged[source.pm_vr_pipeline.source_id] = materials
        else:
            for source in members:
                materials = _copied_materials(unit, source, layer, state, image)
                created.extend(materials)
                staged[source.pm_vr_pipeline.source_id] = materials
        return staged, created
    except Exception:
        discard_unused_materials(created)
        raise


def commit_materials(unit, layer, members, state, staged, created, mode='BEAUTY'):
    try:
        collapse_to_one = (
            mode == 'BEAUTY'
            and layer.layer_type in UNLIT_LAYER_TYPES
        )
        for source in members:
            generated = find_generated(unit.unit_id, source.pm_vr_pipeline.source_id, mode)
            if not generated:
                raise PipelineBakeError(f'{source.name}: canonical generated object is missing')
            materials = staged[source.pm_vr_pipeline.source_id]
            if (
                not collapse_to_one
                and len(generated.data.materials) != len(materials)
            ):
                raise PipelineBakeError(
                    f'{source.name}: generated mesh has '
                    f'{len(generated.data.materials)} material slots but '
                    f'{len(materials)} output materials were prepared'
                )
        old_materials = {
            source.pm_vr_pipeline.source_id: _state_materials(unit.unit_id, source.pm_vr_pipeline.source_id, state, mode)
            for source in members
        }
        for source in members:
            generated = find_generated(unit.unit_id, source.pm_vr_pipeline.source_id, mode)
            materials = staged[source.pm_vr_pipeline.source_id]
            if collapse_to_one:
                generated.data.materials.clear()
                for material in materials:
                    generated.data.materials.append(material)
            else:
                # Assignment in place preserves polygon.material_index. Calling
                # clear() here would silently collapse every polygon to slot 0.
                for index, material in enumerate(materials):
                    generated.data.materials[index] = material
            generated[TAG_STATE] = state
            if collapse_to_one:
                for polygon in generated.data.polygons:
                    polygon.material_index = 0
        unique_old = {}
        for materials in old_materials.values():
            for material in materials:
                unique_old.setdefault(material.as_pointer(), material)
        for material in unique_old.values():
            if material not in created:
                release_generated_material(material)
        for material in created:
            protect_generated_material(material)
            # New materials were named while the previous ones still existed
            # (Name.001); take the canonical name back so exports stay stable.
            base_name = material.get(TAG_NAME)
            if base_name and material.name != base_name:
                material.name = base_name
        return created
    except Exception:
        discard_unused_materials(created)
        raise


def prepare_lightmap_materials(unit, layer, members, state, image):
    staged = {}
    created = []
    try:
        for source in members:
            materials = []
            source_materials = [slot.material for slot in source.material_slots] or [None]
            for slot, original in enumerate(source_materials):
                material = original.copy() if original else make_fallback_material(
                    f"PMVR_LM_{source.name}_{slot}"
                )
                created.append(material)
                material.name = f"PMVR_LM_{safe_stem(source.name)}_{state}_{slot}_{unit.unit_id[:8]}"
                add_lightmap_nodes(material, image, source_used_nodes=bool(original and original.use_nodes))
                _tag_material(
                    material,
                    unit,
                    source.pm_vr_pipeline.source_id,
                    layer,
                    state,
                    slot,
                    mode='LIGHTMAP',
                )
                materials.append(material)
            staged[source.pm_vr_pipeline.source_id] = materials
        return staged, created
    except Exception:
        discard_unused_materials(created)
        raise
