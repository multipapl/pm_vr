import os

import bpy
from bpy.app.handlers import persistent


_ASSET_FILE = "PMVR_UV_Checker.png"
_MATERIAL_NAME = "PMVR_CheckerPreview"
_OWNER_KEY = "pm_vr_checker_preview"
_SHADER_VERSION_KEY = "pm_vr_checker_shader_version"
_SHADER_VERSION = 3
_MAPPING_NODE = "PMVR Checker Mapping"
_DIFFUSE_NODE = "PMVR Checker Diffuse"
_render_suspended = {}
_scene_suspensions = {}


class PMVR_CheckerSlotState(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    slot_index: bpy.props.IntProperty(default=-1)
    original_link: bpy.props.StringProperty(default='DATA')
    original_material: bpy.props.PointerProperty(type=bpy.types.Material)
    local_enabled: bpy.props.BoolProperty(default=False)
    global_enabled: bpy.props.BoolProperty(default=False)


class PMVR_CheckerMeshState(bpy.types.PropertyGroup):
    mesh: bpy.props.PointerProperty(type=bpy.types.Mesh)


def _asset_path():
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(addon_root, "assets", _ASSET_FILE)


def _canonical_material():
    exact = bpy.data.materials.get(_MATERIAL_NAME)
    owned = [material for material in bpy.data.materials if material.get(_OWNER_KEY)]
    if exact and exact not in owned:
        raise RuntimeError(
            f'Material name "{_MATERIAL_NAME}" is already used by non-PM VR data'
        )
    canonical = exact or (owned[0] if owned else None)
    if canonical is None:
        return None
    if canonical.name != _MATERIAL_NAME:
        canonical.name = _MATERIAL_NAME
    for duplicate in tuple(owned):
        if duplicate == canonical:
            continue
        duplicate.user_remap(canonical)
        bpy.data.materials.remove(duplicate)
    return canonical


def _load_image():
    path = _asset_path()
    if not os.path.isfile(path):
        raise RuntimeError(f"Checker asset is missing: {path}")
    normalized = os.path.normcase(os.path.abspath(path))
    for image in bpy.data.images:
        image_path = bpy.path.abspath(image.filepath, library=image.library)
        if image_path and os.path.normcase(os.path.abspath(image_path)) == normalized:
            return image
    image = bpy.data.images.load(path, check_existing=True)
    image[_OWNER_KEY] = True
    try:
        image.colorspace_settings.name = 'sRGB'
    except (TypeError, ValueError):
        pass
    return image


def _set_material_tiling(material, tiling):
    if not material or not material.use_nodes or not material.node_tree:
        return
    mapping = material.node_tree.nodes.get(_MAPPING_NODE)
    if mapping and mapping.inputs.get("Scale"):
        value = float(tiling)
        mapping.inputs["Scale"].default_value = (value, value, value)


def _build_material(tiling, material=None):
    material = material or bpy.data.materials.new(_MATERIAL_NAME)
    material[_OWNER_KEY] = True
    material[_SHADER_VERSION_KEY] = _SHADER_VERSION
    material.use_fake_user = True
    material.use_nodes = True
    material.diffuse_color = (0.36, 0.58, 0.51, 1.0)

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    coordinates = nodes.new('ShaderNodeTexCoord')
    coordinates.name = "PMVR Checker UV"
    coordinates.location = (-620, 0)
    mapping = nodes.new('ShaderNodeMapping')
    mapping.name = _MAPPING_NODE
    mapping.location = (-420, 0)
    texture = nodes.new('ShaderNodeTexImage')
    texture.name = "PMVR Checker Image"
    texture.location = (-180, 0)
    texture.image = _load_image()
    texture.interpolation = 'Linear'
    texture.extension = 'REPEAT'
    diffuse = nodes.new('ShaderNodeBsdfDiffuse')
    diffuse.name = _DIFFUSE_NODE
    diffuse.location = (80, 0)
    diffuse.inputs["Roughness"].default_value = 0.6
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (320, 0)
    links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
    links.new(texture.outputs["Color"], diffuse.inputs["Color"])
    links.new(diffuse.outputs["BSDF"], output.inputs["Surface"])
    _set_material_tiling(material, tiling)
    return material


def ensure_material(scene):
    material = _canonical_material()
    if (
        material is None
        or material.get(_SHADER_VERSION_KEY) != _SHADER_VERSION
        or not material.use_nodes
        or not material.node_tree
        or not material.node_tree.nodes.get(_DIFFUSE_NODE)
    ):
        material = _build_material(scene.pm_vr_checker_tiling, material)
    else:
        material.use_fake_user = True
        _set_material_tiling(material, scene.pm_vr_checker_tiling)
    return material


def _slot_states(view_layer, obj):
    return [state for state in view_layer.pm_vr_checker_slot_states if state.object == obj]


def _has_local_override(view_layer, obj):
    return any(state.local_enabled for state in _slot_states(view_layer, obj))


def _ensure_placeholder(view_layer, mesh):
    if len(mesh.materials):
        return
    mesh.materials.append(None)
    if not any(state.mesh == mesh for state in view_layer.pm_vr_checker_mesh_states):
        state = view_layer.pm_vr_checker_mesh_states.add()
        state.mesh = mesh


def _apply_object_override(scene, view_layer, obj, local=False, global_scope=False):
    states = _slot_states(view_layer, obj)
    created_states = not states
    material = ensure_material(scene)
    if not states:
        try:
            _ensure_placeholder(view_layer, obj.data)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return 0, 1
        for slot_index, slot in enumerate(obj.material_slots):
            state = view_layer.pm_vr_checker_slot_states.add()
            state.object = obj
            state.slot_index = slot_index
            state.original_link = slot.link
            if slot.link == 'OBJECT':
                state.original_material = slot.material
            state.local_enabled = local
            state.global_enabled = global_scope
        states = _slot_states(view_layer, obj)
    else:
        for state in states:
            state.local_enabled = state.local_enabled or local
            state.global_enabled = state.global_enabled or global_scope

    applied = skipped = 0
    for state in states:
        if state.slot_index < 0 or state.slot_index >= len(obj.material_slots):
            skipped += 1
            continue
        try:
            slot = obj.material_slots[state.slot_index]
            slot.link = 'OBJECT'
            slot.material = material
            applied += 1
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            skipped += 1
    if created_states and not applied:
        _remove_object_scope(
            view_layer,
            obj,
            local=local,
            global_scope=global_scope,
        )
    return applied, skipped


def _restore_state(state):
    obj = state.object
    if not obj or state.slot_index < 0 or state.slot_index >= len(obj.material_slots):
        return
    slot = obj.material_slots[state.slot_index]
    slot.link = state.original_link
    if state.original_link == 'OBJECT':
        slot.material = state.original_material


def _referenced_meshes(view_layer):
    return {
        state.object.data.as_pointer()
        for state in view_layer.pm_vr_checker_slot_states
        if state.object and state.object.type == 'MESH' and state.object.data
    }


def _cleanup_mesh_states(view_layer):
    referenced = _referenced_meshes(view_layer)
    for index in reversed(range(len(view_layer.pm_vr_checker_mesh_states))):
        state = view_layer.pm_vr_checker_mesh_states[index]
        mesh = state.mesh
        if mesh and mesh.as_pointer() in referenced:
            continue
        try:
            if mesh and len(mesh.materials) == 1 and mesh.materials[0] is None:
                mesh.materials.pop(index=0)
        except (ReferenceError, RuntimeError, TypeError):
            pass
        view_layer.pm_vr_checker_mesh_states.remove(index)


def _remove_object_scope(view_layer, obj, local=False, global_scope=False):
    states = _slot_states(view_layer, obj)
    for state in states:
        if local:
            state.local_enabled = False
        if global_scope:
            state.global_enabled = False
    if any(state.local_enabled or state.global_enabled for state in states):
        return
    for state in states:
        try:
            _restore_state(state)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    for index in reversed(range(len(view_layer.pm_vr_checker_slot_states))):
        if view_layer.pm_vr_checker_slot_states[index].object == obj:
            view_layer.pm_vr_checker_slot_states.remove(index)
    _cleanup_mesh_states(view_layer)


def _restore_originals(view_layer):
    for state in view_layer.pm_vr_checker_slot_states:
        try:
            _restore_state(state)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass
    for state in view_layer.pm_vr_checker_mesh_states:
        mesh = state.mesh
        try:
            if mesh and len(mesh.materials) == 1 and mesh.materials[0] is None:
                mesh.materials.pop(index=0)
        except (ReferenceError, RuntimeError, TypeError):
            pass


def _reapply_overrides(scene, view_layer):
    material = ensure_material(scene)
    for state in view_layer.pm_vr_checker_mesh_states:
        mesh = state.mesh
        if mesh and len(mesh.materials) == 0:
            try:
                mesh.materials.append(None)
            except (ReferenceError, RuntimeError, TypeError):
                pass
    for state in view_layer.pm_vr_checker_slot_states:
        obj = state.object
        if not obj or state.slot_index < 0 or state.slot_index >= len(obj.material_slots):
            continue
        try:
            slot = obj.material_slots[state.slot_index]
            slot.link = 'OBJECT'
            slot.material = material
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass


def _clear_layer(view_layer):
    had_state = bool(
        len(view_layer.pm_vr_checker_slot_states)
        or len(view_layer.pm_vr_checker_mesh_states)
        or view_layer.pm_vr_checker_enabled
    )
    _restore_originals(view_layer)
    view_layer.pm_vr_checker_slot_states.clear()
    view_layer.pm_vr_checker_mesh_states.clear()
    view_layer.pm_vr_checker_enabled = False
    return had_state


def _disable_other_layers(active_view_layer):
    for scene in bpy.data.scenes:
        for view_layer in scene.view_layers:
            if view_layer != active_view_layer:
                _clear_layer(view_layer)


def suspend_scene(scene):
    """Temporarily restore all checker-managed objects in a scene."""
    key = scene.as_pointer()
    existing = _scene_suspensions.get(key)
    if existing:
        existing["depth"] += 1
        return key
    suspended = []
    for view_layer in scene.view_layers:
        if len(view_layer.pm_vr_checker_slot_states):
            _restore_originals(view_layer)
            suspended.append((scene, view_layer))
    if not suspended:
        return None
    _scene_suspensions[key] = {
        "depth": 1,
        "layers": suspended,
    }
    return key


def restore_suspended(token):
    if token is None:
        return
    suspension = _scene_suspensions.get(token)
    if not suspension:
        return
    suspension["depth"] -= 1
    if suspension["depth"] > 0:
        return
    _scene_suspensions.pop(token, None)
    for scene, view_layer in suspension["layers"]:
        try:
            if len(view_layer.pm_vr_checker_slot_states):
                _reapply_overrides(scene, view_layer)
        except ReferenceError:
            pass


def _update_tiling(scene, _context):
    material = _canonical_material()
    if material:
        _set_material_tiling(material, scene.pm_vr_checker_tiling)


class PM_OT_VR_ToggleGlobalChecker(bpy.types.Operator):
    bl_idname = "pm_vr.toggle_global_checker"
    bl_label = "Global Checker"
    bl_description = "Toggle checker preview on all mesh objects in the current scene"

    def execute(self, context):
        view_layer = context.view_layer
        if view_layer.pm_vr_checker_enabled:
            objects = {
                state.object for state in view_layer.pm_vr_checker_slot_states
                if state.object and state.global_enabled
            }
            for obj in objects:
                _remove_object_scope(view_layer, obj, global_scope=True)
            view_layer.pm_vr_checker_enabled = False
            self.report({'INFO'}, "Global Checker disabled")
            return {'FINISHED'}

        _disable_other_layers(view_layer)
        applied = skipped = 0
        for obj in context.scene.objects:
            if obj.type != 'MESH' or not obj.data:
                continue
            added, missed = _apply_object_override(
                context.scene,
                view_layer,
                obj,
                global_scope=True,
            )
            applied += added
            skipped += missed
        if not applied:
            self.report({'WARNING'}, "No editable mesh material slots found")
            return {'CANCELLED'}
        view_layer.pm_vr_checker_enabled = True
        message = f"Global Checker enabled on {applied} material slot(s)"
        if skipped:
            message += f", {skipped} skipped"
        self.report({'INFO'}, message)
        return {'FINISHED'}


class PM_OT_VR_ToggleSelectedChecker(bpy.types.Operator):
    bl_idname = "pm_vr.toggle_selected_checker"
    bl_label = "Selected Checker"
    bl_description = "Toggle persistent viewport checker overrides on selected mesh objects"

    @classmethod
    def poll(cls, context):
        return bool(
            not context.view_layer.pm_vr_checker_enabled
            and any(obj.type == 'MESH' for obj in context.selected_objects)
        )

    def execute(self, context):
        view_layer = context.view_layer
        _disable_other_layers(view_layer)
        selected = [
            obj for obj in context.selected_objects
            if obj.type == 'MESH' and obj.data
        ]
        turn_off = bool(selected) and all(
            _has_local_override(view_layer, obj) for obj in selected
        )
        applied = skipped = 0
        for obj in selected:
            if turn_off:
                _remove_object_scope(view_layer, obj, local=True)
            else:
                added, missed = _apply_object_override(
                    context.scene,
                    view_layer,
                    obj,
                    local=True,
                )
                applied += added
                skipped += missed
        if turn_off:
            self.report({'INFO'}, f"Removed Checker from {len(selected)} selected object(s)")
        else:
            message = f"Enabled Checker on {len(selected)} selected object(s)"
            if skipped:
                message += f", {skipped} slot(s) skipped"
            self.report({'INFO'}, message)
        return {'FINISHED'} if selected else {'CANCELLED'}


class PM_OT_VR_ClearCheckerOverrides(bpy.types.Operator):
    bl_idname = "pm_vr.clear_checker_overrides"
    bl_label = "Clear All Checker Overrides"
    bl_description = "Restore original materials for every checker-managed object in this scene"

    def execute(self, context):
        cleared = sum(
            _clear_layer(view_layer)
            for view_layer in context.scene.view_layers
        )
        self.report(
            {'INFO'} if cleared else {'WARNING'},
            "All Checker overrides cleared" if cleared else "No Checker overrides to clear",
        )
        return {'FINISHED'}


@persistent
def _suspend_for_render(scene, _depsgraph=None):
    token = suspend_scene(scene)
    if token:
        _render_suspended[scene.as_pointer()] = token


@persistent
def _restore_after_render(scene, _depsgraph=None):
    token = _render_suspended.pop(scene.as_pointer(), ()) if scene else ()
    restore_suspended(token)


def draw_ui(layout, context):
    scene = context.scene
    view_layer = context.view_layer
    column = layout.column(align=True)
    column.label(text="UV Checker:")
    column.operator(
        PM_OT_VR_ToggleGlobalChecker.bl_idname,
        text="Global On" if view_layer.pm_vr_checker_enabled else "Global Off",
        icon='UV',
        depress=view_layer.pm_vr_checker_enabled,
    )
    row = column.row(align=True)
    selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
    all_local = bool(selected) and all(
        _has_local_override(view_layer, obj) for obj in selected
    )
    selected_button = row.row(align=True)
    selected_button.enabled = not view_layer.pm_vr_checker_enabled
    selected_button.operator(
        PM_OT_VR_ToggleSelectedChecker.bl_idname,
        text="Selected Off" if all_local else "Selected On",
        icon='RESTRICT_SELECT_OFF',
        depress=all_local,
    )
    row.operator(PM_OT_VR_ClearCheckerOverrides.bl_idname, text="Clear All", icon='X')
    column.prop(scene, "pm_vr_checker_tiling", text="Tiling")
    local_count = len({
        state.object for state in view_layer.pm_vr_checker_slot_states
        if state.object and state.local_enabled
    })
    column.label(text=f"1024 × 1024 px  ·  Local: {local_count}", icon='IMAGE_DATA')


classes = (
    PMVR_CheckerSlotState,
    PMVR_CheckerMeshState,
    PM_OT_VR_ToggleGlobalChecker,
    PM_OT_VR_ToggleSelectedChecker,
    PM_OT_VR_ClearCheckerOverrides,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pm_vr_checker_tiling = bpy.props.IntProperty(
        name="Checker Tiling",
        description="Number of times the checker image repeats across the 0-1 UV tile",
        default=1,
        min=1,
        max=32,
        soft_max=8,
        update=_update_tiling,
    )
    bpy.types.ViewLayer.pm_vr_checker_enabled = bpy.props.BoolProperty(
        name="Global Checker Enabled", default=False, options={'HIDDEN'},
    )
    bpy.types.ViewLayer.pm_vr_checker_slot_states = bpy.props.CollectionProperty(
        type=PMVR_CheckerSlotState, options={'HIDDEN'},
    )
    bpy.types.ViewLayer.pm_vr_checker_mesh_states = bpy.props.CollectionProperty(
        type=PMVR_CheckerMeshState, options={'HIDDEN'},
    )
    if _suspend_for_render not in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.append(_suspend_for_render)
    if _restore_after_render not in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.append(_restore_after_render)
    if _restore_after_render not in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.append(_restore_after_render)


def unregister():
    for handlers, callback in (
        (bpy.app.handlers.render_cancel, _restore_after_render),
        (bpy.app.handlers.render_post, _restore_after_render),
        (bpy.app.handlers.render_pre, _suspend_for_render),
    ):
        if callback in handlers:
            handlers.remove(callback)
    for token in tuple(_render_suspended.values()):
        restore_suspended(token)
    _render_suspended.clear()
    for token, suspension in tuple(_scene_suspensions.items()):
        suspension["depth"] = 1
        restore_suspended(token)
    for scene in bpy.data.scenes:
        for view_layer in scene.view_layers:
            _clear_layer(view_layer)
    for property_owner, property_name in (
        (bpy.types.ViewLayer, "pm_vr_checker_mesh_states"),
        (bpy.types.ViewLayer, "pm_vr_checker_slot_states"),
        (bpy.types.ViewLayer, "pm_vr_checker_enabled"),
        (bpy.types.Scene, "pm_vr_checker_tiling"),
    ):
        if hasattr(property_owner, property_name):
            delattr(property_owner, property_name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
