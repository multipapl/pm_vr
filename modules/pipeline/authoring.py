"""Small contextual tools that write the platform's actual names and properties."""
import re
from pathlib import Path

import bpy

from . import looks, platform
from .constants import TAG_GENERATED, TAG_SOURCE_ID
from .identity import ensure_source_id, find_layer, layer_members
from .ui_sections import section

# These are UI labels only; the keys are the existing export contract.
FIELDS = {
    'title': ('Title', 'STRING', 'Name shown in Places or the audio mixer'),
    'order': ('Order', 'INT', 'Order in Places; absent means alphabetical'),
    'volume': ('Volume', 'FLOAT', 'Default audio volume; the app mixer remembers changes'),
    'opacity': ('Opacity', 'FLOAT', 'Object opacity; absent Glass inherits the material'),
    'brightness': ('Brightness', 'FLOAT', 'Translucent colour multiplier'),
    'emissiveIntensity': ('Emission', 'FLOAT', 'Multiplier of the authored emission'),
    'reflectionIntensity': ('Reflections', 'FLOAT', 'Scene reflection multiplier'),
}
ROLES = (
    ('StartPosition', 'Start position', 'EMPTY'), ('Hotspot', 'Place', 'EMPTY'),
    ('InfoButton', 'Info button', 'EMPTY'), ('InfoPanel', 'Info panel', 'EMPTY'),
    ('SFX', 'Point sound', 'EMPTY'), ('Ambience', 'Ambient sound', 'EMPTY'),
    ('Music_Source', 'Music source', 'EMPTY'), ('Music_Button', 'Music button', 'EMPTY'),
    ('Music_Panel', 'Music panel', 'EMPTY'), ('SkyPoint', 'Sky position', 'EMPTY'),
    ('Inspect', 'Inspect point', 'EMPTY'), ('Probe', 'Reflection probe', 'CAMERA'),
    ('Zone', 'Zone box', 'MESH'), ('Skybox', 'Sky mesh', 'MESH'),
    ('Video', 'Video mesh', 'MESH'), ('Clock', 'Clock mesh', 'MESH'),
    ('Navmesh', 'Walkable mesh', 'MESH'), ('Collision', 'Collision mesh', 'MESH'),
)
ROLE_ITEMS = tuple((key, label, 'Use the ' + key + ' naming convention') for key, label, _ in ROLES)
FIXED = {'StartPosition', 'Music_Source', 'Music_Button', 'Music_Panel'}
CREATABLE = {key for key, _, kind in ROLES if kind != 'MESH'} | {'Zone'}
_enum_cache = {}


def source_object(obj):
    if obj and obj.get(TAG_GENERATED):
        source_id = obj.get(TAG_SOURCE_ID, '')
        sources = [source for source in bpy.data.objects if source.pm_vr_pipeline.is_registered_source
                   and not source.get(TAG_GENERATED) and source.pm_vr_pipeline.source_id == source_id]
        return sources[0] if len(sources) == 1 else None
    return obj


def role_of(obj):
    if not obj:
        return ''
    name = obj.name
    if name == 'StartPosition':
        return name
    for role in ('Music_Source', 'Music_Button', 'Music_Panel'):
        if re.fullmatch(re.escape(role) + r'(?:[._]\d+)?', name):
            return role
    for role in ('Navmesh', 'Collision', 'Skybox'):
        if name == role or name.startswith(role + '_') or re.fullmatch(role + r'\.\d+', name):
            return role
    head, sep, tail = name.partition('_')
    return head if sep and tail and head in {key for key, _, _ in ROLES} else ''


def fields_for(project, obj):
    obj = source_object(obj)
    if not obj:
        return ()
    layer = find_layer(project, obj.pm_vr_pipeline.render_layer_id)
    if not layer:
        return ()
    if layer.layer_type == 'RUNTIME':
        return {'Hotspot': ('title', 'order'), 'SFX': ('title', 'volume'),
                'Ambience': ('title', 'volume'), 'Music_Source': ('volume',)}.get(role_of(obj), ())
    return {'GLASS': ('opacity',), 'TRANSLUCENT': ('opacity', 'brightness'),
            'PBR': ('emissiveIntensity',), 'EMISSIVE': ('emissiveIntensity',)}.get(layer.layer_type, ())


def fallback(obj, key):
    if key == 'title':
        name = obj.name.partition('_')[2] or obj.name
        return re.sub(r'[._]?\d+$', '', name) if role_of(obj) in ('SFX', 'Ambience') else name
    if key == 'order':
        return 0
    if key == 'opacity' and obj and obj.type == 'MESH':
        # Only Glass inherits material opacity; other semantic layers use 1.
        project = bpy.context.scene.pm_vr_project
        layer = find_layer(project, obj.pm_vr_pipeline.render_layer_id)
        if layer and layer.layer_type == 'GLASS':
            values = []
            for slot in obj.material_slots:
                tree = slot.material.node_tree if slot.material else None
                for node in tree.nodes if tree else ():
                    if node.type == 'BSDF_PRINCIPLED':
                        socket = node.inputs.get('Alpha')
                        if socket and not socket.is_linked:
                            values.append(float(socket.default_value))
            if values and all(abs(v - values[0]) < 1e-6 for v in values):
                return values[0]
            return None  # Linked or differing slots must be authored consciously.
    return 1.0


def editable(obj):
    return obj is not None and obj.is_editable


def write_value(obj, key, value):
    kind = FIELDS[key][1]
    if kind == 'STRING':
        value = str(value)
    elif kind == 'INT':
        value = int(value)
    else:
        value = max(0.0, float(value))
        if key in ('volume', 'opacity'):
            value = min(1.0, value)
    obj[key] = value
    kwargs = {'description': FIELDS[key][2]}
    if kind == 'FLOAT':
        kwargs.update(min=0.0, soft_max=1.0 if key in ('volume', 'opacity') else 5.0)
        if key in ('volume', 'opacity'):
            kwargs['max'] = 1.0
    obj.id_properties_ui(key).update(**kwargs)


class PMVR_OT_PlatformProperty(bpy.types.Operator):
    bl_idname = 'pmvr.platform_property'
    bl_label = 'Add Property'
    bl_description = 'Edit the exact export property on the original source; keep other existing values'
    # This is a one-shot Add button, not an adjustable last operation. Blender
    # redo would undo to an older selection before re-executing its modes.
    bl_options = {'UNDO'}
    key: bpy.props.EnumProperty(items=tuple((k, v[0], v[2]) for k, v in FIELDS.items()), options={'HIDDEN'})
    # Retain scripted calls, but expose no modes or multi-object actions in UI.
    action: bpy.props.EnumProperty(items=(('ADD', 'Add', ''), ('REMOVE', 'Remove', ''), ('COPY', 'Copy', '')), options={'HIDDEN'})
    bulk: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    value: bpy.props.FloatProperty(name='Value', default=1.0, min=0.0, options={'HIDDEN'})
    target_uid: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        label = FIELDS[properties.key][0]
        if properties.action == 'COPY':
            return f'Apply this {label.lower()} to compatible selected original sources, replacing their current values'
        if properties.action == 'REMOVE':
            return f'Remove the {label.lower()} override and use the material or platform default'
        return f'Add {label.lower()} on this source, then edit its value here; existing values are kept'

    @classmethod
    def poll(cls, context):
        return bool(context.scene and not context.scene.pm_vr_project.operation_running)

    def execute(self, context):
        project = context.scene.pm_vr_project
        active = context.scene if self.key == 'reflectionIntensity' else source_object(context.object)
        if self.target_uid and (not active or str(active.session_uid) != self.target_uid):
            self.report({'WARNING'}, 'The selected object changed; add the property on its current row')
            return {'CANCELLED'}
        objects = [active]
        if self.bulk and self.key != 'reflectionIntensity':
            objects = list({o.as_pointer(): o for selected in context.selected_objects
                            if (o := source_object(selected)) and self.key in fields_for(project, o)}.values())
        if not active or (self.key != 'reflectionIntensity' and self.key not in fields_for(project, active)):
            self.report({'ERROR'}, 'This property does not apply to the selected source')
            return {'CANCELLED'}
        if self.action == 'COPY' and self.key not in active:
            self.report({'ERROR'}, 'Set the active source value first')
            return {'CANCELLED'}
        if any(not editable(o) for o in objects):
            self.report({'ERROR'}, 'A selected source is linked or not editable')
            return {'CANCELLED'}
        changed = 0
        for obj in objects:
            if self.action == 'REMOVE':
                if self.key in obj:
                    del obj[self.key]
                    changed += 1
            elif self.action == 'COPY':
                write_value(obj, self.key, active[self.key])
                changed += 1
            elif self.key not in obj:
                value = fallback(obj, self.key)
                write_value(obj, self.key, self.value if value is None else value)
                changed += 1
        self.report({'INFO'}, f'{changed} source value(s) changed')
        return {'FINISHED'}

    def invoke(self, context, _event):
        return self.execute(context)


def runtime_objects(project):
    ids = {layer.layer_id for layer in project.render_layers if layer.layer_type == 'RUNTIME'}
    return [obj for layer_id in ids for obj in layer_members(layer_id)]


def zone_names(project):
    return sorted({re.sub(r'(?:[._]\d+)$', '', obj.name[5:]) for obj in runtime_objects(project)
                   if obj.name.startswith('Zone_')})


def _cache(items):
    key = tuple(items)
    return _enum_cache.setdefault(key, items)


def _zones(_self, context):
    return _cache([('NONE', 'Default / global', '')] + [(name, name, '') for name in zone_names(context.scene.pm_vr_project)])


def _looks(_self, context):
    return _cache([('ALL', 'All lighting looks', '')] + [(look.look_id, look.display_name, '')
                                                       for look in context.scene.pm_vr_project.lighting_looks])


def _roles(self, context):
    obj = source_object(context.object)
    return _cache([(key, label, 'Use the ' + key + ' naming convention', index)
                   for index, (key, label, kind) in enumerate(ROLES)
                   if (key in CREATABLE if self.create else obj is None or obj.type == kind or key == 'Probe' and obj.type == 'EMPTY')])


def runtime_name(project, role, tail, zone='NONE', look_id='ALL', instance=0):
    if role in FIXED:
        return role + (f'_{instance:03}' if role.startswith('Music_') and instance else '')
    if role == 'SkyPoint':
        return 'SkyPoint_' + (zone if zone != 'NONE' else '')
    if role == 'Skybox':
        name = 'Skybox' + ('_' + zone if zone != 'NONE' else '')
        look = looks.find(project, look_id)
        # Runtime look tokens include Day too; export-file default suffixes
        # are a separate convention. ALL keeps a genuinely shared sky.
        return name + ('_' + look.display_name if look else '')
    if role in ('Navmesh', 'Collision'):
        return role + ('_' + tail if tail else '')
    return role + '_' + tail


def media_hints(name):
    name = name.replace('.', '_')
    role, _, tail = name.partition('_')
    if role in ('SFX', 'Ambience'):
        return ['audio/sfx/' + re.sub(r'[._]?\d+$', '', name) + '.mp3']
    if role == 'Hotspot':
        return ['hotspots/' + name.replace('.', '_') + '.jpg']
    if role in ('InfoButton', 'InfoPanel'):
        return ['info/' + tail + '.md']
    if role == 'Video':
        return ['video/' + re.sub(r'[._]?\d+$', '', tail) + '.mov (or .mp4)']
    if role == 'Inspect':
        return ['inspect/' + tail + '.md', 'inspect/' + tail + '.usdz']
    if role == 'Music':
        return ['audio/music/main/']
    return []


def naming_problem(project, obj, name, role, creating=False):
    kind = next(kind for key, _, kind in ROLES if key == role)
    if not creating and (obj is None or obj.type != kind and not (role == 'Probe' and obj.type == 'EMPTY')):
        return f'This role needs a {kind.lower()} object'
    if creating and role not in CREATABLE:
        return 'Select an existing mesh for this role'
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.\d+)?', name) or name.endswith('_'):
        return 'Use Latin letters and digits; enter a name after the role'
    if role == 'Zone':
        tail = re.sub(r'[._]\d+$', '', name[5:])
        if '_' in tail or tail.casefold() in {look.display_name.casefold() for look in project.lighting_looks}:
            return 'Zone: one word, without underscores; different from lighting names'
    normalized = name.replace('.', '_').casefold()
    if any(existing != obj and existing.name.replace('.', '_').casefold() == normalized
           for existing in bpy.data.objects):
        return 'That object name is already in use'
    return ''


class PMVR_OT_RuntimeRole(bpy.types.Operator):
    bl_idname = 'pmvr.runtime_role'
    bl_label = 'Runtime Object'
    bl_options = {'REGISTER', 'UNDO'}
    create: bpy.props.BoolProperty(default=False)
    role: bpy.props.EnumProperty(name='Role', items=_roles)
    tail: bpy.props.StringProperty(name='Name', default='')
    zone: bpy.props.EnumProperty(name='Zone', items=_zones)
    look_id: bpy.props.EnumProperty(name='Lighting', items=_looks)
    instance: bpy.props.IntProperty(name='Instance', default=0, min=0, description='0 uses the base name; multiple music anchors use a numeric suffix')

    @classmethod
    def poll(cls, context):
        return bool(context.scene.pm_vr_project.initialized and not context.scene.pm_vr_project.operation_running
                    and (context.object is None or context.object.mode == 'OBJECT'))

    def invoke(self, context, _event):
        obj = source_object(context.object)
        if self.create:
            self.role = 'Hotspot'
        if not self.create and obj:
            role = role_of(obj)
            if role:
                self.role = role
            else:
                self.role = 'Video' if obj.type == 'MESH' else ('Probe' if obj.type == 'CAMERA' else 'Hotspot')
            if role.startswith('Music_'):
                match = re.search(r'[._](\d+)$', obj.name)
                self.instance = int(match.group(1)) if match else 0
            self.tail = obj.name[len(role) + 1:] if role and obj.name.startswith(role + '_') else obj.name
            if role == 'SkyPoint' and self.tail in zone_names(context.scene.pm_vr_project):
                self.zone = self.tail
            if role == 'Skybox':
                project = context.scene.pm_vr_project
                for zone in zone_names(project):
                    if obj.name == 'Skybox_' + zone or obj.name.startswith('Skybox_' + zone + '_'):
                        self.zone = zone
                for look in project.lighting_looks:
                    if obj.name.endswith('_' + look.display_name):
                        self.look_id = look.look_id
        return context.window_manager.invoke_props_dialog(self, width=430)

    def draw(self, context):
        project = context.scene.pm_vr_project
        self.layout.prop(self, 'role')
        if self.role.startswith('Music_'):
            self.layout.prop(self, 'instance')
        if self.role in ('Skybox', 'SkyPoint'):
            self.layout.prop(self, 'zone')
            if self.role == 'Skybox':
                self.layout.prop(self, 'look_id')
        elif self.role not in FIXED:
            self.layout.prop(self, 'tail')
        name = runtime_name(project, self.role, self.tail, self.zone, self.look_id, self.instance)
        self.layout.label(text=name, icon='OBJECT_DATA')
        problem = naming_problem(project, source_object(context.object) if not self.create else None,
                                 name, self.role, self.create)
        if problem:
            self.layout.label(text=problem, icon='ERROR')
        for hint in media_hints(name):
            self.layout.label(text=hint, icon='FILE')
        self.layout.label(text='Source in Runtime • Export Original', icon='RENDERLAYERS')
        if self.role in ('StartPosition', 'Hotspot'):
            self.layout.label(text='On the floor; +Y is forward; rotate around Z')
        if self.role == 'SFX':
            self.layout.label(text='Sphere size 1; scale controls the sound radius')
        if self.role == 'Zone':
            self.layout.label(text='Scale the box to the room; include the floor and feet')
        if self.role == 'Skybox':
            look = looks.find(project, self.look_id)
            self.layout.label(text='Collection: ' + (look.display_name + ' lighting' if look else 'outside lighting collections'), icon='OUTLINER_COLLECTION')

    def execute(self, context):
        role = self.role
        project = context.scene.pm_vr_project
        root = project.source_root_collection
        if context.object and context.object.mode != 'OBJECT':
            self.report({'ERROR'}, 'Return to Object Mode before creating or naming Runtime objects')
            return {'CANCELLED'}
        obj = None if self.create else source_object(context.object)
        name = runtime_name(project, role, self.tail.strip(), self.zone, self.look_id, self.instance)
        problem = naming_problem(project, obj, name, role, self.create)
        if not root:
            problem = 'Choose Source Root in Project Settings'
        elif not root.is_editable:
            problem = 'Source Root is linked or not editable'
        if role == 'Skybox' and self.look_id != 'ALL':
            look = looks.find(project, self.look_id)
            if not look or not look.lighting_collection or not look.lighting_collection.is_editable:
                problem = 'Choose an editable lighting collection for this look in Project Settings'
        if obj and (not editable(obj) or obj.pm_vr_pipeline.bake_unit_id):
            problem = 'Use an editable object outside a bake unit'
        if problem:
            self.report({'ERROR'}, problem)
            return {'CANCELLED'}
        from .setup_ops import add_layer, _drop_incompatible_extra_exports
        layer = next((layer for layer in project.render_layers if layer.layer_type == 'RUNTIME'), None)
        if layer is None:
            layer = add_layer(project, 'Runtime', 'RUNTIME')
        if self.create:
            if role == 'Zone':
                bpy.ops.mesh.primitive_cube_add(size=2, location=context.scene.cursor.location)
                obj = context.object
                obj.display_type = 'WIRE'
            else:
                data = bpy.data.cameras.new(name) if role == 'Probe' else None
                obj = bpy.data.objects.new(name, data)
                obj.location = context.scene.cursor.location
                obj.empty_display_type = 'SPHERE' if role == 'SFX' else 'ARROWS'
                obj.empty_display_size = 1.0 if role == 'SFX' else 0.3
            collection = next((child for child in root.children if child.name == 'Runtime'), None)
            if collection is None:
                collection = bpy.data.collections.new('Runtime')
                root.children.link(collection)
            if obj.name not in collection.objects:
                collection.objects.link(obj)
            if role == 'Zone':
                for parent in tuple(obj.users_collection):
                    if parent != collection:
                        parent.objects.unlink(obj)
            if role == 'Probe':
                probes = project.probe_collection
                if probes is None:
                    probes = bpy.data.collections.new('Probes')
                    collection.children.link(probes)
                    project.probe_collection = probes
                if obj.name not in probes.objects:
                    probes.objects.link(obj)
        elif obj.name not in root.all_objects:
            root.objects.link(obj)
        obj.name = name
        if role == 'Probe' and obj.type == 'CAMERA':
            probes = project.probe_collection
            if probes is None:
                probes = bpy.data.collections.new('Probes')
                root.children.link(probes)
                project.probe_collection = probes
            if obj.name not in probes.objects:
                probes.objects.link(obj)
        if role == 'Skybox':
            from .state import collection_contains
            look = looks.find(project, self.look_id)
            target = look.lighting_collection if look else root
            if obj.name not in target.objects:
                target.objects.link(obj)
            for collection in tuple(obj.users_collection):
                if collection == target:
                    continue
                if look:
                    keep = collection_contains(target, collection)
                else:
                    keep = not any(item.lighting_collection and collection_contains(item.lighting_collection, collection)
                                   for item in project.lighting_looks)
                if not keep:
                    collection.objects.unlink(obj)
        ensure_source_id(obj)
        meta = obj.pm_vr_pipeline
        meta.render_layer_id = layer.layer_id
        meta.processing_role = 'EXPORT_ORIGINAL'
        _drop_incompatible_extra_exports(project, meta, layer)
        from . import preview
        if role == 'Skybox' and self.look_id != 'ALL':
            from .state import activate_state, PipelineStateError
            try:
                activate_state(context, self.look_id)
                from .setup_ops import preview_state
                preview_state(project)
            except PipelineStateError as exc:
                self.report({'WARNING'}, str(exc))
        preview.set_mode(project, 0)
        context.view_layer.update()
        for selected in context.selected_objects:
            selected.select_set(False)
        try:
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except RuntimeError:
            self.report({'WARNING'}, 'Object named; enable its collection in this View Layer to select it')
        return {'FINISHED'}


def runtime_issues(project):
    objects = runtime_objects(project)
    issues = list(platform.runtime_warnings(objects))
    if not any(obj.name == 'StartPosition' and obj.type == 'EMPTY' for obj in objects):
        issues.append(('', 'Add one StartPosition Empty'))
    if not any(obj.name.startswith('Navmesh') and obj.type == 'MESH' for obj in objects):
        issues.append(('', 'Assign a walkable mesh to Navmesh'))
    if not any(obj.name.startswith('Probe_') and obj.type in ('EMPTY', 'CAMERA') for obj in objects):
        issues.append(('', 'Add at least one reflection probe'))
    for look in project.lighting_looks:
        sky = 'Skybox_' + look.display_name
        if not any(obj.name in ('Skybox', sky) and obj.type == 'MESH' for obj in objects):
            issues.append(('', 'Missing default sky for ' + look.display_name + ': ' + sky))
    names = {obj.name for obj in objects}
    base = Path(bpy.path.abspath(project.usdz_output_directory)).parent
    for obj in objects:
        role = role_of(obj)
        for key in fields_for(project, obj):
            if key not in obj:
                continue
            value = obj[key]
            kind = FIELDS[key][1]
            if (kind == 'STRING' and not isinstance(value, str)
                    or kind == 'INT' and not isinstance(value, int)
                    or kind == 'FLOAT' and not isinstance(value, (float, int))):
                issues.append((obj.name, key + ': invalid value type'))
            elif kind == 'FLOAT' and (value < 0 or key in ('volume', 'opacity') and value > 1):
                issues.append((obj.name, key + ': value outside the supported range'))
        if role in ('InfoButton', 'InfoPanel'):
            partner = ('InfoPanel' if role == 'InfoButton' else 'InfoButton') + obj.name[len(role):]
            if partner not in names:
                issues.append((obj.name, 'Missing paired object: ' + partner))
        if role == 'SkyPoint' and obj.name[9:] not in zone_names(project):
            issues.append((obj.name, 'Select an existing zone'))
        if role == 'Probe':
            from .probes import probe_path
            for look in project.lighting_looks:
                path = Path(probe_path(project, obj, look.look_id))
                if not path.is_file():
                    issues.append((obj.name, 'Missing panorama: ' + path.name))
        for hint in media_hints(obj.name):
            first = hint.split(' (or ')[0]
            alternatives = [base / first]
            if '(or .mp4)' in hint:
                alternatives.append((base / first).with_suffix('.mp4'))
            if '(or .glb)' in hint:
                alternatives.append((base / first).with_suffix('.glb'))
            if not any(path.exists() for path in alternatives):
                issues.append((obj.name, 'Missing ' + hint))
    # Group properties may be authored on any one instance, but not conflict.
    groups = {}
    for obj in objects:
        if role_of(obj) in ('SFX', 'Ambience'):
            group = re.sub(r'[._]?\d+$', '', obj.name)
            for key in ('title', 'volume'):
                if key in obj:
                    groups.setdefault((group, key), []).append((obj.name, obj[key]))
    for (group, key), values in groups.items():
        if any(value != values[0][1] for _, value in values[1:]):
            issues.append((values[0][0], f'{group}: conflicting {key} values'))
    return issues


class PMVR_OT_SelectRuntimeIssue(bpy.types.Operator):
    bl_idname = 'pmvr.select_runtime_issue'
    bl_label = 'Select Object'
    object_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return bool(not context.scene.pm_vr_project.operation_running
                    and (context.object is None or context.object.mode == 'OBJECT'))

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None or obj.name not in context.view_layer.objects:
            self.report({'WARNING'}, 'Object is not in the current View Layer')
            return {'CANCELLED'}
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class PMVR_OT_CheckRuntime(bpy.types.Operator):
    bl_idname = 'pmvr.check_runtime'
    bl_label = 'Check Runtime'

    def invoke(self, context, _event):
        return context.window_manager.invoke_popup(self, width=650)

    def draw(self, context):
        issues = runtime_issues(context.scene.pm_vr_project)
        self.layout.label(text=f'{len(issues)} issue(s)' if issues else 'Runtime names and linked resources look good',
                          icon='ERROR' if issues else 'CHECKMARK')
        for name, message in issues:
            row = self.layout.row(align=True)
            if name:
                row.operator('pmvr.select_runtime_issue', text=name, icon='RESTRICT_SELECT_OFF').object_name = name
            row.label(text=message)
        self.layout.label(text='AM checks export completeness and scene placement after export.', icon='INFO')

    def execute(self, _context):
        return {'FINISHED'}


def draw_property(layout, context, obj, key):
    row = layout.row(align=True)
    if key in obj:
        kind = FIELDS[key][1]
        valid = isinstance(obj[key], str) if kind == 'STRING' else isinstance(obj[key], (float, int))
        if valid:
            row.prop(obj, '["' + key + '"]', text=FIELDS[key][0])
        else:
            row.label(text=FIELDS[key][0] + ': invalid value type', icon='ERROR')
    else:
        row.label(text=FIELDS[key][0])
        op = row.operator('pmvr.platform_property', text='Add', icon='ADD')
        op.key, op.action = key, 'ADD'
        op.target_uid = str(obj.session_uid)


def draw_runtime(layout, context, project, obj):
    box = section(layout, 'pmvr_setup_runtime', 'Runtime', 'EMPTY_AXIS')
    if box is None:
        return
    box.enabled = not project.operation_running
    box.operator('pmvr.check_runtime', text='Check Runtime', icon='CHECKMARK')
    box.operator('pmvr.runtime_role', text='Create Runtime', icon='ADD').create = True
    if obj and not obj.pm_vr_pipeline.bake_unit_id:
        box.operator('pmvr.runtime_role', text='Set Runtime role / name', icon='SORTALPHA')
    layer = find_layer(project, obj.pm_vr_pipeline.render_layer_id) if obj else None
    if layer and layer.layer_type == 'RUNTIME':
        for hint in media_hints(obj.name):
            box.label(text=hint, icon='FILE')


def draw_properties(layout, context, project, obj):
    box = section(layout, 'pmvr_setup_properties', 'Object Properties', 'PROPERTIES', default_closed=False)
    if box is None:
        return
    box.enabled = not project.operation_running
    if context.object and context.object.get(TAG_GENERATED) and obj is None:
        box.label(text='Cannot find a unique original source', icon='ERROR')
    elif obj is None:
        box.label(text='Select a source or generated object', icon='INFO')
    if obj:
        box.label(text=('Original: ' if context.object != obj else '') + obj.name,
                  icon='LINKED' if context.object != obj else 'OBJECT_DATA')
        fields = fields_for(project, obj)
        if fields:
            body = box.column(align=True)
            body.enabled = editable(obj)
            for key in fields:
                draw_property(body, context, obj, key)
        else:
            box.label(text='No platform properties for this layer')


def draw(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        return
    from . import preview
    preview.draw(layout, project)
    obj = source_object(context.object)
    draw_runtime(layout, context, project, obj)
    draw_properties(layout, context, project, obj)


CLASSES = (PMVR_OT_PlatformProperty, PMVR_OT_RuntimeRole, PMVR_OT_SelectRuntimeIssue, PMVR_OT_CheckRuntime)
