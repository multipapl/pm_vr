"""Keep Setup list navigation synchronized with the active viewport object."""

import bpy

from .constants import TAG_GENERATED, TAG_LAYER_ID, TAG_SOURCE_ID, TAG_UNIT_ID


_MSGBUS_OWNER = object()
_timer_pending = False
_applying = False


def _source_from_id(source_id):
    if not source_id:
        return None
    for obj in bpy.data.objects:
        metadata = getattr(obj, "pm_vr_pipeline", None)
        if metadata and metadata.source_id == source_id:
            return obj
    return None


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
            source = _source_from_id(obj.get(TAG_SOURCE_ID, ""))
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
    if ui_state and ui_state.stage != 'SETUP':
        return False
    project = getattr(scene, "pm_vr_project", None)
    if not project or not project.initialized:
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
    elif project.active_bake_unit_index != previous_unit_index:
        # Export Original and other non-bake sources navigate the layer list
        # only. Undo the layer-index callback's convenience unit selection.
        project.active_bake_unit_index = previous_unit_index
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


def register():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.LayerObjects, "active"),
        owner=_MSGBUS_OWNER,
        args=(),
        notify=request_sync,
        options={'PERSISTENT'},
    )


def unregister():
    global _timer_pending
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    if bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _timer_pending = False
