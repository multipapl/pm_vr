"""Transactional multi-object Beauty baker for semantic bake units."""

from array import array
from datetime import datetime
import hashlib
import os
import time
import uuid

import bpy

from ..lightmap_baker.compositor import denoise_external_beauty, denoise_image
from ..lightmap_baker.images import StagedExport, create_float_image, remove_image, save_linear_exr
from ..lightmap_baker.material import add_lightmap_nodes
from ..lightmap_baker.progress import BakeProgressFeedback
from ..lightmap_baker.receiver import _wrap_surface_for_bake
from ..lightmap_baker.state import ContextState
from .constants import (
    BAKE_UV_NAME,
    GENERATED_COLLECTION,
    PRIMARY_UV_NAME,
    SCHEMA_VERSION,
    TAG_GENERATED,
    TAG_LAYER_ID,
    TAG_MATERIAL_SLOT,
    TAG_MODE,
    TAG_SCHEMA,
    TAG_LAYER_TYPE,
    TAG_SOURCE_ID,
    TAG_STATE,
    TAG_UNIT_ID,
    UNLIT_LAYER_TYPES,
    WORK_COLLECTION,
)
from .identity import find_layer, find_unit, safe_stem, unit_members
from . import log, viewport_overlay
from .state import activate_state
from .validation import object_render_visible, validate_unit


class PipelineBakeError(RuntimeError):
    pass


class PipelineBakeCancelled(PipelineBakeError):
    pass


def _switch_viewports_to_wireframe(context):
    snapshot = []
    window_manager = getattr(context, "window_manager", None)
    for window in getattr(window_manager, "windows", []):
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            try:
                snapshot.append((space, space.shading.type))
                space.shading.type = 'WIREFRAME'
            except (AttributeError, ReferenceError, TypeError, ValueError):
                pass
    return snapshot


def _restore_viewport_shading(snapshot):
    for space, shading_type in snapshot or []:
        try:
            space.shading.type = shading_type
        except (AttributeError, ReferenceError, TypeError, ValueError):
            pass


def _collection(name):
    collection = bpy.data.collections.get(name)
    if not collection:
        collection = bpy.data.collections.new(name)
    if name == WORK_COLLECTION:
        collection["pmvr_temporary_work"] = True
    return collection


def _ensure_scene_collection(scene, collection):
    if collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)


def _clear_collection(collection):
    for obj in list(collection.objects):
        mesh = obj.data if obj.type == 'MESH' else None
        materials = [slot.material for slot in obj.material_slots if slot.material]
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            if material.users == 0:
                bpy.data.materials.remove(material)


def _remove_work_collection(collection):
    if not collection:
        return
    if (
        collection.get("pmvr_temporary_work")
        and bpy.data.collections.get(collection.name) is collection
    ):
        _clear_collection(collection)
        bpy.data.collections.remove(collection)


def _live_generated_objects(unit_id, mode='BEAUTY'):
    return [
        obj for obj in bpy.data.objects
        if obj.get(TAG_GENERATED)
        and obj.get(TAG_UNIT_ID) == unit_id
        and obj.get(TAG_MODE) == mode
    ]


def _find_generated(unit_id, source_id, mode='BEAUTY'):
    return next((
        obj for obj in _live_generated_objects(unit_id, mode)
        if obj.get(TAG_SOURCE_ID) == source_id
    ), None)


def _source_root_objects(project):
    root = project.source_root_collection
    return set(root.all_objects) if root else set()


def _layer_collections_for(root, collection):
    matches = []
    if root.collection == collection:
        matches.append(root)
    for child in root.children:
        matches.extend(_layer_collections_for(child, collection))
    return matches


class EvaluationSnapshot:
    """Restore render visibility, samples and generated visibility after a unit."""

    def __init__(self, context):
        self.scene = context.scene
        self.hide_render = [(obj, obj.hide_render) for obj in context.scene.objects]
        self.samples = getattr(context.scene.cycles, "samples", None) if hasattr(context.scene, "cycles") else None
        generated = bpy.data.collections.get(GENERATED_COLLECTION)
        self.generated_layer_states = [
            (layer_collection, layer_collection.exclude)
            for layer_collection in (
                _layer_collections_for(context.view_layer.layer_collection, generated)
                if generated else []
            )
        ]

    def isolate_source_root(self, project):
        # Exclusion removes generated results from View Layer evaluation as
        # well as rendering. Object hide_render remains a defensive fallback
        # for generated objects linked through another collection path.
        for layer_collection, _exclude in self.generated_layer_states:
            layer_collection.exclude = True
        allowed = _source_root_objects(project)
        for obj, _value in self.hide_render:
            if obj.get(TAG_GENERATED) or obj not in allowed:
                obj.hide_render = True

    def restore(self):
        for obj, value in self.hide_render:
            try:
                obj.hide_render = value
            except ReferenceError:
                pass
        if self.samples is not None:
            try:
                self.scene.cycles.samples = self.samples
            except (AttributeError, TypeError):
                pass
        for layer_collection, exclude in self.generated_layer_states:
            try:
                layer_collection.exclude = exclude
            except (AttributeError, ReferenceError):
                pass


class BakeConfigurationSnapshot:
    ATTRIBUTES = (
        "target", "use_clear", "margin", "margin_type", "use_selected_to_active",
        "view_from",
        "use_pass_direct", "use_pass_indirect", "use_pass_color",
        "use_pass_diffuse", "use_pass_glossy", "use_pass_transmission", "use_pass_emit",
    )

    def __init__(self, scene):
        self.scene = scene
        self.engine = scene.render.engine
        self.bake = scene.render.bake
        self.values = {name: getattr(self.bake, name) for name in self.ATTRIBUTES if hasattr(self.bake, name)}
        self.bake_type = getattr(scene.cycles, "bake_type", None) if hasattr(scene, "cycles") else None

    def configure(self, bake_type, margin, clear, pass_filter=None):
        self.scene.render.engine = 'CYCLES'
        values = {
            "target": 'IMAGE_TEXTURES',
            "use_clear": clear,
            "margin": margin,
            "margin_type": 'ADJACENT_FACES',
            "use_selected_to_active": False,
            "view_from": 'ABOVE_SURFACE',
        }
        if pass_filter is not None:
            values.update({
                "use_pass_direct": 'DIRECT' in pass_filter,
                "use_pass_indirect": 'INDIRECT' in pass_filter,
                "use_pass_color": 'COLOR' in pass_filter,
                "use_pass_diffuse": 'DIFFUSE' in pass_filter,
                "use_pass_glossy": 'GLOSSY' in pass_filter,
                "use_pass_transmission": 'TRANSMISSION' in pass_filter,
                "use_pass_emit": 'EMIT' in pass_filter,
            })
        for name, value in values.items():
            if hasattr(self.bake, name):
                try:
                    setattr(self.bake, name, value)
                except (TypeError, ValueError):
                    if name == "margin_type":
                        self.bake.margin_type = 'EXTEND'
        if self.bake_type is not None:
            self.scene.cycles.bake_type = bake_type

    def restore(self):
        self.scene.render.engine = self.engine
        for name, value in self.values.items():
            try:
                setattr(self.bake, name, value)
            except (TypeError, ValueError, AttributeError):
                pass
        if self.bake_type is not None:
            self.scene.cycles.bake_type = self.bake_type


def _principled_nodes(material):
    if not material or not material.use_nodes or not material.node_tree:
        return []
    return [node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']


def _make_fallback_material(name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    return material


def _add_bake_target(material, image):
    if not material.use_nodes:
        material.use_nodes = True
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node.name = f"PMVR Bake Target {uuid.uuid4().hex}"
    node.label = "PMVR Shared Bake Target"
    node.image = image
    node.interpolation = 'Linear'
    # Match SimpleBake: Cycles must see one unambiguous selected/active image
    # node in every material used by the receiver.
    for existing in tree.nodes:
        existing.select = False
    tree.nodes.active = node
    node.select = True
    return node


def _set_target_image(receiver, image):
    for node in receiver["target_nodes"]:
        node.image = image
        node.id_data.nodes.active = node


def _copy_receiver(context, source, work_collection, image, receiver_type):
    source_uvs = source.data.uv_layers
    primary_source_uv = source_uvs.get(PRIMARY_UV_NAME)
    if not primary_source_uv:
        raise PipelineBakeError(
            f'{source.name}: source UV map "{PRIMARY_UV_NAME}" is missing'
        )
    # Materials must always sample their original textures through UVMap.
    # The SimpleBake channel is only the destination layout for Cycles bake.
    source_uvs.active = primary_source_uv
    for source_uv in source_uvs:
        source_uv.active_render = source_uv == primary_source_uv

    depsgraph = context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
    mesh.name = f"__PMVR_WORK_MESH_{uuid.uuid4().hex}"
    obj = bpy.data.objects.new(f"__PMVR_WORK_{uuid.uuid4().hex}", mesh)
    obj.matrix_world = source.matrix_world.copy()
    work_collection.objects.link(obj)
    target_nodes = []
    # new_from_object() preserves evaluated polygon material indices. Never
    # clear this collection: Blender immediately remaps every polygon to slot
    # zero, and appending the slots again cannot recover those assignments.
    evaluated_materials = list(mesh.materials)
    if not evaluated_materials:
        evaluated_materials = [None]
    for index, source_material in enumerate(evaluated_materials):
        material = source_material.copy() if source_material else _make_fallback_material(f"__PMVR_WORK_MAT_{uuid.uuid4().hex}")
        material.name = f"__PMVR_WORK_MAT_{uuid.uuid4().hex}"
        if receiver_type == 'LIGHTMAP':
            _wrap_surface_for_bake(material)
        if receiver_type == 'PBR':
            for principled in _principled_nodes(material):
                metallic = principled.inputs.get("Metallic")
                if metallic:
                    for link in list(metallic.links):
                        material.node_tree.links.remove(link)
                    metallic.default_value = 0.0
        target_nodes.append(_add_bake_target(material, image))
        if index < len(mesh.materials):
            mesh.materials[index] = material
        else:
            mesh.materials.append(material)
    used_material_indices = sorted({polygon.material_index for polygon in mesh.polygons})
    invalid_material_indices = [
        index for index in used_material_indices if index >= len(mesh.materials)
    ]
    if invalid_material_indices:
        raise PipelineBakeError(
            f"{source.name}: evaluated mesh uses invalid material slots "
            f"{invalid_material_indices}"
        )
    if len(mesh.materials) > 1:
        log.info(
            "Beauty",
            f'{source.name}: prepared {len(mesh.materials)} material slot(s); '
            f'polygon slots {used_material_indices}',
        )
    primary_uv = mesh.uv_layers.get(PRIMARY_UV_NAME)
    bake_uv = mesh.uv_layers.get(BAKE_UV_NAME)
    if not bake_uv:
        raise PipelineBakeError(f'{source.name}: evaluated mesh is missing "{BAKE_UV_NAME}"')
    if not primary_uv:
        raise PipelineBakeError(f'{source.name}: evaluated mesh is missing "{PRIMARY_UV_NAME}"')
    mesh.uv_layers.active = bake_uv
    for mesh_uv in mesh.uv_layers:
        mesh_uv.active_render = mesh_uv == primary_uv
    obj.hide_render = False
    obj.hide_viewport = False
    return {"source": source, "object": obj, "mesh": mesh, "target_nodes": target_nodes}


def _select_only(context, obj):
    for selected in list(context.selected_objects):
        selected.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _supported_bake_kwargs(kwargs):
    try:
        supported = {prop.identifier for prop in bpy.ops.object.bake.get_rna_type().properties}
        return {key: value for key, value in kwargs.items() if key in supported}
    except Exception:
        return kwargs


def _bake_receivers(
    context,
    receivers,
    image,
    bake_type,
    margin,
    pass_filter=None,
    progress_callback=None,
):
    config = BakeConfigurationSnapshot(context.scene)
    try:
        for index, receiver in enumerate(receivers):
            if progress_callback:
                progress_callback(index, receiver)
            _set_target_image(receiver, image)
            _select_only(context, receiver["object"])
            # The image is newly created and already blank. SimpleBake keeps
            # use_clear disabled for every object so merged objects accumulate
            # into the same atlas.
            clear = False
            config.configure(bake_type, margin, clear, pass_filter)
            kwargs = {
                "type": bake_type,
                "use_clear": clear,
                "target": 'IMAGE_TEXTURES',
                "margin": margin,
                "margin_type": 'ADJACENT_FACES',
                "normal_space": (
                    'OBJECT'
                    if bake_type == 'NORMAL'
                    else context.scene.render.bake.normal_space
                ),
                "normal_r": context.scene.render.bake.normal_r,
                "normal_g": context.scene.render.bake.normal_g,
                "normal_b": context.scene.render.bake.normal_b,
            }
            try:
                result = bpy.ops.object.bake(**_supported_bake_kwargs(kwargs))
            except RuntimeError as exc:
                if "cancel" in str(exc).lower():
                    raise PipelineBakeCancelled(str(exc)) from exc
                raise
            if 'FINISHED' not in result:
                raise PipelineBakeCancelled(f"{bake_type} bake was cancelled")
    finally:
        config.restore()


def _signature_for_receivers(receivers, layer_type=""):
    digest = hashlib.sha256()
    digest.update(layer_type.encode())
    for receiver in sorted(receivers, key=lambda item: item["source"].pm_vr_pipeline.source_id):
        source = receiver["source"]
        mesh = receiver["mesh"]
        digest.update(source.pm_vr_pipeline.source_id.encode())
        digest.update(f"{len(mesh.vertices)}:{len(mesh.edges)}:{len(mesh.polygons)}:{len(mesh.loops)}".encode())
        digest.update(array('f', [value for row in source.matrix_world for value in row]).tobytes())
        uv = mesh.uv_layers.get(BAKE_UV_NAME)
        coords = array('f', [0.0]) * (len(uv.data) * 2)
        uv.data.foreach_get("uv", coords)
        digest.update(coords.tobytes())
        indices = array('i', [0]) * len(mesh.polygons)
        if indices:
            mesh.polygons.foreach_get("material_index", indices)
            digest.update(indices.tobytes())
        digest.update(str(len(mesh.materials)).encode())
    digest.update(str(SCHEMA_VERSION).encode())
    return digest.hexdigest()


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
    material[TAG_GENERATED] = True
    material[TAG_UNIT_ID] = unit.unit_id
    material[TAG_SOURCE_ID] = source_id
    material[TAG_LAYER_ID] = layer.layer_id
    material[TAG_LAYER_TYPE] = layer.layer_type
    material[TAG_MODE] = mode
    material[TAG_STATE] = state
    material[TAG_MATERIAL_SLOT] = slot
    material[TAG_SCHEMA] = SCHEMA_VERSION


def _tag_image(image, unit, layer, state, mode):
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
    principled = _principled_nodes(material)[0]
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
            material = original.copy() if original else _make_fallback_material(f"PMVR_{source.name}_{slot}")
            results.append(material)
            material.name = f"PMVR_{safe_stem(source.name)}_{state}_{slot}_{unit.unit_id[:8]}"
            principled = _principled_nodes(material)
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
        for material in results:
            if material.users == 0:
                bpy.data.materials.remove(material)
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
    for obj in _live_generated_objects(unit.unit_id, mode):
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


def _check_commit(unit, layer, receivers, staged, signature, mode='BEAUTY'):
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
        generated = _find_generated(unit.unit_id, source_id, mode)
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
        return _find_generated(parent_meta.bake_unit_id, parent_meta.source_id, mode)
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
        child = _find_generated(metadata.bake_unit_id, metadata.source_id, mode)
        target = parents_by_source_object[parent.as_pointer()]
        if not child or child.parent == target:
            continue
        world = child.matrix_world.copy()
        child.parent = target
        child.parent_type = child_source.parent_type
        child.parent_bone = child_source.parent_bone if child_source.parent_type == 'BONE' else ""
        child.matrix_world = world


def _commit_generated_geometry(context, unit, layer, receivers, signature, mode='BEAUTY'):
    generated_collection = _collection(GENERATED_COLLECTION)
    _ensure_scene_collection(context.scene, generated_collection)
    previous_signature = _previous_signature(unit, mode)
    compatible = not previous_signature or previous_signature == signature
    for receiver in receivers:
        source = receiver["source"]
        source_id = source.pm_vr_pipeline.source_id
        generated = _find_generated(unit.unit_id, source_id, mode)
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
        generated.hide_render = False
        generated.hide_set(False)
    live_source_ids = {receiver["source"].pm_vr_pipeline.source_id for receiver in receivers}
    for generated in _live_generated_objects(unit.unit_id, mode):
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
        obj.get(TAG_SOURCE_ID): obj for obj in _live_generated_objects(unit.unit_id, mode)
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


def _prepare_materials(unit, layer, members, state, image):
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
        for material in created:
            if material.users == 0:
                bpy.data.materials.remove(material)
        raise


def _commit_materials(unit, layer, members, state, staged, created, mode='BEAUTY'):
    try:
        collapse_to_one = (
            mode == 'BEAUTY'
            and layer.layer_type in UNLIT_LAYER_TYPES
        )
        for source in members:
            generated = _find_generated(unit.unit_id, source.pm_vr_pipeline.source_id, mode)
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
            generated = _find_generated(unit.unit_id, source.pm_vr_pipeline.source_id, mode)
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
        return created
    except Exception:
        for material in created:
            if material.users == 0:
                bpy.data.materials.remove(material)
        raise


def _prepare_lightmap_materials(unit, layer, members, state, image):
    staged = {}
    created = []
    try:
        for source in members:
            materials = []
            source_materials = [slot.material for slot in source.material_slots] or [None]
            for slot, original in enumerate(source_materials):
                material = original.copy() if original else _make_fallback_material(
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
        for material in created:
            if material.users == 0:
                bpy.data.materials.remove(material)
        raise


def _output_directory(value, label):
    if value.startswith("//") and not bpy.data.filepath:
        raise PipelineBakeError(f"Save the .blend file before using a relative {label} directory")
    directory = bpy.path.abspath(value)
    os.makedirs(directory, exist_ok=True)
    return directory


def _staging_path(final_path):
    directory, filename = os.path.split(final_path)
    stem, extension = os.path.splitext(filename)
    return os.path.join(directory, f".{stem}.pmvr_tmp_{uuid.uuid4().hex[:12]}{extension}")


def _point_image_at_file(image, filepath, file_format):
    image.filepath = bpy.path.relpath(filepath) if bpy.data.filepath else filepath
    image.filepath_raw = image.filepath
    image.source = 'FILE'
    image.file_format = file_format
    image.reload()


def _beauty_final_path(context, unit, state):
    project = context.scene.pm_vr_project
    directory = _output_directory(project.beauty_output_directory, "Beauty")
    suffix = "" if state == 'DAY' else "_Evening"
    layer = find_layer(project, unit.render_layer_id)
    layer_name = layer.display_name if layer else "Layer"
    stem = safe_stem(f"{layer_name}_{unit.display_name}")
    collision = next((
        other for other in project.bake_units
        if other.unit_id != unit.unit_id
        and safe_stem(
            f"{(find_layer(project, other.render_layer_id).display_name if find_layer(project, other.render_layer_id) else 'Layer')}_"
            f"{other.display_name}"
        ).casefold() == stem.casefold()
    ), None)
    if collision:
        raise PipelineBakeError(
            f'Beauty filename conflict: rename unit "{unit.display_name}" '
            f'or "{collision.display_name}"'
        )
    return os.path.join(directory, f"{stem}{suffix}_Beauty.png")


def _stage_beauty_image(context, unit, state, image):
    """Write the Beauty PNG beside its final path; the final file is untouched."""
    staged = StagedExport(
        _beauty_final_path(context, unit, state),
        "",
    )
    staged.staging_path = _staging_path(staged.final_path)
    export_scene = bpy.data.scenes.new(
        f"__PMVR_BEAUTY_EXPORT_{uuid.uuid4().hex}"
    )
    try:
        settings = export_scene.render.image_settings
        settings.file_format = 'PNG'
        settings.color_mode = 'RGB'
        settings.color_depth = '8'
        source_view = context.scene.view_settings
        target_view = export_scene.view_settings
        for attribute in (
            "view_transform", "look", "exposure", "gamma",
        ):
            try:
                setattr(target_view, attribute, getattr(source_view, attribute))
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            export_scene.display_settings.display_device = (
                context.scene.display_settings.display_device
            )
            export_scene.sequencer_colorspace_settings.name = (
                context.scene.sequencer_colorspace_settings.name
            )
        except (AttributeError, TypeError, ValueError):
            pass
        image.save_render(staged.staging_path, scene=export_scene)
        # Later stages (denoise, material preview) read the staged pixels.
        _point_image_at_file(image, staged.staging_path, 'PNG')
    except Exception:
        staged.cleanup()
        raise
    finally:
        bpy.data.scenes.remove(export_scene)
    return staged


def _beauty_image_name(layer, unit, state):
    return (
        f"PMVR_{safe_stem(layer.display_name)}_"
        f"{safe_stem(unit.display_name)}_{state}_Beauty"
    )


def _stage_lightmap_image(context, unit, state, image):
    project = context.scene.pm_vr_project
    directory = _output_directory(project.lightmap_output_directory, "Lightmap")
    suffix = "" if state == 'DAY' else "_Evening"
    final_path = os.path.join(
        directory,
        f"{safe_stem(unit.display_name)}_{unit.artifact_key[:8]}{suffix}_LM.exr",
    )
    staged = StagedExport(final_path, _staging_path(final_path))
    try:
        save_linear_exr(image, staged.staging_path)
    except Exception:
        staged.cleanup()
        raise
    return staged


def _commit_staged_file(staged, image, file_format):
    """Atomically publish a staged file; the previous file stays as backup."""
    staged.commit()
    _point_image_at_file(image, staged.final_path, file_format)


def _record(project, unit, state, signature, image, status, message="", mode='BEAUTY'):
    record = project.build_records.add()
    record.unit_id = unit.unit_id
    record.lighting_state = state
    record.bake_mode = mode
    record.signature = signature
    record.image_name = image.name if image else ""
    record.timestamp = datetime.now().isoformat(timespec="seconds")
    record.status = status
    record.message = message


def _show_bake_stage(operator, message, step, step_count):
    feedback = getattr(operator, "_pmvr_feedback", None) if operator else None
    if feedback:
        feedback.set_stage(message, step, step_count)


class BeautyBakeRuntime:
    """One prepared Beauty unit, advanced by the modal queue operator."""

    def __init__(self, context, unit, operator=None):
        self.context = context
        self.project = context.scene.pm_vr_project
        self.unit_id = unit.unit_id
        self.operator = operator
        self.state = self.project.active_lighting_state
        self._resolve()
        self.members = []
        self.receivers = []
        self.image = None
        self.signature = ""
        self.snapshot = None
        self.context_state = None
        self.config = None
        self.work_collection = None
        self.created_materials = []
        self.warnings = []
        self.finished = False

    def _resolve(self):
        # Collection items move in memory when the collection grows or shrinks;
        # look the unit and layer up by stable ID instead of holding them.
        self.unit = find_unit(self.project, self.unit_id)
        if not self.unit:
            raise PipelineBakeError("bake unit was removed during the bake")
        self.layer = find_layer(self.project, self.unit.render_layer_id)
        if not self.layer:
            raise PipelineBakeError(f'unit "{self.unit.display_name}" has no render layer')

    def prepare(self):
        issues = validate_unit(self.context, self.unit, require_visible=True)
        errors = [issue.message for issue in issues if issue.severity == 'ERROR']
        if errors:
            raise PipelineBakeError("; ".join(errors))
        self.members = unit_members(self.unit.unit_id)
        visible = [
            obj for obj in self.members
            if object_render_visible(obj, self.context.view_layer)
        ]
        if not visible:
            return "SKIPPED"

        self.work_collection = _collection(WORK_COLLECTION)
        _ensure_scene_collection(self.context.scene, self.work_collection)
        _clear_collection(self.work_collection)
        self.snapshot = EvaluationSnapshot(self.context)
        self.context_state = ContextState(self.context)
        self.snapshot.isolate_source_root(self.project)
        for source in self.members:
            source.hide_render = True
        self.context.scene.cycles.samples = self.project.cycles_samples
        self.image = create_float_image(
            _beauty_image_name(self.layer, self.unit, self.state),
            int(self.unit.resolution),
        )
        try:
            self.image.colorspace_settings.name = 'sRGB'
        except (TypeError, ValueError):
            pass
        _tag_image(
            self.image,
            self.unit,
            self.layer,
            self.state,
            'BEAUTY',
        )
        for source in self.members:
            self.receivers.append(
                _copy_receiver(
                    self.context,
                    source,
                    self.work_collection,
                    self.image,
                    self.layer.layer_type,
                )
            )
        self.signature = _signature_for_receivers(
            self.receivers,
            self.layer.layer_type,
        )
        all_passes = {
            'DIRECT', 'INDIRECT', 'COLOR', 'DIFFUSE',
            'GLOSSY', 'TRANSMISSION', 'EMIT',
        }
        self.config = BakeConfigurationSnapshot(self.context.scene)
        self.config.configure('COMBINED', self.project.margin, False, all_passes)
        log.info(
            "Beauty",
            f'Start {self.state.title()} unit "{self.unit.display_name}": '
            f'{len(self.receivers)} object(s), {self.unit.resolution}px, '
            f'{self.project.cycles_samples} samples',
        )
        return "READY"

    def select_receiver(self, index):
        self._resolve()
        receiver = self.receivers[index]
        _set_target_image(receiver, self.image)
        _select_only(self.context, receiver["object"])
        _show_bake_stage(
            self.operator,
            f"Combined {index + 1}/{len(self.receivers)} — {receiver['source'].name}",
            index + 1,
            len(self.receivers) + 3,
        )
        return receiver

    def bake_kwargs(self):
        bake = self.context.scene.render.bake
        return _supported_bake_kwargs({
            "type": 'COMBINED',
            "use_clear": False,
            "target": 'IMAGE_TEXTURES',
            "margin": self.project.margin,
            "margin_type": getattr(bake, "margin_type", 'ADJACENT_FACES'),
            "normal_space": bake.normal_space,
            "normal_r": bake.normal_r,
            "normal_g": bake.normal_g,
            "normal_b": bake.normal_b,
        })

    def finish(self):
        self._resolve()
        step_count = len(self.receivers) + 3
        _show_bake_stage(
            self.operator,
            "Save external Beauty",
            len(self.receivers) + 1,
            step_count,
        )
        # Match SimpleBake: establish an external file before compositor work.
        # It is staged beside the final path; the previous successful file is
        # replaced only after every Blender-side step has been prepared.
        staged_file = _stage_beauty_image(
            self.context, self.unit, self.state, self.image
        )
        published = False
        try:
            _show_bake_stage(
                self.operator,
                "Compositor denoise",
                len(self.receivers) + 2,
                step_count,
            )
            try:
                denoise_external_beauty(
                    self.context.scene,
                    self.image,
                    staged_file.staging_path,
                )
            except Exception as exc:
                self.warnings.append(f"denoise failed, raw Beauty used: {exc}")
                if self.operator:
                    self.operator.report(
                        {'WARNING'},
                        f"Denoise failed; using raw Beauty: {exc}",
                    )
                log.warning(
                    "Beauty",
                    f'{self.unit.display_name}: denoise failed; using raw Beauty: {exc}',
                )
            _show_bake_stage(
                self.operator,
                "Build preview result",
                len(self.receivers) + 3,
                step_count,
            )
            staged, self.created_materials = _prepare_materials(
                self.unit,
                self.layer,
                self.members,
                self.state,
                self.image,
            )
            _check_commit(
                self.unit,
                self.layer,
                self.receivers,
                staged,
                self.signature,
            )
            _commit_staged_file(staged_file, self.image, 'PNG')
            published = True
            _commit_generated_geometry(
                self.context,
                self.unit,
                self.layer,
                self.receivers,
                self.signature,
            )
            _commit_materials(
                self.unit,
                self.layer,
                self.members,
                self.state,
                staged,
                self.created_materials,
            )
        except Exception:
            if published:
                staged_file.rollback()
            raise
        finally:
            staged_file.cleanup()
        staged_file.finalize()
        log.info("Beauty", f'Saved external image: "{staged_file.final_path}"')
        old_image_name = (
            self.unit.day_beauty_image
            if self.state == 'DAY'
            else self.unit.evening_beauty_image
        )
        if old_image_name and old_image_name != self.image.name:
            old_image = bpy.data.images.get(old_image_name)
            if old_image and old_image.users == 0:
                bpy.data.images.remove(old_image)
        self.image.name = _beauty_image_name(
            self.layer,
            self.unit,
            self.state,
        )
        if self.state == 'DAY':
            self.unit.day_signature = self.signature
            self.unit.day_beauty_image = self.image.name
            self.unit.day_status = "Ready"
        else:
            self.unit.evening_signature = self.signature
            self.unit.evening_beauty_image = self.image.name
            self.unit.evening_status = "Ready"
        other_signature = (
            self.unit.evening_signature
            if self.state == 'DAY'
            else self.unit.day_signature
        )
        if other_signature and other_signature != self.signature:
            if self.state == 'DAY':
                self.unit.evening_status = (
                    "Structurally incompatible — rebake required"
                )
            else:
                self.unit.day_status = (
                    "Structurally incompatible — rebake required"
                )
        _record(
            self.project,
            self.unit,
            self.state,
            self.signature,
            self.image,
            "SUCCESS",
            "; ".join(self.warnings),
        )
        viewport_overlay.mark_baked(
            self.project,
            self.unit,
            self.state,
            'BEAUTY',
        )
        self.finished = True
        log.info(
            "Beauty",
            f'Completed {self.state.title()} unit "{self.unit.display_name}"',
        )
        self.cleanup(keep_image=True)

    def _label(self):
        try:
            self._resolve()
        except PipelineBakeError:
            return None
        return self.unit.display_name

    def fail(self, exc):
        label = self._label()
        log.error(
            "Beauty",
            f'Failed {self.state.title()} unit "{label or self.unit_id}": {exc}',
        )
        if label is not None:
            _record(
                self.project,
                self.unit,
                self.state,
                "",
                self.image,
                "FAILED",
                str(exc),
            )
        self.cleanup(keep_image=False)

    def cancel(self, reason):
        label = self._label()
        log.warning(
            "Beauty",
            f'Cancelled {self.state.title()} unit "{label or self.unit_id}": '
            f'{reason}; previous result kept',
        )
        if label is not None:
            _record(
                self.project,
                self.unit,
                self.state,
                "",
                None,
                "CANCELLED",
                reason,
            )
        self.cleanup(keep_image=False)

    def cleanup(self, keep_image=False):
        cleanup_errors = []
        if self.config:
            try:
                self.config.restore()
            except Exception as exc:
                cleanup_errors.append(f"bake settings: {exc}")
            self.config = None
        if self.work_collection:
            try:
                _remove_work_collection(self.work_collection)
            except Exception as exc:
                cleanup_errors.append(f"work collection: {exc}")
            self.work_collection = None
        for material in self.created_materials:
            try:
                if material.users == 0:
                    bpy.data.materials.remove(material)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(f"material: {exc}")
        if not keep_image and self.image:
            try:
                if self.image.users == 0:
                    remove_image(self.image)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(f"image: {exc}")
        if self.snapshot:
            try:
                self.snapshot.restore()
            except Exception as exc:
                cleanup_errors.append(f"visibility: {exc}")
            self.snapshot = None
        if self.context_state:
            try:
                self.context_state.restore(self.context)
            except Exception as exc:
                cleanup_errors.append(f"context: {exc}")
            self.context_state = None
        if cleanup_errors:
            log.warning(
                "Beauty",
                "Cleanup completed with warnings: " + "; ".join(cleanup_errors),
            )


def bake_lightmap_unit(context, unit, operator=None):
    project = context.scene.pm_vr_project
    layer = find_layer(project, unit.render_layer_id)
    state = project.active_lighting_state
    issues = validate_unit(context, unit, require_visible=True)
    errors = [issue.message for issue in issues if issue.severity == 'ERROR']
    if errors:
        raise PipelineBakeError("; ".join(errors))
    members = unit_members(unit.unit_id)
    if not any(object_render_visible(obj, context.view_layer) for obj in members):
        return "SKIPPED", "fully hidden in active state"

    work_collection = _collection(WORK_COLLECTION)
    _ensure_scene_collection(context.scene, work_collection)
    _clear_collection(work_collection)
    snapshot = EvaluationSnapshot(context)
    context_state = ContextState(context)
    raw = albedo = normal = None
    receivers = []
    created_materials = []
    try:
        snapshot.isolate_source_root(project)
        for source in members:
            source.hide_render = True
        context.scene.cycles.samples = project.cycles_samples
        resolution = int(unit.resolution)
        raw = create_float_image(
            f"PMVR_Lightmap_{unit.artifact_key[:8]}_{state}_{uuid.uuid4().hex[:8]}",
            resolution,
        )
        _tag_image(raw, unit, layer, state, 'LIGHTMAP')
        albedo = create_float_image(
            f"__PMVR_LM_ALBEDO_{uuid.uuid4().hex}", resolution, (1.0, 1.0, 1.0, 1.0)
        )
        normal = create_float_image(
            f"__PMVR_LM_NORMAL_{uuid.uuid4().hex}", resolution, (0.5, 0.5, 1.0, 1.0)
        )
        for source in members:
            receivers.append(
                _copy_receiver(context, source, work_collection, raw, 'LIGHTMAP')
            )
        signature = _signature_for_receivers(receivers, 'LIGHTMAP')
        _show_bake_stage(operator, "Lightmap / Lighting", 1, 5)
        _bake_receivers(
            context,
            receivers,
            raw,
            'DIFFUSE',
            project.margin,
            {'DIRECT', 'INDIRECT'},
        )
        context.scene.cycles.samples = 1
        try:
            _show_bake_stage(operator, "Denoise guide / Albedo", 2, 5)
            _bake_receivers(
                context,
                receivers,
                albedo,
                'DIFFUSE',
                project.margin,
                {'COLOR'},
            )
            _show_bake_stage(operator, "Denoise guide / Normal", 3, 5)
            _bake_receivers(context, receivers, normal, 'NORMAL', project.margin)
        finally:
            context.scene.cycles.samples = project.cycles_samples
        try:
            _show_bake_stage(operator, "Compositor denoise", 4, 5)
            denoise_image(context.scene, raw, albedo, normal)
        except Exception as exc:
            if operator:
                operator.report({'WARNING'}, f"Lightmap denoise failed; using raw result: {exc}")
            print(f"[PM VR][Lightmap] Denoise warning for {unit.display_name}: {exc}")
        _show_bake_stage(operator, "Build preview result", 5, 5)
        staged, created_materials = _prepare_lightmap_materials(
            unit, layer, members, state, raw
        )
        _check_commit(unit, layer, receivers, staged, signature, mode='LIGHTMAP')
        staged_file = _stage_lightmap_image(context, unit, state, raw)
        published = False
        try:
            _commit_staged_file(staged_file, raw, 'OPEN_EXR')
            published = True
            _commit_generated_geometry(
                context, unit, layer, receivers, signature, mode='LIGHTMAP'
            )
            _commit_materials(
                unit,
                layer,
                members,
                state,
                staged,
                created_materials,
                mode='LIGHTMAP',
            )
        except Exception:
            if published:
                staged_file.rollback()
            raise
        finally:
            staged_file.cleanup()
        staged_file.finalize()
        if state == 'DAY':
            old_image_name = unit.day_lightmap_image
            unit.day_lightmap_signature = signature
            unit.day_lightmap_image = raw.name
            unit.day_lightmap_status = "Ready"
        else:
            old_image_name = unit.evening_lightmap_image
            unit.evening_lightmap_signature = signature
            unit.evening_lightmap_image = raw.name
            unit.evening_lightmap_status = "Ready"
        if old_image_name and old_image_name != raw.name:
            old_image = bpy.data.images.get(old_image_name)
            if old_image and old_image.users == 0:
                bpy.data.images.remove(old_image)
        _record(project, unit, state, signature, raw, "SUCCESS", mode='LIGHTMAP')
        viewport_overlay.mark_baked(project, unit, state, 'LIGHTMAP')
        return "SUCCESS", "Lightmap ready"
    except Exception as exc:
        _record(
            project,
            unit,
            state,
            "",
            raw,
            "FAILED",
            str(exc),
            mode='LIGHTMAP',
        )
        for material in created_materials:
            if material.users == 0:
                bpy.data.materials.remove(material)
        if raw and raw.users == 0:
            remove_image(raw)
        raise
    finally:
        _remove_work_collection(work_collection)
        remove_image(albedo)
        remove_image(normal)
        snapshot.restore()
        context_state.restore(context)


# Cycles bake jobs report cancellation only through app handlers: the job's
# own modal handler consumes Esc before the queue operator can see it.
_BAKE_JOB = {"cancelled": False, "completed": False}
_QUEUE = {"running": False, "cancel_requested": False}


def _on_bake_job_cancel(*_args):
    _BAKE_JOB["cancelled"] = True


def _on_bake_job_complete(*_args):
    _BAKE_JOB["completed"] = True


def _bake_job_handler_lists():
    handlers = bpy.app.handlers
    return (
        (getattr(handlers, "object_bake_cancel", None), _on_bake_job_cancel),
        (getattr(handlers, "object_bake_complete", None), _on_bake_job_complete),
    )


def _remove_bake_job_handlers():
    for handler_list, callback in _bake_job_handler_lists():
        if handler_list is None:
            continue
        # Match by name so a reloaded module also removes stale callbacks.
        for existing in list(handler_list):
            if (
                getattr(existing, "__name__", "") == callback.__name__
                and getattr(existing, "__module__", "") == __name__
            ):
                handler_list.remove(existing)


def _install_bake_job_handlers():
    _remove_bake_job_handlers()
    for handler_list, callback in _bake_job_handler_lists():
        if handler_list is not None:
            handler_list.append(callback)


def _reset_bake_job_flags():
    _BAKE_JOB["cancelled"] = False
    _BAKE_JOB["completed"] = False


def shutdown():
    """Detach bake-job handlers when the add-on is unregistered."""
    _remove_bake_job_handlers()
    _QUEUE["running"] = False
    _QUEUE["cancel_requested"] = False


def _ensure_object_mode(context):
    obj = getattr(context, "active_object", None)
    if obj and obj.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass


class PMVR_OT_CancelBakeQueue(bpy.types.Operator):
    bl_idname = "pmvr.cancel_bake_queue"
    bl_label = "Cancel Bake"
    bl_description = (
        "Stop the running bake queue. The unit in progress is discarded and "
        "earlier results stay unchanged. Esc also stops a running Cycles pass "
        "immediately"
    )

    @classmethod
    def poll(cls, context):
        return bool(context.scene and context.scene.pm_vr_project.operation_running)

    def execute(self, context):
        if not _QUEUE["running"]:
            # No queue owns the flag (for example after an interrupted run);
            # release the Bake button instead of leaving it disabled.
            context.scene.pm_vr_project.operation_running = False
            self.report({'INFO'}, "No bake is running; Bake is available again")
            return {'FINISHED'}
        _QUEUE["cancel_requested"] = True
        self.report(
            {'WARNING'},
            "Cancelling after the current Cycles pass; press Esc to stop it now",
        )
        return {'FINISHED'}


class PMVR_OT_BakeQueue(bpy.types.Operator):
    bl_idname = "pmvr.bake_queue"
    bl_label = "Bake Queue"
    bl_description = "Bake every queued unit for the checked Day/Evening states"

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(project.bake_queue and not project.operation_running)

    def execute(self, context):
        if bpy.app.background:
            self.report({'ERROR'}, "Interactive Cycles bake is required")
            return {'CANCELLED'}
        project = context.scene.pm_vr_project
        states = []
        if project.bake_day:
            states.append('DAY')
        if project.bake_evening:
            states.append('EVENING')
        if not states:
            self.report({'ERROR'}, "Choose Day, Evening, or both")
            return {'CANCELLED'}
        _ensure_object_mode(context)
        self._viewport_shading = _switch_viewports_to_wireframe(context)
        _QUEUE["cancel_requested"] = False
        log.info(
            "Bake",
            f"Queue start: {len(project.bake_queue)} unit(s), "
            f"states {', '.join(states)}, mode {project.bake_mode}",
        )
        if project.bake_mode == 'LIGHTMAP':
            return self._execute_lightmap(context, states)

        entries = [entry.unit_id for entry in project.bake_queue]
        self._project = project
        self._states = states
        self._original_state = project.active_lighting_state
        self._jobs = [
            (state, unit_id) for state in states for unit_id in entries
        ]
        self._job_cursor = 0
        self._current_runtime = None
        self._receiver_index = 0
        self._waiting_for_bake = False
        self._job_seen_running = False
        self._job_started_at = 0.0
        self._cancel_requested = False
        self._cancel_reason = ""
        self._discarded_unit = ""
        self._succeeded = self._skipped = self._failed = self._warned = 0
        self._timer = None
        self._feedback = BakeProgressFeedback(
            context,
            title="PM VR BEAUTY BAKER",
        )
        self._pmvr_feedback = self._feedback
        project.operation_running = True
        _QUEUE["running"] = True
        _install_bake_job_handlers()
        try:
            self._feedback.start(len(self._jobs))
            self._feedback.set_candidate_count(len(self._jobs))
            self._feedback.add_message(
                'INFO',
                "Esc cancels the whole queue; finished units are kept",
            )
            context.window_manager.progress_begin(0, len(self._jobs))
            self._timer = context.window_manager.event_timer_add(
                0.2,
                window=context.window,
            )
            context.window_manager.modal_handler_add(self)
            result = self._start_next_job(context)
            if result:
                return result
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._failed += 1
            log.error("Beauty", f"Could not start modal bake: {exc}")
            if self._current_runtime:
                self._current_runtime.fail(exc)
                self._current_runtime = None
            return self._finish_modal(context, cancelled=True)

    def _request_cancel(self, reason):
        if not self._cancel_requested:
            self._cancel_requested = True
            self._cancel_reason = reason
            log.warning("Bake", f"Cancel requested: {reason}")
            self._feedback.add_message('WARNING', f"Cancelling: {reason}")

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:
            # Never leave the scene isolated or the Bake button disabled after
            # an unexpected error inside an unattended queue.
            log.error("Bake", f"Unexpected queue error: {exc}")
            if self._current_runtime:
                self._failed += 1
                try:
                    self._current_runtime.fail(exc)
                except Exception as cleanup_exc:
                    log.error("Bake", f"Cleanup after queue error failed: {cleanup_exc}")
                self._current_runtime = None
            return self._finish_modal(context, cancelled=True)

    def _modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._request_cancel("Esc pressed")
            if not self._waiting_for_bake:
                return self._finish_modal(context, cancelled=True)
            return {'PASS_THROUGH'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if _QUEUE["cancel_requested"]:
            self._request_cancel("Cancel button")
        if not self._waiting_for_bake:
            if self._cancel_requested:
                return self._finish_modal(context, cancelled=True)
            return {'PASS_THROUGH'}

        running = bpy.app.is_job_running('OBJECT_BAKE')
        if running:
            self._job_seen_running = True
            return {'PASS_THROUGH'}
        if (
            not self._job_seen_running
            and not _BAKE_JOB["completed"]
            and not _BAKE_JOB["cancelled"]
            and time.monotonic() - self._job_started_at < 1.0
        ):
            return {'PASS_THROUGH'}

        self._waiting_for_bake = False
        if _BAKE_JOB["cancelled"]:
            # Esc in the Cycles job or its status-bar cancel button. The
            # interrupted member left a partial atlas: discard the whole unit.
            self._request_cancel("Cycles bake was cancelled")
        runtime = self._current_runtime
        if runtime and not self._cancel_requested:
            receiver = runtime.receivers[self._receiver_index]
            log.info(
                "Beauty",
                f'Baked {self._receiver_index + 1}/{len(runtime.receivers)} '
                f'"{receiver["source"].name}" in unit '
                f'"{runtime._label() or runtime.unit_id}"',
            )
        if self._cancel_requested:
            return self._finish_modal(context, cancelled=True)
        self._receiver_index += 1
        if self._receiver_index < len(self._current_runtime.receivers):
            try:
                return self._start_receiver(context)
            except Exception as exc:
                self._failed += 1
                self._current_runtime.fail(exc)
                self._feedback.complete_object()
                self._current_runtime = None
                result = self._start_next_job(context)
                return result or {'RUNNING_MODAL'}
        try:
            self._current_runtime.finish()
            self._succeeded += 1
            if self._current_runtime.warnings:
                self._warned += 1
        except Exception as exc:
            self._failed += 1
            self._current_runtime.fail(exc)
        finally:
            self._feedback.complete_object()
            self._current_runtime = None
        result = self._start_next_job(context)
        return result or {'RUNNING_MODAL'}

    def _start_next_job(self, context):
        while self._job_cursor < len(self._jobs):
            if self._cancel_requested or _QUEUE["cancel_requested"]:
                self._request_cancel(self._cancel_reason or "Cancel button")
                return self._finish_modal(context, cancelled=True)
            state, unit_id = self._jobs[self._job_cursor]
            self._job_cursor += 1
            self._project.operation_progress = (
                (self._job_cursor - 1) / max(1, len(self._jobs))
            )
            context.window_manager.progress_update(self._job_cursor - 1)
            unit = find_unit(self._project, unit_id)
            self._feedback.begin_object(
                f"{state.title()} — {unit.display_name if unit else 'Missing unit'}",
                self._job_cursor,
                len(self._jobs),
            )
            if not unit:
                self._skipped += 1
                self._feedback.complete_object()
                continue
            try:
                activate_state(context, state)
                runtime = BeautyBakeRuntime(context, unit, self)
                # Own the runtime before preparation starts. Preparation can
                # fail after it has hidden sources, changed bake settings, or
                # created PMVR_WORK data, and must always be cleaned up.
                self._current_runtime = runtime
                status = runtime.prepare()
                if status == "SKIPPED":
                    runtime.cleanup(keep_image=False)
                    self._current_runtime = None
                    self._skipped += 1
                    self._feedback.complete_object()
                    continue
                self._receiver_index = 0
                return self._start_receiver(context)
            except Exception as exc:
                self._failed += 1
                if self._current_runtime:
                    self._current_runtime.fail(exc)
                    self._current_runtime = None
                else:
                    log.error(
                        "Beauty",
                        f'Failed {state.title()} unit "{unit.display_name}": {exc}',
                    )
                self._feedback.complete_object()
        return self._finish_modal(context, cancelled=False)

    def _start_receiver(self, _context):
        runtime = self._current_runtime
        runtime.select_receiver(self._receiver_index)
        _reset_bake_job_flags()
        result = bpy.ops.object.bake(
            'INVOKE_DEFAULT',
            **runtime.bake_kwargs(),
        )
        if 'CANCELLED' in result:
            self._request_cancel("Cycles bake could not start")
            return self._finish_modal(runtime.context, cancelled=True)
        self._waiting_for_bake = True
        self._job_seen_running = bpy.app.is_job_running('OBJECT_BAKE')
        self._job_started_at = time.monotonic()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        # Blender cancels modal operators when a file is loaded or the window
        # closes. Old datablock references may already be invalid.
        try:
            if self._current_runtime:
                self._current_runtime.cleanup(keep_image=False)
        except Exception:
            pass
        self._current_runtime = None
        try:
            if self._timer:
                context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        self._timer = None
        _remove_bake_job_handlers()
        _QUEUE["running"] = False
        _QUEUE["cancel_requested"] = False
        try:
            self._project.operation_running = False
        except Exception:
            pass

    def _finish_modal(self, context, cancelled):
        if self._current_runtime:
            self._discarded_unit = self._current_runtime._label() or "removed unit"
            self._current_runtime.cancel(self._cancel_reason or "queue stopped")
            self._current_runtime = None
        timer = getattr(self, "_timer", None)
        if timer:
            context.window_manager.event_timer_remove(timer)
            self._timer = None
        _remove_bake_job_handlers()
        _QUEUE["running"] = False
        _QUEUE["cancel_requested"] = False
        context.window_manager.progress_end()
        self._project.operation_running = False
        self._project.operation_progress = 1.0
        try:
            activate_state(context, self._original_state)
        except Exception as exc:
            log.warning("Bake", f"Could not restore {self._original_state}: {exc}")
        _restore_viewport_shading(self._viewport_shading)
        state_label = " + ".join(state.title() for state in self._states)
        summary = (
            f"Beauty ({state_label}): {self._succeeded} ready, "
            f"{self._skipped} skipped, {self._failed} failed"
        )
        if self._warned:
            summary += f", {self._warned} with warnings"
        if cancelled:
            summary += ", cancelled"
            if self._discarded_unit:
                summary += f' ("{self._discarded_unit}" discarded)'
        self._project.last_operation_summary = summary
        log.info("Bake", summary)
        self._feedback.finish(
            summary,
            has_errors=bool(self._failed or cancelled or self._warned),
        )
        self.report(
            {'WARNING'} if self._failed or cancelled or self._warned else {'INFO'},
            summary + ("; see the PMVR Pipeline Log text" if self._failed or self._warned else ""),
        )
        return (
            {'FINISHED'}
            if (self._succeeded or self._skipped) and not cancelled
            else {'CANCELLED'}
        )

    def _execute_lightmap(self, context, states):
        project = context.scene.pm_vr_project
        entries = [entry.unit_id for entry in project.bake_queue]
        original_state = project.active_lighting_state
        total_jobs = len(entries) * len(states)
        feedback = BakeProgressFeedback(context)
        project.operation_running = True
        succeeded = skipped = failed = 0
        cancelled = False
        try:
            feedback.start(total_jobs)
            feedback.set_candidate_count(total_jobs)
            self._pmvr_feedback = feedback
            context.window_manager.progress_begin(0, total_jobs)
            job_index = 0
            for state in states:
                if cancelled:
                    break
                activate_state(context, state)
                for unit_id in entries:
                    job_index += 1
                    unit = find_unit(project, unit_id)
                    feedback.begin_object(
                        f"{state.title()} — {unit.display_name if unit else 'Missing unit'}",
                        job_index,
                        total_jobs,
                    )
                    if not unit:
                        skipped += 1
                        continue
                    try:
                        status, _message = bake_lightmap_unit(
                            context,
                            unit,
                            self,
                        )
                        if status == "SKIPPED":
                            skipped += 1
                        else:
                            succeeded += 1
                    except PipelineBakeCancelled as exc:
                        cancelled = True
                        log.warning("Lightmap", f'Cancelled in "{unit.display_name}": {exc}')
                        break
                    except Exception as exc:
                        failed += 1
                        log.error(
                            "Lightmap",
                            f'[{state}] Failed "{unit.display_name}": {exc}',
                        )
                    finally:
                        feedback.complete_object()
        finally:
            context.window_manager.progress_end()
            project.operation_running = False
            _restore_viewport_shading(self._viewport_shading)
            try:
                activate_state(context, original_state)
            except Exception as exc:
                log.warning("Bake", f"Restore warning: {exc}")
        state_label = " + ".join(state.title() for state in states)
        summary = (
            f"Lightmap ({state_label}): {succeeded} ready, "
            f"{skipped} skipped, {failed} failed"
        )
        if cancelled:
            summary += ", cancelled"
        project.last_operation_summary = summary
        log.info("Bake", summary)
        feedback.finish(summary, has_errors=bool(failed or cancelled))
        self.report({'WARNING'} if failed or cancelled else {'INFO'}, summary)
        return {'FINISHED'} if (succeeded or skipped) and not cancelled else {'CANCELLED'}


CLASSES = (PMVR_OT_BakeQueue, PMVR_OT_CancelBakeQueue)
