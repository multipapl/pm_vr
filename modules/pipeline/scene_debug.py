"""Interactive controller and UI for viewport diagnostic channels."""

import bpy
from bpy.app.handlers import persistent

from . import viewport_overlay
from .constants import DEBUG_MODE_ITEMS, GENERAL_DEBUG_MODES


DEBUG_MODES = tuple(item[0] for item in DEBUG_MODE_ITEMS)
DEBUG_MODE_LABELS = {identifier: label for identifier, label, _description in DEBUG_MODE_ITEMS}
DIRECT_MODE_KEYS = {
    key: mode
    for key, mode in zip(
        ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT'),
        DEBUG_MODES,
    )
}

_operator_running = False
_stop_requested = False
_session_generation = 0
_addon_keymaps = []


def _available_modes(project):
    if project and project.initialized:
        return DEBUG_MODES
    return tuple(mode for mode in DEBUG_MODES if mode in GENERAL_DEBUG_MODES)


def _available_mode_items(_operator, context):
    project = getattr(getattr(context, "scene", None), "pm_vr_project", None)
    available = set(_available_modes(project))
    return tuple(item for item in DEBUG_MODE_ITEMS if item[0] in available)


def draw_controls(layout, context, project):
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Scene Debug", icon='OVERLAY')
    header.operator(
        PMVR_OT_SceneDebugToggle.bl_idname,
        text="Stop Debug" if _operator_running else "Start Debug",
        depress=_operator_running,
    )
    box.operator_menu_enum(
        PMVR_OT_SceneDebugSetMode.bl_idname,
        "mode",
        text=DEBUG_MODE_LABELS.get(project.overlay_mode, "Choose Mode"),
        icon='DOWNARROW_HLT',
    )
    if project.overlay_mode == 'OFF':
        if not project.initialized:
            box.label(text="Pipeline modes 1–3 require Initialize", icon='INFO')
        return

    box.prop(project, "overlay_opacity", text="Intensity", slider=True)
    if project.overlay_mode == 'UV_CHECKER':
        box.row(align=True).prop(project, "debug_checker_uv", expand=True)
        if hasattr(context.scene, "pm_vr_checker_tiling"):
            box.prop(context.scene, "pm_vr_checker_tiling", text="Tiling")
    if project.overlay_mode in {'BAKE_STATUS', 'RENDER_LAYERS', 'BAKE_UNITS'}:
        box.prop(project, "overlay_show_unassigned", toggle=True)
    if project.overlay_mode == 'BAKE_STATUS':
        state = project.active_lighting_state.title()
        mode = "Beauty" if project.bake_mode == 'BEAUTY' else "Lightmap"
        box.label(text=f"Showing {state} · {mode}", icon='INFO')
    if _operator_running:
        if project.initialized:
            box.label(text="1–8 switch modes · [ ] cycle · Esc exit")
        else:
            box.label(text="4–8 switch modes · [ ] cycle · Esc exit")


def _set_status_text(context, text=None):
    workspace = getattr(context, "workspace", None)
    if workspace:
        workspace.status_text_set(text)


def _clear_all_status_text():
    for workspace in bpy.data.workspaces:
        try:
            workspace.status_text_set(None)
        except (ReferenceError, RuntimeError):
            pass


def _set_mode(context, mode):
    project = getattr(context.scene, "pm_vr_project", None)
    if not project or mode not in _available_modes(project):
        return False
    if mode == 'UV_CHECKER':
        viewport_overlay.prepare_checker()
    if mode == 'TEXEL_DENSITY':
        viewport_overlay.invalidate_texel_cache()
    project.overlay_mode = mode
    viewport_overlay.tag_redraw()
    return True


class PMVR_OT_SceneDebugToggle(bpy.types.Operator):
    bl_idname = "pmvr.scene_debug_toggle"
    bl_label = "Toggle Scene Debug"
    bl_description = "Start or stop viewport diagnostics (Ctrl+Shift+D)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        project = getattr(getattr(context, "scene", None), "pm_vr_project", None)
        return bool(
            context.area
            and context.area.type == 'VIEW_3D'
            and project
        )

    def execute(self, context):
        global _operator_running, _stop_requested, _session_generation
        if _operator_running:
            _stop_requested = True
            context.scene.pm_vr_project.overlay_mode = 'OFF'
            _set_status_text(context)
            viewport_overlay.tag_redraw()
            return {'FINISHED'}

        project = context.scene.pm_vr_project
        if project.overlay_mode == 'OFF':
            project.overlay_mode = (
                'BAKE_STATUS' if project.initialized else 'UV_HEALTH'
            )
        elif project.overlay_mode not in _available_modes(project):
            project.overlay_mode = 'UV_HEALTH'
        _operator_running = True
        _stop_requested = False
        _session_generation += 1
        self._session_generation = _session_generation
        context.window_manager.modal_handler_add(self)
        controls = (
            "1 Bake  2 Layers  3 Units  4 UV  5 TD  6 Checker  7 Scale  "
            "8 Linked"
            if project.initialized
            else "4 UV  5 TD  6 Checker  7 Scale  8 Linked"
        )
        _set_status_text(
            context,
            f"PM VR Scene Debug  |  {controls}  [ ] Cycle  Esc Exit",
        )
        viewport_overlay.tag_redraw()
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        global _operator_running, _stop_requested
        _operator_running = False
        _stop_requested = False
        project = getattr(context.scene, "pm_vr_project", None)
        if project:
            project.overlay_mode = 'OFF'
        _set_status_text(context)
        viewport_overlay.tag_redraw()
        return {'FINISHED'}

    def modal(self, context, event):
        if getattr(self, "_session_generation", -1) != _session_generation:
            return {'FINISHED'}
        if _stop_requested:
            return self._finish(context)
        project = getattr(context.scene, "pm_vr_project", None)
        if not project or project.overlay_mode == 'OFF':
            return self._finish(context)
        if project.overlay_mode not in _available_modes(project):
            project.overlay_mode = 'UV_HEALTH'

        if event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if event.type == 'ESC' or (
            event.type == 'D' and event.ctrl and event.shift
        ):
            return self._finish(context)

        mode = DIRECT_MODE_KEYS.get(event.type)
        if mode and not (event.ctrl or event.shift or event.alt or event.oskey):
            if _set_mode(context, mode):
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}

        if event.type in {'LEFT_BRACKET', 'RIGHT_BRACKET'}:
            available_modes = _available_modes(project)
            current = project.overlay_mode
            if current not in available_modes:
                current = available_modes[0]
            offset = -1 if event.type == 'LEFT_BRACKET' else 1
            index = (available_modes.index(current) + offset) % len(available_modes)
            _set_mode(context, available_modes[index])
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class PMVR_OT_SceneDebugSetMode(bpy.types.Operator):
    bl_idname = "pmvr.scene_debug_set_mode"
    bl_label = "Set Scene Debug Mode"
    bl_description = "Switch the diagnostic channel and enable debug hotkeys"
    bl_options = {'INTERNAL'}

    mode: bpy.props.EnumProperty(
        items=_available_mode_items,
    )

    @classmethod
    def poll(cls, context):
        return PMVR_OT_SceneDebugToggle.poll(context)

    def execute(self, context):
        if not _set_mode(context, self.mode):
            self.report({'WARNING'}, "Initialize the pipeline to use this debug mode")
            return {'CANCELLED'}
        if not _operator_running:
            return bpy.ops.pmvr.scene_debug_toggle('INVOKE_DEFAULT')
        return {'FINISHED'}


@persistent
def _load_post(_filepath):
    global _operator_running, _stop_requested, _session_generation
    _session_generation += 1
    _operator_running = False
    _stop_requested = False
    _clear_all_status_text()


def _ensure_keymap():
    if _addon_keymaps:
        return None
    window_manager = getattr(bpy.context, "window_manager", None)
    key_configs = getattr(window_manager, "keyconfigs", None)
    key_config = getattr(key_configs, "addon", None)
    if key_config is None:
        return 0.25

    keymap = key_config.keymaps.new(name='3D View', space_type='VIEW_3D')
    keymap_item = keymap.keymap_items.new(
        PMVR_OT_SceneDebugToggle.bl_idname,
        type='D',
        value='PRESS',
        ctrl=True,
        shift=True,
    )
    _addon_keymaps.append((keymap, keymap_item))
    return None


def register():
    bpy.utils.register_class(PMVR_OT_SceneDebugToggle)
    bpy.utils.register_class(PMVR_OT_SceneDebugSetMode)
    if not bpy.app.timers.is_registered(_ensure_keymap):
        bpy.app.timers.register(_ensure_keymap, first_interval=0.1)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    global _operator_running, _stop_requested, _session_generation
    for keymap, keymap_item in reversed(_addon_keymaps):
        keymap.keymap_items.remove(keymap_item)
    _addon_keymaps.clear()
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    _operator_running = False
    _stop_requested = True
    _session_generation += 1
    _clear_all_status_text()
    if bpy.app.timers.is_registered(_ensure_keymap):
        bpy.app.timers.unregister(_ensure_keymap)
    bpy.utils.unregister_class(PMVR_OT_SceneDebugSetMode)
    bpy.utils.unregister_class(PMVR_OT_SceneDebugToggle)
