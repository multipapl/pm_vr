"""Central validation for setup, bake and export."""

from dataclasses import dataclass

import bpy

from .constants import BAKE_LAYER_TYPES, BAKE_UV_NAME, PRIMARY_UV_NAME
from .identity import duplicate_source_ids, find_layer, find_unit, layer_members, unit_members
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
        issues.append(Issue('ERROR', f"Duplicate source ID {source_id[:8]}: {names}"))
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
        if layer.layer_type in {'PBR', 'ALPHA'}:
            for slot_index, slot in enumerate(obj.material_slots):
                material = slot.material
                principled = (
                    [node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED']
                    if material and material.use_nodes and material.node_tree
                    else []
                )
                if len(principled) != 1:
                    issues.append(Issue(
                        'ERROR',
                        f"Material slot {slot_index} must contain exactly one Principled BSDF",
                        obj.name,
                    ))
                    continue
                if layer.layer_type == 'ALPHA':
                    alpha = principled[0].inputs.get("Alpha")
                    if not alpha or (not alpha.is_linked and alpha.default_value >= 1.0):
                        issues.append(Issue(
                            'ERROR',
                            f"Translucent material slot {slot_index} has no Alpha branch/value",
                            obj.name,
                        ))
        visible_count += int(object_render_visible(obj, context.view_layer))
    if require_visible and visible_count == 0:
        issues.append(Issue('INFO', f'Unit "{unit.display_name}" is fully hidden and will be skipped'))
    elif require_visible and visible_count != len(members):
        issues.append(Issue('ERROR', f'Unit "{unit.display_name}" is only partially visible'))
    return issues


def validate_all(context):
    project = context.scene.pm_vr_project
    issues = validate_project(context)
    root_objects = set(project.source_root_collection.all_objects) if project.source_root_collection else set()
    output_names = {}
    for layer in project.render_layers:
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
    for unit in project.bake_units:
        issues.extend(validate_unit(context, unit, require_visible=False))
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
