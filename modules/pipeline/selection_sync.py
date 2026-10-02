"""Keep Setup list navigation synchronized with the active viewport object."""

import bpy
from contextlib import contextmanager
from bpy.app.handlers import persistent

from .constants import BAKE_LAYER_TYPES, TAG_GENERATED, TAG_LAYER_ID, TAG_SOURCE_ID, TAG_UNIT_ID
from .identity import sources_by_id


_MSGBUS_OWNER = object()
_timer_pending = False
_applying = False
_navigation_depth = 0
_original_targets = {}


@contextmanager
def navigation_guard():
    global _navigation_depth
    _navigation_depth += 1
    try:
        yield
    finally:
        _navigation_depth -= 1


def set_index(project, key, value):
    with navigation_guard():
        setattr(project, key, value)


def original_list_member(project, obj):
    """Same source membership as Setup, without an extra persistent cache."""
    index = project.active_render_layer_index
    if not 0 <= index < len(project.render_layers) or obj.get(TAG_GENERATED):
        return False
    layer = project.render_layers[index]
    metadata = obj.pm_vr_pipeline
    return (metadata.is_registered_source and metadata.render_layer_id == layer.layer_id
            and (layer.layer_type not in BAKE_LAYER_TYPES or metadata.processing_role == 'EXPORT_ORIGINAL'))


def set_original_object(project, obj):
    key = project.id_data.session_uid
    if obj is None:
        _original_targets.pop(key, None)
    else:
        _original_targets[key] = (obj.session_uid, obj.name)


def original_object(project):
    """UI focus without an ID user, a saved reference or stale RNA pointers."""
    key = project.id_data.session_uid
    target = _original_targets.get(key)
    if target is None:
        return None
    uid, name = target
    obj = bpy.data.objects.get(name)
    if obj is None or obj.session_uid != uid:
        # Rename/undo can change the collection index and label; the session
        # identity is unchanged. A replacement with the same name is different.
        obj = next((item for item in bpy.data.objects if item.session_uid == uid), None)
        set_original_object(project, obj)
    return obj


def original_selected(project, context):
    if _applying or _navigation_depth or project.operation_running:
        return
    if not context.view_layer or not context.scene or context.scene.pm_vr_project != project:
        return
    if context.object and context.object.mode != 'OBJECT':
        return
    obj = original_object(project)
    if not obj or not original_list_member(project, obj) or obj.name not in context.view_layer.objects:
        return
    # An original must be visible even if the previous view showed only bakes.
    # This is viewport navigation, never render flags or collection exclusions.
    project.preview_mode = 'SOURCES'
    obj.hide_set(False)
    if not obj.visible_get(view_layer=context.view_layer):
        return
    with navigation_guard():
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
    _redraw_viewports()


def list_selected(project, context, queue=False):
    if _applying or _navigation_depth or project.operation_running:
        return
    if not context.view_layer or not context.scene or context.scene.pm_vr_project != project:
        return
    if context.object and context.object.mode != 'OBJECT':
        return
    items = project.bake_queue if queue else project.bake_units
    index = project.active_bake_queue_index if queue else project.active_bake_unit_index
    if index >= len(items):
        return
    unit_id = items[index].unit_id
    from .identity import unit_members
    from .generated import live_generated_objects
    from . import preview
    objects = (live_generated_objects(unit_id, project.bake_mode)
               if project.show_generated and not project.show_sources else unit_members(unit_id))
    if objects and project.preview_last_queue and project.show_generated and not any(preview.matches(project, obj) for obj in objects):
        project.preview_last_queue = False
    objects = [obj for obj in objects if obj.name in context.view_layer.objects
               and (not obj.get(TAG_GENERATED) or preview.matches(project, obj))
               and obj.visible_get(view_layer=context.view_layer)]
    if not objects:
        return 0
    with navigation_guard():
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
        context.view_layer.objects.active = objects[0]
    _redraw_viewports()
    return len(objects)


def _ownership_for_object(project, obj):
    if not obj:
        return "", ""

    if obj.get(TAG_GENERATED):
        layer_id = obj.get(TAG_LAYER_ID, "")
        unit_id = obj.get(TAG_UNIT_ID, "")
        if unit_id and not layer_id:
            unit = next(
                (item for item in project.bake_units if item.unit_id == unit_id),
                None,
            )
            layer_id = unit.render_layer_id if unit else ""
        if not layer_id:
            source = sources_by_id().get(obj.get(TAG_SOURCE_ID, ""))
            if source:
                metadata = source.pm_vr_pipeline
                layer_id = metadata.render_layer_id
                unit_id = unit_id or metadata.bake_unit_id
        return layer_id, unit_id

    metadata = getattr(obj, "pm_vr_pipeline", None)
    if not metadata or not metadata.is_registered_source:
        return "", ""
    unit_id = (
        metadata.bake_unit_id
        if metadata.processing_role == 'BAKE'
        else ""
    )
    return metadata.render_layer_id, unit_id


def _sync(scene, view_layer):
    ui_state = getattr(scene, "pm_vr_ui_state", None)
    if ui_state and ui_state.stage not in ('SETUP', 'BAKE'):
        return False
    project = getattr(scene, "pm_vr_project", None)
    if not project or not project.initialized or project.operation_running:
        return False
    obj = view_layer.objects.active
    layer_id, unit_id = _ownership_for_object(project, obj)
    if not layer_id:
        return False

    layer_index = next(
        (
            index for index, layer in enumerate(project.render_layers)
            if layer.layer_id == layer_id
        ),
        None,
    )
    if layer_index is None:
        return False

    changed = False
    previous_unit_index = project.active_bake_unit_index
    if project.active_render_layer_index != layer_index:
        project.active_render_layer_index = layer_index
        changed = True

    if unit_id:
        unit_index = next(
            (
                index for index, unit in enumerate(project.bake_units)
                if unit.unit_id == unit_id and unit.render_layer_id == layer_id
            ),
            None,
        )
        if unit_index is not None and project.active_bake_unit_index != unit_index:
            project.active_bake_unit_index = unit_index
            changed = True
        queue_index = next((i for i, item in enumerate(project.bake_queue) if item.unit_id == unit_id), None)
        if queue_index is not None and project.active_bake_queue_index != queue_index:
            project.active_bake_queue_index = queue_index
            changed = True
    elif project.active_bake_unit_index != previous_unit_index:
        # Export Original and other non-bake sources navigate the layer list
        # only. Undo the layer-index callback's convenience unit selection.
        project.active_bake_unit_index = previous_unit_index
    if original_list_member(project, obj) and original_object(project) != obj:
        # _sync is guarded: reverse navigation highlights the row without
        # showing sources or replacing a user's existing multi-selection.
        set_original_object(project, obj)
        changed = True
    return changed


def _redraw_viewports():
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _flush():
    global _timer_pending, _applying
    _timer_pending = False
    if _applying:
        return None
    _applying = True
    changed = False
    try:
        window_manager = getattr(bpy.context, "window_manager", None)
        windows = tuple(getattr(window_manager, "windows", ()))
        if windows:
            seen = set()
            for window in windows:
                scene = window.scene
                view_layer = window.view_layer
                key = (scene.as_pointer(), view_layer.as_pointer())
                if key in seen:
                    continue
                seen.add(key)
                changed = _sync(scene, view_layer) or changed
        else:
            scene = getattr(bpy.context, "scene", None)
            view_layer = getattr(bpy.context, "view_layer", None)
            if scene and view_layer:
                changed = _sync(scene, view_layer)
    finally:
        _applying = False
    if changed:
        _redraw_viewports()
    return None


def request_sync(*_args):
    global _timer_pending
    if _applying or _timer_pending:
        return
    _timer_pending = True
    if not bpy.app.timers.is_registered(_flush):
        bpy.app.timers.register(_flush, first_interval=0.0)


def sync_now(scene, view_layer):
    """Deterministic entry point used by registration and Blender tests."""
    global _applying
    if _applying:
        return False
    _applying = True
    try:
        return _sync(scene, view_layer)
    finally:
        _applying = False


def _subscribe():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=_MSGBUS_OWNER,
        args=(),
        notify=request_sync,
        options={'PERSISTENT'},
    )


@persistent
def _load_post(_filepath):
    # Blender drops every message-bus subscription when a file is loaded.
    _subscribe()
    _original_targets.clear()
    request_sync()


def register():
    _subscribe()
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    global _timer_pending
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _timer_pending = False
    _original_targets.clear()
