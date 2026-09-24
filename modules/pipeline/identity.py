"""Stable identity helpers; names are labels, never ownership keys."""

import re
import uuid

import bpy

from .constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID


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
        claim_identity(obj)
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


def extra_export_members(layer_id):
    return [
        obj for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.is_registered_source
        and any(entry.layer_id == layer_id for entry in obj.pm_vr_pipeline.extra_export_layers)
    ]


def export_layer_members(layer_id):
    """Resolve a file's sources without changing their bake-layer ownership."""
    members = layer_members(layer_id)
    seen = {obj.as_pointer() for obj in members}
    for obj in extra_export_members(layer_id):
        if obj.as_pointer() not in seen:
            members.append(obj)
            seen.add(obj.as_pointer())
    return members


def unit_members(unit_id):
    return [
        obj for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.is_registered_source
        and obj.pm_vr_pipeline.processing_role == 'BAKE'
        and obj.pm_vr_pipeline.bake_unit_id == unit_id
    ]


def sources_by_id():
    """Map stable source IDs to registered source objects."""
    return {
        obj.pm_vr_pipeline.source_id: obj
        for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.is_registered_source
        and obj.pm_vr_pipeline.source_id
    }


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


# Shift+D, Alt+D and copy/paste copy every property, including stable IDs.
# session_uid is not copied and survives undo, so it tells an original from
# its copy. IDs already shared when a file is loaded stay ambiguous and are
# left to Register Selected Duplicates as New Sources.
_AMBIGUOUS = -1
_source_owners = {}
_generated_owners = {}
_object_count = [-1]


def _identity_key(obj):
    if obj.get(TAG_GENERATED):
        return _generated_owners, (obj.get(TAG_UNIT_ID), obj.get(TAG_SOURCE_ID), obj.get(TAG_MODE))
    metadata = getattr(obj, "pm_vr_pipeline", None)
    if metadata and metadata.source_id:
        return _source_owners, metadata.source_id
    return None, None


def _group_by_identity():
    groups = {}
    for obj in bpy.data.objects:
        owners, key = _identity_key(obj)
        if owners is not None:
            groups.setdefault((id(owners), key), (owners, key, []))[2].append(obj)
    return groups.values()


def claim_identity(obj):
    """Record obj as the original owner of its stable ID."""
    owners, key = _identity_key(obj)
    if owners is not None:
        owners[key] = obj.session_uid


def remember_identity_owners():
    """Treat every object present now as an original."""
    _source_owners.clear()
    _generated_owners.clear()
    for owners, key, objects in _group_by_identity():
        owners[key] = objects[0].session_uid if len(objects) == 1 else _AMBIGUOUS
    _object_count[0] = len(bpy.data.objects)


def _separate(obj):
    if obj.get(TAG_GENERATED):
        # A copy of a generated result becomes an ordinary object.
        for name in [name for name in obj.keys() if name.startswith("pmvr_")]:
            del obj[name]
        return
    metadata = obj.pm_vr_pipeline
    metadata.source_id = new_id()
    # The copy's SimpleBake UVs overlap the original's atlas; it keeps its
    # layer and role but needs its own unit.
    metadata.bake_unit_id = ""
    metadata.extra_export_layers.clear()
    _source_owners[metadata.source_id] = obj.session_uid


def separate_copied_identities(force=False):
    """Give objects copied from registered or generated objects their own identity."""
    count = len(bpy.data.objects)
    grew = count > _object_count[0]
    _object_count[0] = count
    if not (grew or force):
        return 0
    separated = 0
    for owners, key, objects in list(_group_by_identity()):
        uids = [obj.session_uid for obj in objects]
        owner = owners.get(key)
        if owner not in uids:
            if owner == _AMBIGUOUS and len(objects) > 1:
                continue
            owners[key] = uids[0] if len(objects) == 1 else _AMBIGUOUS
            continue
        for obj in objects:
            if obj.session_uid != owner:
                _separate(obj)
                separated += 1
    return separated
