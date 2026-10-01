"""Lighting looks and result access with unchanged v2 DAY/EVENING storage.

Legacy fields remain authoritative for DAY/EVENING reads. Writes also update
the new collections, allowing v2 to read a file saved by v3 without rebaking.
"""
import re

import bpy

from .identity import new_id

FORMAT_VERSION = 1
LEGACY = {'DAY': 'day', 'EVENING': 'evening'}
_SYNCING = False


def ensure(project):
    global _SYNCING
    if _SYNCING:
        return
    if project.lighting_format_version != 0:
        return
    _SYNCING = True
    try:
        for state, name in (('DAY', 'Day'), ('EVENING', 'Evening')):
            prefix = LEGACY[state]
            if state == 'EVENING' and not (project.legacy_working_directory or
                    project.evening_lighting_collection or project.evening_world):
                continue
            look = project.lighting_looks.add()
            look.look_id, look.display_name = state, name
            look.is_default = state == 'DAY'
            look.lighting_collection = getattr(project, prefix + '_lighting_collection')
            look.world = getattr(project, prefix + '_world')
            look.bake_enabled = getattr(project, 'bake_' + prefix)
        project.active_look_id = project.active_lighting_state
        project.lighting_format_version = FORMAT_VERSION
        for unit in project.bake_units:
            for state in LEGACY:
                set_result(unit, state, **{key: result_value(unit, state, key)
                           for key in ('signature', 'image_name', 'status', 'baked_resolution')})
                set_result(unit, state, mode='LIGHTMAP', **{key: result_value(unit, state, key, 'LIGHTMAP')
                           for key in ('signature', 'image_name', 'status')})
                for variant in unit.variants:
                    set_variant_result(variant, state, variant_file(variant, state), variant_signature(variant, state))
    finally:
        _SYNCING = False


def find(project, state):
    ensure(project)
    return next((look for look in project.lighting_looks if look.look_id == state), None)


def active_id(project):
    ensure(project)
    return project.active_look_id or project.active_lighting_state


def name(project, state):
    look = find(project, state)
    return look.display_name if look else state.title()


def suffix(project, state):
    look = find(project, state)
    if not look:
        raise ValueError('Lighting look is missing: ' + state)
    return '' if look.is_default else '_' + look.display_name


def checked(project):
    ensure(project)
    return [look.look_id for look in project.lighting_looks if look.bake_enabled]


def default_id(project):
    ensure(project)
    return next((look.look_id for look in project.lighting_looks if look.is_default), '')


def legacy_updated(project, _context):
    global _SYNCING
    if _SYNCING or project.lighting_format_version == 0:
        return
    _SYNCING = True
    try:
        for state, prefix in LEGACY.items():
            look = next((x for x in project.lighting_looks if x.look_id == state), None)
            collection, world = getattr(project, prefix + '_lighting_collection'), getattr(project, prefix + '_world')
            if not look and (collection or world):
                look = project.lighting_looks.add()
                look.look_id, look.display_name = state, prefix.title()
                look.is_default = not any(x.is_default for x in project.lighting_looks)
            if look:
                look.lighting_collection, look.world = collection, world
                look.bake_enabled = getattr(project, 'bake_' + prefix)
    finally:
        _SYNCING = False


def look_updated(look, _context):
    global _SYNCING
    if _SYNCING or look.look_id not in LEGACY:
        return
    project = look.id_data.pm_vr_project
    prefix = LEGACY[look.look_id]
    _SYNCING = True
    try:
        setattr(project, prefix + '_lighting_collection', look.lighting_collection)
        setattr(project, prefix + '_world', look.world)
        setattr(project, 'bake_' + prefix, look.bake_enabled)
    finally:
        _SYNCING = False


def legacy_active_updated(project, context):
    if not _SYNCING:
        project.active_look_id = project.active_lighting_state
    from .data import _overlay_updated
    _overlay_updated(project, context)


def validate_names(project):
    ensure(project)
    if not project.lighting_looks or sum(x.is_default for x in project.lighting_looks) != 1:
        raise ValueError('Lighting needs exactly one default look')
    ids = [look.look_id for look in project.lighting_looks]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError('Lighting look IDs must be present and unique')
    names = set()
    reserved = {'unlit', 'pbr', 'alpha', 'translucent', 'glass', 'emissive', 'runtime', 'variant'}
    reserved.update(obj.name[5:].casefold() for obj in bpy.data.objects if obj.name.startswith('Zone_'))
    for look in project.lighting_looks:
        if not re.fullmatch(r'[A-Za-z]+', look.display_name):
            raise ValueError('Look names must be a single Latin word without underscores')
        key = look.display_name.casefold()
        if key in names or key in reserved:
            raise ValueError('Look names must differ from other looks, layer types and zones')
        names.add(key)
    # File stems must be unique across looks as well as within each look.
    from .identity import safe_stem
    files = set()
    for look in project.lighting_looks:
        for layer in project.render_layers:
            file = (safe_stem(layer.display_name) + suffix(project, look.look_id)).casefold()
            if file in files:
                raise ValueError('Layer/look names produce duplicate export filenames: ' + file)
            files.add(file)
    for unit in project.bake_units:
        tails = set()
        for look in project.lighting_looks:
            for variant in (None, *unit.variants):
                tail = (('' if variant is None else '_' + safe_stem(variant.title))
                        + suffix(project, look.look_id)).casefold()
                if tail in tails:
                    raise ValueError(f'{unit.display_name}: look and variant names produce duplicate bake filenames')
                tails.add(tail)


def _collection_result(collection, state, create=False):
    result = next((item for item in collection if item.look_id == state), None)
    if result is None and create:
        result = collection.add()
        result.look_id = state
    return result


def _legacy_field(state, key, mode):
    prefix = LEGACY[state]
    fields = {'signature': 'signature', 'image_name': 'beauty_image',
              'status': 'status', 'baked_resolution': 'baked_resolution'}
    if mode == 'LIGHTMAP':
        fields = {'signature': 'lightmap_signature', 'image_name': 'lightmap_image',
                  'status': 'lightmap_status'}
    return prefix + '_' + fields[key] if key in fields else None


def result_value(unit, state, key, mode='BEAUTY'):
    if state in LEGACY:
        field = _legacy_field(state, key, mode)
        if field:
            return getattr(unit, field)
    result = _collection_result(unit.beauty_results if mode == 'BEAUTY' else unit.lightmap_results, state)
    return getattr(result, key) if result else (0 if key == 'baked_resolution' else '')


def set_result(unit, state, mode='BEAUTY', **values):
    result = _collection_result(unit.beauty_results if mode == 'BEAUTY' else unit.lightmap_results, state, True)
    for key, value in values.items():
        setattr(result, key, value)
        if state in LEGACY:
            field = _legacy_field(state, key, mode)
            if field:
                setattr(unit, field, value)


def result_ids(unit, mode='BEAUTY'):
    return list(dict.fromkeys([*LEGACY, *(x.look_id for x in
                              (unit.beauty_results if mode == 'BEAUTY' else unit.lightmap_results))]))


def variant_file(variant, state):
    if state in LEGACY:
        return getattr(variant, LEGACY[state] + '_file')
    result = _collection_result(variant.look_results, state)
    return result.file if result else ''


def variant_signature(variant, state):
    if state in LEGACY:
        return getattr(variant, LEGACY[state] + '_signature')
    result = _collection_result(variant.look_results, state)
    return result.signature if result else ''


def set_variant_result(variant, state, path, signature):
    result = _collection_result(variant.look_results, state, True)
    result.file, result.signature = path, signature
    if state in LEGACY:
        setattr(variant, LEGACY[state] + '_file', path)
        setattr(variant, LEGACY[state] + '_signature', signature)


def queue_done(entry, state):
    if state in LEGACY:
        return getattr(entry, LEGACY[state] + '_done')
    return any(x.look_id == state for x in entry.completed_looks)


def set_queue_done(entry, state):
    _collection_result(entry.completed_looks, state, True)
    if state in LEGACY:
        setattr(entry, LEGACY[state] + '_done', True)


def clear_queue_done(entry):
    entry.day_done = entry.evening_done = False
    entry.completed_looks.clear()


def record_id(record):
    return record.look_id or record.lighting_state


class PMVR_OT_AddLightingLook(bpy.types.Operator):
    bl_idname = 'pmvr.add_lighting_look'
    bl_label = 'Add Lighting'
    bl_options = {'REGISTER', 'UNDO'}
    name: bpy.props.StringProperty(name='Name', default='Night')
    lighting_collection: bpy.props.StringProperty(name='Lighting Collection')
    world: bpy.props.StringProperty(name='World')

    @classmethod
    def poll(cls, context):
        return not context.scene.pm_vr_project.operation_running

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, _context):
        self.layout.prop(self, 'name')
        self.layout.prop_search(self, 'lighting_collection', bpy.data, 'collections')
        self.layout.prop_search(self, 'world', bpy.data, 'worlds')

    def execute(self, context):
        project = context.scene.pm_vr_project
        ensure(project)
        # Validate before adding persistent data; an invalid dialog is a no-op.
        if (not re.fullmatch(r'[A-Za-z]+', self.name) or
                self.name.casefold() in {x.display_name.casefold() for x in project.lighting_looks} or
                self.name.casefold() in {'unlit', 'pbr', 'alpha', 'translucent', 'glass', 'emissive', 'runtime', 'variant'} or
                any(obj.name[5:].casefold() == self.name.casefold() for obj in bpy.data.objects if obj.name.startswith('Zone_'))):
            self.report({'ERROR'}, 'Use a unique Latin word different from layer types and zone names')
            return {'CANCELLED'}
        look = project.lighting_looks.add()
        look.look_id, look.display_name = new_id(), self.name
        look.lighting_collection = bpy.data.collections.get(self.lighting_collection)
        look.world = bpy.data.worlds.get(self.world)
        return {'FINISHED'}


class PMVR_OT_DefaultLightingLook(bpy.types.Operator):
    bl_idname = 'pmvr.default_lighting_look'
    bl_label = 'Set Default Lighting'
    bl_options = {'REGISTER', 'UNDO'}
    look_id: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return not context.scene.pm_vr_project.operation_running

    def execute(self, context):
        project = context.scene.pm_vr_project
        if not find(project, self.look_id):
            return {'CANCELLED'}
        for look in project.lighting_looks:
            look.is_default = look.look_id == self.look_id
        return {'FINISHED'}


CLASSES = (PMVR_OT_AddLightingLook, PMVR_OT_DefaultLightingLook)
