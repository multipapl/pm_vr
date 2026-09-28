"""Temporary bake scene state: isolation, receiver copies and Cycles bake calls."""

from array import array
import hashlib
import uuid

import bpy

from ..lightmap_baker.receiver import _wrap_surface_for_bake
from .constants import (
    BAKE_UV_NAME,
    GENERATED_COLLECTION,
    PRIMARY_UV_NAME,
    SCHEMA_VERSION,
    TAG_GENERATED,
    WORK_COLLECTION,
)
from . import log


class PipelineBakeError(RuntimeError):
    pass


class PipelineBakeCancelled(PipelineBakeError):
    pass


def switch_viewports_to_wireframe(context):
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


def restore_viewport_shading(snapshot):
    for space, shading_type in snapshot or []:
        try:
            space.shading.type = shading_type
        except (AttributeError, ReferenceError, TypeError, ValueError):
            pass


_COLLECTION_TAGS = {
    WORK_COLLECTION: "pmvr_temporary_work",
    GENERATED_COLLECTION: "pmvr_generated_collection",
}


def find_pipeline_collection(name):
    """PM VR's own collection, identified by tag rather than by name."""
    tag = _COLLECTION_TAGS[name]
    owned = next((c for c in bpy.data.collections if c.get(tag)), None)
    if owned:
        return owned
    legacy = bpy.data.collections.get(name)
    if (
        legacy
        and name == GENERATED_COLLECTION
        and not legacy.children
        and all(obj.get(TAG_GENERATED) for obj in legacy.objects)
    ):
        # Created before collections were tagged: it holds only generated data.
        legacy[tag] = True
        return legacy
    return None


def pipeline_collection(name):
    collection = find_pipeline_collection(name)
    if not collection:
        # A user collection may already use the name; Blender then picks a
        # free one, and the user's collection is never touched.
        collection = bpy.data.collections.new(name)
        collection[_COLLECTION_TAGS[name]] = True
    return collection


def ensure_scene_collection(scene, collection):
    if collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)


def clear_collection(collection):
    for obj in list(collection.objects):
        mesh = obj.data if obj.type == 'MESH' else None
        materials = [slot.material for slot in obj.material_slots if slot.material]
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            if material.users == 0:
                bpy.data.materials.remove(material)


def remove_work_collection(collection):
    if not collection:
        return
    if (
        collection.get("pmvr_temporary_work")
        and bpy.data.collections.get(collection.name) is collection
    ):
        clear_collection(collection)
        bpy.data.collections.remove(collection)


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


# Temporary bake visibility is mirrored into the .blend so a crash or a save
# during the bake can be undone by recover_interrupted_bake() on load.
RESTORE_HIDE_RENDER = "pmvr_bake_restore_hide_render"
RESTORE_EXCLUDE = "pmvr_bake_restore_exclude"


class EvaluationSnapshot:
    """Restore render visibility, samples and generated visibility after a unit."""

    def __init__(self, context):
        self.scene = context.scene
        self.view_layer_name = context.view_layer.name
        self.hide_render = [(obj, obj.hide_render) for obj in context.scene.objects]
        self.samples = getattr(context.scene.cycles, "samples", None) if hasattr(context.scene, "cycles") else None
        self.generated = find_pipeline_collection(GENERATED_COLLECTION)
        self.generated_layer_states = [
            (layer_collection, layer_collection.exclude)
            for layer_collection in (
                _layer_collections_for(context.view_layer.layer_collection, self.generated)
                if self.generated else []
            )
        ]

    def hide(self, obj):
        if RESTORE_HIDE_RENDER not in obj:
            obj[RESTORE_HIDE_RENDER] = obj.hide_render
        obj.hide_render = True

    def isolate_source_root(self, project):
        # Exclusion removes generated results from View Layer evaluation as
        # well as rendering. Object hide_render remains a defensive fallback
        # for generated objects linked through another collection path.
        if self.generated_layer_states:
            self.generated[RESTORE_EXCLUDE] = {
                self.view_layer_name: self.generated_layer_states[0][1],
            }
        for layer_collection, _exclude in self.generated_layer_states:
            layer_collection.exclude = True
        allowed = _source_root_objects(project)
        for obj, _value in self.hide_render:
            if obj.get(TAG_GENERATED) or obj not in allowed:
                self.hide(obj)

    def restore(self):
        for obj, value in self.hide_render:
            try:
                obj.hide_render = value
                if RESTORE_HIDE_RENDER in obj:
                    del obj[RESTORE_HIDE_RENDER]
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
        try:
            if self.generated and RESTORE_EXCLUDE in self.generated:
                del self.generated[RESTORE_EXCLUDE]
        except ReferenceError:
            pass


def recover_interrupted_bake():
    """Undo temporary bake state found in a freshly loaded file."""
    restored = 0
    for obj in bpy.data.objects:
        if RESTORE_HIDE_RENDER in obj:
            obj.hide_render = bool(obj[RESTORE_HIDE_RENDER])
            del obj[RESTORE_HIDE_RENDER]
            restored += 1
    generated = find_pipeline_collection(GENERATED_COLLECTION)
    if generated and RESTORE_EXCLUDE in generated:
        states = generated[RESTORE_EXCLUDE].to_dict()
        for scene in bpy.data.scenes:
            for view_layer in scene.view_layers:
                if view_layer.name in states:
                    for layer_collection in _layer_collections_for(view_layer.layer_collection, generated):
                        layer_collection.exclude = bool(states[view_layer.name])
        del generated[RESTORE_EXCLUDE]
        restored += 1
    for collection in list(bpy.data.collections):
        if collection.get("pmvr_temporary_work"):
            remove_work_collection(collection)
            restored += 1
    return restored


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


def principled_nodes(material):
    if not material or not material.use_nodes or not material.node_tree:
        return []
    return [node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']


def _linked_from(socket):
    """Output socket feeding an input, skipping reroutes and muted links."""
    while socket is not None and socket.is_linked:
        link = socket.links[0]
        if link.is_muted or not link.is_valid:
            return None
        if link.from_node.type == 'REROUTE':
            socket = link.from_node.inputs[0]
            continue
        return link.from_socket
    return None


def _socket_value(socket):
    value = socket.default_value
    if hasattr(value, "__len__"):
        # A colour used as opacity: Blender converts it by luminance; the
        # average is close enough to tell opaque from transparent.
        return sum(value[:3]) / 3.0
    return float(value)


def _group_output(tree):
    outputs = [node for node in tree.nodes if node.type == 'GROUP_OUTPUT']
    return next((node for node in outputs if node.is_active_output), outputs[0] if outputs else None)


def _alpha_value(input_socket, groups, invert=False):
    """(socket in the material's own tree, invert) or ('VALUE', value) for an
    alpha input, following group inputs out to the group node's links."""
    source = _linked_from(input_socket)
    if source is None:
        value = _socket_value(input_socket)
        return ('VALUE', 1.0 - value if invert else value)
    if source.node.type == 'GROUP_INPUT' and groups:
        outer = groups[-1]
        outer_input = next(
            (socket for socket in outer.inputs if socket.identifier == source.identifier),
            None,
        )
        if outer_input is None:
            raise PipelineBakeError(f'node group "{outer.node_tree.name}" has no input "{source.name}"')
        return _alpha_value(outer_input, groups[:-1], invert)
    if groups:
        raise PipelineBakeError(
            f'transparency is computed inside node group "{groups[-1].node_tree.name}"; '
            "feed it in through a group input (like Opacity)"
        )
    return ('SOCKET', source, invert)


def _shader_alphas(shader_socket, groups, depth=0):
    """Alpha sources found behind a shader output socket."""
    if shader_socket is None or depth > 32:
        return []
    node = shader_socket.node
    if node.type == 'GROUP' and node.node_tree:
        output = _group_output(node.node_tree)
        inner = next(
            (socket for socket in output.inputs if socket.identifier == shader_socket.identifier),
            None,
        ) if output else None
        return _shader_alphas(_linked_from(inner), [*groups, node], depth + 1) if inner else []
    if node.type == 'GROUP_INPUT' and groups:
        outer = groups[-1]
        outer_input = next(
            (socket for socket in outer.inputs if socket.identifier == shader_socket.identifier),
            None,
        )
        return _shader_alphas(_linked_from(outer_input), groups[:-1], depth + 1) if outer_input else []
    if node.type == 'BSDF_PRINCIPLED':
        return [_alpha_value(node.inputs["Alpha"], groups)]
    if node.type == 'MIX_SHADER':
        factor, first, second = node.inputs[0], _linked_from(node.inputs[1]), _linked_from(node.inputs[2])
        for transparent, other, invert in ((first, second, False), (second, first, True)):
            if transparent is not None and transparent.node.type == 'BSDF_TRANSPARENT':
                # Factor 0 shows the first input: with Transparent there, the
                # factor itself is the opacity.
                others = _shader_alphas(other, groups, depth + 1)
                if any(kind != 'VALUE' or value < 0.999 for kind, value, *_ in others):
                    raise PipelineBakeError("transparency is combined from several sources")
                return [_alpha_value(factor, groups, invert)]
        return _shader_alphas(first, groups, depth + 1) + _shader_alphas(second, groups, depth + 1)
    if node.type == 'ADD_SHADER':
        return (
            _shader_alphas(_linked_from(node.inputs[0]), groups, depth + 1)
            + _shader_alphas(_linked_from(node.inputs[1]), groups, depth + 1)
        )
    if node.type == 'BSDF_TRANSPARENT':
        return [('VALUE', 0.0)]
    return []


def find_alpha_source(material):
    """Where a material's transparency comes from, for the Alpha layer.

    Accepts a Principled Alpha input, or a Mix Shader with a Transparent BSDF
    on one side (its Factor is the opacity), also inside node groups when the
    value comes in through a group input. Returns ('SOCKET', output socket in
    the material's own tree, invert) or ('VALUE', value); raises
    PipelineBakeError when there is no transparency or it is ambiguous."""
    if not material or not material.use_nodes or not material.node_tree:
        raise PipelineBakeError("material has no nodes")
    outputs = [node for node in material.node_tree.nodes if node.type == 'OUTPUT_MATERIAL']
    output = next((node for node in outputs if node.is_active_output), outputs[0] if outputs else None)
    if not output:
        raise PipelineBakeError("material has no Material Output")
    found = _shader_alphas(_linked_from(output.inputs["Surface"]), [])
    # One shader often reaches the output twice (a Principled feeding both a
    # Mix and an Add with Translucent): the same source counts once.
    unique = {}
    for item in found:
        key = (item[0], item[1].as_pointer(), item[2]) if item[0] == 'SOCKET' else (item[0], round(item[1], 4))
        unique.setdefault(key, item)
    transparent = [item for item in unique.values() if item[0] != 'VALUE' or item[1] < 0.999]
    if not transparent:
        raise PipelineBakeError(
            "no transparency found (Principled Alpha, or a Mix Shader with Transparent BSDF)"
        )
    if len(transparent) > 1:
        raise PipelineBakeError("transparency comes from several shaders; keep one source")
    return transparent[0]


def make_fallback_material(name):
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


def set_target_image(receiver, image):
    for node in receiver["target_nodes"]:
        node.image = image
        node.id_data.nodes.active = node


def copy_receiver(context, source, work_collection, image, receiver_type, material_map=None):
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
    # new_from_object() keeps the mesh's materials; a slot linked to the object
    # renders the object's material instead (for example on linked duplicates).
    for index, slot in enumerate(source.material_slots):
        if slot.link == 'OBJECT' and index < len(evaluated_materials):
            evaluated_materials[index] = slot.material
    if not evaluated_materials:
        evaluated_materials = [None]
    for index, source_material in enumerate(evaluated_materials):
        # A material variant bakes with its material in place of the one it
        # replaces, on the copy only; the source is never changed.
        if material_map and source_material and source_material.name_full in material_map:
            source_material = material_map[source_material.name_full]
        material = source_material.copy() if source_material else make_fallback_material(f"__PMVR_WORK_MAT_{uuid.uuid4().hex}")
        material.name = f"__PMVR_WORK_MAT_{uuid.uuid4().hex}"
        if receiver_type == 'LIGHTMAP':
            _wrap_surface_for_bake(material)
        if receiver_type == 'PBR':
            for principled in principled_nodes(material):
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
        bad_faces = sum(
            1 for polygon in mesh.polygons if polygon.material_index >= len(mesh.materials)
        )
        raise PipelineBakeError(
            f"{source.name}: {bad_faces} face(s) use material slot(s) "
            f"{invalid_material_indices[:3]} but the object has {len(mesh.materials)}; "
            "in Edit Mode select all and Assign a material"
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


def select_only(context, obj):
    for selected in list(context.selected_objects):
        selected.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def supported_bake_kwargs(kwargs):
    try:
        supported = {prop.identifier for prop in bpy.ops.object.bake.get_rna_type().properties}
        return {key: value for key, value in kwargs.items() if key in supported}
    except Exception:
        return kwargs


def bake_receivers(
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
            set_target_image(receiver, image)
            select_only(context, receiver["object"])
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
                result = bpy.ops.object.bake(**supported_bake_kwargs(kwargs))
            except RuntimeError as exc:
                if "cancel" in str(exc).lower():
                    raise PipelineBakeCancelled(str(exc)) from exc
                raise
            if 'FINISHED' not in result:
                raise PipelineBakeCancelled(f"{bake_type} bake was cancelled")
    finally:
        config.restore()


SIGNATURE_VERSION = "2:"


def signature_for_receivers(receivers, layer_type=""):
    """Structure a result was baked for: topology and material slots of the
    evaluated mesh, the transform, the modifier stack and the authored
    SimpleBake UVs. Evaluated UVs are not used: Bevel (multi-threaded)
    returns them a float step apart on every evaluation, which made Day
    and Evening of the same object look incompatible."""
    digest = hashlib.sha256()
    digest.update(layer_type.encode())
    for receiver in sorted(receivers, key=lambda item: item["source"].pm_vr_pipeline.source_id):
        source = receiver["source"]
        mesh = receiver["mesh"]
        digest.update(source.pm_vr_pipeline.source_id.encode())
        digest.update(f"{len(mesh.vertices)}:{len(mesh.edges)}:{len(mesh.polygons)}:{len(mesh.loops)}".encode())
        digest.update(array('f', [value for row in source.matrix_world for value in row]).tobytes())
        digest.update(",".join(m.type for m in source.modifiers if m.show_render).encode())
        authored = getattr(source.data, "uv_layers", None)
        uv = (authored.get(BAKE_UV_NAME) if authored else None) or mesh.uv_layers.get(BAKE_UV_NAME)
        coords = array('f', [0.0]) * (len(uv.data) * 2)
        uv.data.foreach_get("uv", coords)
        digest.update(coords.tobytes())
        indices = array('i', [0]) * len(mesh.polygons)
        if indices:
            mesh.polygons.foreach_get("material_index", indices)
            digest.update(indices.tobytes())
        digest.update(str(len(mesh.materials)).encode())
    digest.update(str(SCHEMA_VERSION).encode())
    return SIGNATURE_VERSION + digest.hexdigest()


def same_structure(first, second):
    """Whether two stored signatures describe the same structure. One from
    before signature version 2 cannot be compared with a current one; they
    count as the same, so older results need no rebake."""
    if first.startswith(SIGNATURE_VERSION) != second.startswith(SIGNATURE_VERSION):
        return True
    return first == second
