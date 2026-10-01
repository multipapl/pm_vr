"""Central validation for setup, bake and export."""

from dataclasses import dataclass

import bpy
import numpy

from .bake_scene import PipelineBakeError, find_alpha_source, principled_nodes
from .constants import BAKE_LAYER_TYPES, BAKE_UV_NAME, PRIMARY_UV_NAME
from .identity import duplicate_source_ids, find_layer, find_unit, layer_members, unit_members
from .scenarios import Scope, Visibility, hidden_member_message, unit_scenario
from .state import PipelineStateError, validate_state_configuration


@dataclass
class Issue:
    severity: str
    message: str
    object_name: str = ""


def object_render_visible(obj, view_layer):
    """Return authored render inclusion, ignoring PMVR viewport preview hiding."""
    if obj.hide_render:
        return False
    try:
        return view_layer.objects.get(obj.name) is obj
    except ReferenceError:
        return False


def _hidden_reason(obj, view_layer):
    if obj.hide_render:
        return "render disabled, camera icon"
    if not obj.users_scene:
        return "not in the scene"
    return "its collection is disabled in this state"


def validate_project(context, include_state=True):
    project = context.scene.pm_vr_project
    issues = []
    if not project.source_root_collection:
        issues.append(Issue('ERROR', "Source Root Collection is not configured"))
    if include_state:
        try:
            validate_state_configuration(context)
        except PipelineStateError as exc:
            issues.append(Issue('ERROR', str(exc)))
    for source_id, objects in duplicate_source_ids().items():
        names = ", ".join(obj.name for obj in objects)
        issues.append(Issue(
            'ERROR',
            f"Duplicate source ID {source_id[:8]}: {names}; select the copy and run "
            "F3 > Register Selected Duplicates as New Sources",
        ))
    layer_ids = [layer.layer_id for layer in project.render_layers if layer.layer_id]
    if len(layer_ids) != len(set(layer_ids)):
        issues.append(Issue('ERROR', "Render layers contain duplicate stable IDs"))
    unit_ids = [unit.unit_id for unit in project.bake_units if unit.unit_id]
    if len(unit_ids) != len(set(unit_ids)):
        issues.append(Issue('ERROR', "Bake units contain duplicate stable IDs"))
    return issues


def validate_unit(context, unit, require_visible=True):
    project = context.scene.pm_vr_project
    issues = []
    layer = find_layer(project, unit.render_layer_id)
    if not layer:
        return [Issue('ERROR', f'Unit "{unit.display_name}" has no valid render layer')]
    if layer.layer_type not in BAKE_LAYER_TYPES:
        issues.append(Issue('ERROR', f'Unit "{unit.display_name}" belongs to Export Original layer'))
    members = unit_members(unit.unit_id)
    if not members:
        issues.append(Issue('ERROR', f'Unit "{unit.display_name}" has no Bake members'))
        return issues
    visible_count = 0
    duplicate_ids = duplicate_source_ids()
    mesh_owners = {}
    for obj in members:
        if obj.type == 'MESH' and obj.data:
            other = mesh_owners.setdefault(obj.data.as_pointer(), obj)
            if other is not obj:
                issues.append(Issue(
                    'ERROR',
                    f'Shares mesh data with "{other.name}" in this unit; their '
                    f'SimpleBake UVs overlap. Put linked duplicates in separate units',
                    obj.name,
                ))
    for obj in members:
        if obj.type != 'MESH':
            issues.append(Issue('ERROR', "Bake role requires a Mesh", obj.name))
            continue
        if obj.pm_vr_pipeline.render_layer_id != unit.render_layer_id:
            issues.append(Issue('ERROR', "Object layer and unit layer disagree", obj.name))
        source_id = obj.pm_vr_pipeline.source_id
        if source_id and source_id in duplicate_ids:
            names = ", ".join(item.name for item in duplicate_ids[source_id])
            issues.append(Issue(
                'ERROR',
                f"Source identity is duplicated by: {names}",
                obj.name,
            ))
        if not obj.data.uv_layers.get(BAKE_UV_NAME):
            issues.append(Issue('ERROR', f'Missing UV map "{BAKE_UV_NAME}"', obj.name))
        if not obj.data.uv_layers.get(PRIMARY_UV_NAME):
            issues.append(Issue('ERROR', f'Missing UV map "{PRIMARY_UV_NAME}"', obj.name))
        if layer.layer_type == 'PBR':
            # PBR keeps the source's Roughness/Metallic/Normal: it needs to
            # know which node holds them.
            for slot_index, slot in enumerate(obj.material_slots):
                if len(principled_nodes(slot.material)) != 1:
                    issues.append(Issue(
                        'ERROR',
                        f"PBR material slot {slot_index} must contain exactly one Principled BSDF",
                        obj.name,
                    ))
        elif layer.layer_type == 'ALPHA':
            # Alpha keeps only the opacity mask; the rest of the look is baked.
            for slot_index, slot in enumerate(obj.material_slots):
                try:
                    find_alpha_source(slot.material)
                except PipelineBakeError as exc:
                    issues.append(Issue('ERROR', f"Alpha material slot {slot_index}: {exc}", obj.name))
        slot_count = max(1, len(obj.material_slots))
        indices = numpy.empty(len(obj.data.polygons), dtype=numpy.int32)
        obj.data.polygons.foreach_get("material_index", indices)
        bad_faces = int((indices >= slot_count).sum())
        if bad_faces:
            issues.append(Issue(
                'ERROR',
                f"{bad_faces} face(s) use a material slot the object does not have "
                f"(it has {len(obj.material_slots)}); in Edit Mode select all and Assign a material",
                obj.name,
            ))
    hidden = [obj for obj in members if not object_render_visible(obj, context.view_layer)]
    if require_visible and len(hidden) == len(members):
        issues.append(Issue('INFO', f'Unit "{unit.display_name}" is fully hidden and will be skipped'))
    elif require_visible and hidden:
        issues.append(Issue(
            'ERROR',
            f'Unit "{unit.display_name}" is only partially visible: '
            + ", ".join(f'"{obj.name}" ({_hidden_reason(obj, context.view_layer)})' for obj in hidden[:5])
            + (f" and {len(hidden) - 5} more" if len(hidden) > 5 else ""),
        ))
    return issues


def _validate_scenarios(context):
    """Scenario references and units whose scenario switches off their own
    members, checked in the active lighting state."""
    project = context.scene.pm_vr_project
    issues = []
    scope = None
    visibility = {}
    for unit in project.bake_units:
        try:
            scenario = unit_scenario(project, unit)
        except PipelineBakeError as exc:
            issues.append(Issue('ERROR', str(exc)))
            continue
        if not scenario:
            continue
        if scope is None:
            try:
                scope = Scope(project, context.view_layer)
            except PipelineBakeError as exc:
                issues.append(Issue('ERROR', f"Bake scenarios: {exc}"))
                return issues
        if scenario.scenario_id not in visibility:
            visibility[scenario.scenario_id] = Visibility(
                context.view_layer, scope.flags(scenario, {})
            )
        message = hidden_member_message(unit, scenario, visibility[scenario.scenario_id])
        if message:
            issues.append(Issue('ERROR', message[:1].upper() + message[1:]))
    return issues


def validate_all(context):
    project = context.scene.pm_vr_project
    issues = validate_project(context)
    root_objects = set(project.source_root_collection.all_objects) if project.source_root_collection else set()
    output_names = {}
    for layer in project.render_layers:
        if layer.layer_type == 'RUNTIME':
            from .platform import runtime_warnings
            for name, message in runtime_warnings(layer_members(layer.layer_id)):
                issues.append(Issue('WARNING', message, name))
        if not layer.layer_id:
            issues.append(Issue('ERROR', f'Layer "{layer.display_name}" has no stable ID'))
        if not layer.display_name.strip():
            issues.append(Issue('ERROR', "Render layer has no name"))
        key = layer.display_name.strip().casefold()
        if key:
            if key in output_names:
                issues.append(Issue('ERROR', f'Duplicate render layer name: "{layer.display_name}"'))
            output_names[key] = layer.layer_id
        for obj in layer_members(layer.layer_id):
            meta = obj.pm_vr_pipeline
            if obj not in root_objects:
                issues.append(Issue('ERROR', "Assigned source is outside Source Root", obj.name))
            if layer.layer_type not in BAKE_LAYER_TYPES and meta.processing_role != 'EXPORT_ORIGINAL':
                issues.append(Issue('ERROR', "Export Original layer contains a non-original role", obj.name))
            if obj.type == 'EMPTY' and meta.processing_role != 'EXPORT_ORIGINAL':
                issues.append(Issue('ERROR', "Empty must use Export Original", obj.name))
            if meta.processing_role == 'UNASSIGNED':
                issues.append(Issue('ERROR', "Object role is Unassigned", obj.name))
            if meta.processing_role == 'BAKE' and not find_unit(project, meta.bake_unit_id):
                issues.append(Issue('ERROR', "Bake object has no valid bake unit", obj.name))
            parent = obj.parent
            if (
                parent and hasattr(parent, "pm_vr_pipeline")
                and parent.pm_vr_pipeline.is_registered_source
                and parent.pm_vr_pipeline.render_layer_id
                and parent.pm_vr_pipeline.render_layer_id != meta.render_layer_id
            ):
                issues.append(Issue('ERROR', "Parent belongs to another render layer", obj.name))
    for obj in bpy.data.objects:
        if not hasattr(obj, "pm_vr_pipeline") or not obj.pm_vr_pipeline.is_registered_source:
            continue
        meta = obj.pm_vr_pipeline
        if not meta.source_id:
            issues.append(Issue('ERROR', "Registered source has no stable ID", obj.name))
        owner = find_layer(project, meta.render_layer_id)
        seen_targets = set()
        for entry in meta.extra_export_layers:
            target = find_layer(project, entry.layer_id)
            if not target:
                issues.append(Issue('ERROR', "Additional export layer is missing", obj.name))
            elif entry.layer_id == meta.render_layer_id:
                issues.append(Issue('ERROR', "Additional export repeats the object's primary layer", obj.name))
            elif owner and target.layer_type != owner.layer_type:
                issues.append(Issue('ERROR', f'Additional export to "{target.display_name}" has a different layer type', obj.name))
            if entry.layer_id in seen_targets:
                issues.append(Issue('ERROR', "Additional export layer is listed twice", obj.name))
            seen_targets.add(entry.layer_id)
        if meta.extra_export_layers and (not owner or meta.processing_role == 'UNASSIGNED'):
            issues.append(Issue('ERROR', "Additional exports require a valid primary layer and role", obj.name))
    for unit in project.bake_units:
        issues.extend(validate_unit(context, unit, require_visible=False))
    issues.extend(_validate_scenarios(context))
    artifact_keys = [unit.artifact_key for unit in project.bake_units if unit.artifact_key]
    if len(artifact_keys) != len(set(artifact_keys)):
        issues.append(Issue('ERROR', "Bake units contain duplicate artifact keys"))
    queued = [entry.unit_id for entry in project.bake_queue]
    if len(queued) != len(set(queued)):
        issues.append(Issue('ERROR', "Bake queue contains duplicate units"))
    for unit_id in queued:
        if not find_unit(project, unit_id):
            issues.append(Issue('ERROR', "Bake queue contains a missing unit"))
    return issues
