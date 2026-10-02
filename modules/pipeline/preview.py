"""Viewport-only preview choices and the successfully committed queue results."""
import json
import bpy

from . import looks
from .constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID
from .ui_sections import section

_changing = False


def get_mode(project):
    return 2 if project.show_sources and project.show_generated else (1 if project.show_generated else 0)


def set_mode(project, value):
    global _changing
    if _changing:
        return
    _changing = True
    try:
        project.show_sources = value != 1
        project.show_generated = value != 0
        project.preview_format_version = 1
    finally:
        _changing = False
    apply(project)


def legacy_changed(project, sources):
    global _changing
    if _changing or project.operation_running:
        return
    _changing = True
    try:
        if sources and project.show_sources:
            project.show_generated = False
        elif not sources and project.show_generated:
            project.show_sources = False
    finally:
        _changing = False
    apply(project)


def apply(project):
    if project.operation_running or project.id_data != bpy.context.scene:
        return
    from .setup_ops import preview_generated, preview_sources
    preview_sources(project)
    preview_generated(project)


def scope_changed(project, _context):
    apply(project)


def migrate(project):
    # Legacy files could store both switches. Start with their sources;
    # Both is now an explicit choice. No render visibility is authored here.
    if not project.preview_format_version:
        if project.show_sources == project.show_generated:
            set_mode(project, 0)
        project.preview_format_version = 1
    # Old saves can have visibility flags left by the bake snapshots. Apply
    # the current scene's chosen view; a different scene must not override it.
    apply(project)


def matches(project, obj):
    if not project.preview_last_queue:
        return True
    state = looks.active_id(project)
    source_id = obj.get(TAG_SOURCE_ID, '')
    return any(item.look_id == state and item.mode == project.bake_mode
               and item.unit_id == obj.get(TAG_UNIT_ID)
               and source_id in json.loads(item.source_ids or '[]')
               for item in project.preview_results)


def completed(unit_id, state, mode, members):
    return (unit_id, state, mode, [obj.pm_vr_pipeline.source_id for obj in members])


def finish(context, results, preferred_state):
    """Apply after every bake/scenario visibility snapshot has been restored."""
    project = context.scene.pm_vr_project
    project.preview_results.clear()
    for unit_id, state, mode, source_ids in results:
        item = project.preview_results.add()
        item.unit_id, item.look_id, item.mode = unit_id, state, mode
        item.source_ids = json.dumps(source_ids)
    states = {result[1] for result in results}
    state = preferred_state if not results or preferred_state in states else results[-1][1]
    from .state import activate_state
    from .setup_ops import preview_state
    activate_state(context, state)
    preview_state(project)
    project.preview_last_queue = True
    set_mode(project, 1)
    # The Shader Editor should follow a visible result, rather than a hidden
    # source restored by the bake's context snapshot.
    from .selection_sync import navigation_guard
    objects = [obj for obj in context.view_layer.objects if obj.get(TAG_GENERATED)
               and obj.get(TAG_MODE) == project.bake_mode and matches(project, obj)
               and obj.visible_get(view_layer=context.view_layer)]
    active = context.view_layer.objects.active
    unit_id = (active.get(TAG_UNIT_ID) if active.get(TAG_GENERATED) else active.pm_vr_pipeline.bake_unit_id) if active else ''
    chosen = [obj for obj in objects if obj.get(TAG_UNIT_ID) == unit_id]
    if not chosen and objects:
        unit_id = objects[-1].get(TAG_UNIT_ID)
        chosen = [obj for obj in objects if obj.get(TAG_UNIT_ID) == unit_id]
    with navigation_guard():
        for obj in context.view_layer.objects:
            if obj.select_get():
                obj.select_set(False)
        for obj in chosen:
            obj.select_set(True)
        context.view_layer.objects.active = chosen[0] if chosen else None


def draw(layout, project):
    stage = project.id_data.pm_vr_ui_state.stage.lower()
    box = section(layout, 'pmvr_' + stage + '_viewport', 'Viewport', 'HIDE_OFF')
    if box is None:
        return
    box.enabled = not project.operation_running
    box.row(align=True).prop(project, 'preview_mode', expand=True)
    if project.show_generated and (project.preview_results or project.preview_last_queue):
        box.prop(project, 'preview_last_queue', text='Last queue only')
        if project.preview_last_queue:
            count = sum(item.look_id == looks.active_id(project) and item.mode == project.bake_mode
                        for item in project.preview_results)
            box.label(text=f'{count} committed unit(s) in this lighting look', icon='CHECKMARK')
