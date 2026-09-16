"""Stable identity helpers; names are labels, never ownership keys."""

import re
import uuid

import bpy


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def new_id():
    return uuid.uuid4().hex


def ensure_project_id(project):
    if not project.project_id:
        project.project_id = new_id()
    project.initialized = True
    return project.project_id


def ensure_layer_id(layer):
    if not layer.layer_id:
        layer.layer_id = new_id()
    return layer.layer_id


def ensure_unit_id(unit):
    if not unit.unit_id:
        unit.unit_id = new_id()
    if not unit.artifact_key:
        unit.artifact_key = unit.unit_id
    return unit.unit_id


def ensure_source_id(obj):
    metadata = obj.pm_vr_pipeline
    if not metadata.source_id:
        metadata.source_id = new_id()
    metadata.is_registered_source = True
    return metadata.source_id


def find_layer(project, layer_id):
    return next((item for item in project.render_layers if item.layer_id == layer_id), None)


def find_unit(project, unit_id):
    return next((item for item in project.bake_units if item.unit_id == unit_id), None)


def layer_members(layer_id):
    return [
        obj for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.is_registered_source
        and obj.pm_vr_pipeline.render_layer_id == layer_id
    ]


def unit_members(unit_id):
    return [
        obj for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.is_registered_source
        and obj.pm_vr_pipeline.processing_role == 'BAKE'
        and obj.pm_vr_pipeline.bake_unit_id == unit_id
    ]


def duplicate_source_ids():
    seen = {}
    duplicates = {}
    for obj in bpy.data.objects:
        if not hasattr(obj, "pm_vr_pipeline"):
            continue
        source_id = obj.pm_vr_pipeline.source_id
        if not source_id:
            continue
        if source_id in seen:
            duplicates.setdefault(source_id, [seen[source_id]]).append(obj)
        else:
            seen[source_id] = obj
    return duplicates


def safe_stem(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', (value or '').strip()).rstrip(' .')
    value = value or "PMVR"
    if value.upper() in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"
    return value
