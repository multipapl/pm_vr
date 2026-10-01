"""Atomic per-look inventory for Asset Manager (platform contract, schema 1)."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

import bpy

from . import variants, looks
from .constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID
from .identity import safe_stem, unit_members


def look_info(project, state):
    look = looks.find(project, state)
    if look is None:
        raise ValueError('Lighting look was removed')
    return {'id': state, 'name': look.display_name,
            'suffix': looks.suffix(project, state), 'default': look.is_default}


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pmvr_description_', suffix='.json', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _current_files(project, state, excluded):
    look = look_info(project, state)
    files = {}
    for layer in project.render_layers:
        if layer.layer_id not in excluded:
            name = safe_stem(layer.display_name) + look['suffix'] + '.usdz'
            files[name] = {'file': name, 'layer': layer.display_name, 'type': layer.layer_type}
    generated = {}
    for obj in bpy.data.objects:
        if obj.get(TAG_GENERATED) and obj.get(TAG_MODE) == 'BEAUTY':
            generated.setdefault((obj.get(TAG_UNIT_ID), obj.get(TAG_SOURCE_ID)), []).append(obj)
    for unit in project.bake_units:
        if unit.render_layer_id in excluded or not unit.variants or variants.variant_problem(project, unit):
            continue
        members = unit_members(unit.unit_id)
        matches = generated.get((unit.unit_id, members[0].pm_vr_pipeline.source_id), [])
        if len(matches) != 1:
            continue
        entity = variants.usd_name(matches[0].name)
        for variant in unit.variants:
            if variants.variant_status(unit, variant, state) == 'Ready':
                name = variants.model_path(entity, variant, state)
                files[name] = {'file': name, 'type': 'VARIANT'}
    return files


def write(project, state, written=(), excluded=()):
    """Keep previous current entries, add committed files, prune stale names.

    No directory scan: merely existing old packages never become advertised
    files. A failed partial export retains the last committed valid inventory.
    """
    staging = Path(bpy.path.abspath(project.usdz_output_directory)).resolve()
    look = look_info(project, state)
    path = staging / ('PMVR_Export_' + look['name'] + '.json')
    previous = []
    if path.exists():
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema') != 1 or value.get('look', {}).get('id') != state:
            raise ValueError('Export description has an incompatible schema or lighting ID')
        previous = value.get('files', [])
    allowed = _current_files(project, state, set(excluded))
    candidates = {item['file'] for item in previous
                  if isinstance(item, dict) and isinstance(item.get('file'), str)
                  and item['file'] in allowed
                  and all(item.get(key) == value for key, value in allowed[item['file']].items())}
    for value in written:
        file = Path(value).resolve()
        if not file.is_relative_to(staging):
            raise ValueError('Committed export is outside the USDZ directory')
        candidates.add(file.relative_to(staging).as_posix())
    entries = [allowed[name] for name in sorted(candidates)
               if name in allowed and (staging / name).is_file()]
    intensity = project.id_data.get('reflectionIntensity', 1.0)
    document = {'schema': 1, 'look': look,
                'settings': {'reflectionIntensity': intensity},
                'exportedAt': datetime.now().astimezone().isoformat(), 'files': entries}
    _atomic_json(path, document)
    # A renamed look must not remain discoverable through its old description.
    # Packages remain on disk; only metadata owned by this look is replaced.
    for previous_path in staging.glob('PMVR_Export_*.json'):
        if previous_path == path:
            continue
        try:
            old = json.loads(previous_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        if old.get('schema') == 1 and old.get('look', {}).get('id') == state:
            previous_path.unlink()
    return str(path)
