"""Authored platform properties and warning-only Runtime naming checks."""
from contextlib import contextmanager
import re

import bpy

from .constants import TAG_GENERATED, TAG_SOURCE_ID

OBJECT_PROPERTIES = ('title', 'order', 'volume', 'opacity', 'brightness', 'emissiveIntensity')
EMPTY_ROLES = ('Hotspot', 'InfoButton', 'InfoPanel', 'SFX', 'Ambience', 'Music', 'SkyPoint', 'Inspect')
MESH_ROLES = ('Zone', 'Video', 'Clock')


def hide_runtime_helpers(snapshot, project):
    """Data-only Runtime geometry must never shadow a bake or probe render.

    Uses the existing visibility snapshot, including interrupted-run recovery;
    restores authored visibility and leaves export membership untouched.
    """
    layers = {layer.layer_id for layer in project.render_layers if layer.layer_type == 'RUNTIME'}
    root = project.source_root_collection
    # Changing render visibility may invalidate Blender's collection cache.
    # Materialize first; complex linked scenes can also expose null entries.
    objects = tuple(root.all_objects) if root else ()
    for obj in objects:
        if obj is None:
            continue
        meta = obj.pm_vr_pipeline
        if (obj.type == 'MESH' and meta.is_registered_source and meta.render_layer_id in layers
                and obj.name.startswith(('Zone_', 'Navmesh', 'Collision'))):
            snapshot.hide(obj)


@contextmanager
def authored_properties(objects):
    """Latest source properties on generated export objects, with full rollback.

    A deleted source property is absent in USD even if an older generated copy
    still carries it. No rebake or permanent generated-data edit is needed.
    """
    sources = {obj.pm_vr_pipeline.source_id: obj for obj in bpy.data.objects
               if obj.pm_vr_pipeline.is_registered_source and not obj.get(TAG_GENERATED)}
    saved = []
    try:
        for obj in objects:
            if not obj.get(TAG_GENERATED):
                continue
            source = sources.get(obj.get(TAG_SOURCE_ID))
            if source is None:
                continue
            for name in OBJECT_PROPERTIES:
                saved.append((obj, name, name in obj, obj.get(name)))
                if name in source:
                    obj[name] = source[name]
                elif name in obj:
                    del obj[name]
        yield
    finally:
        for obj, name, existed, value in reversed(saved):
            if existed:
                obj[name] = value
            elif name in obj:
                del obj[name]


def runtime_warnings(objects):
    """Never rename, remove, or block an authored Runtime object."""
    problems = []
    for obj in objects:
        name = obj.name
        # Web/iOS entities are reserved and intentionally ignored by AVP.
        if name.startswith('Teleport') or re.fullmatch(r'[^_]+_StartPosition', name):
            continue
        if name == 'StartPosition':
            expected = 'EMPTY'
        elif name.startswith(('Navmesh', 'Collision')):
            expected = 'MESH'
        elif name == 'Skybox' or name.startswith('Skybox_'):
            expected = 'MESH'
        elif name.startswith('Probe_') and name[6:]:
            # Probe authoring uses cameras; export converts them to Empty.
            expected = ('EMPTY', 'CAMERA')
        elif name.startswith('Music_'):
            role = re.sub(r'[._]\d+$', '', name)
            if role not in ('Music_Source', 'Music_Button', 'Music_Panel'):
                problems.append((name, 'Unknown Music role; use Music_Source, Music_Button or Music_Panel'))
                continue
            expected = 'EMPTY'
        else:
            role, separator, tail = name.partition('_')
            if separator and tail and role in EMPTY_ROLES:
                expected = 'EMPTY'
            elif separator and tail and role in MESH_ROLES:
                expected = 'MESH'
            elif obj.type == 'MESH' and any(
                slot.material and slot.material.name.startswith('RK_') for slot in obj.material_slots
            ):
                continue
            else:
                problems.append((name, 'Unknown Runtime name; see Runtime Names in the pipeline rules'))
                continue
        types = (expected,) if isinstance(expected, str) else expected
        if obj.type not in types:
            problems.append((name, 'Runtime role expects ' + ' or '.join(types)))
    return problems
