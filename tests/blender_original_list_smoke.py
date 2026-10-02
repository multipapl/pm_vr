"""Original-object navigation survives renames and only Setup authors properties."""
from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules import pipeline
from PM_VR.modules.pipeline import selection_sync
from PM_VR.modules.pipeline.constants import TAG_GENERATED
from PM_VR.modules.pipeline.identity import layer_members, new_id


class Layout:
    def __init__(self, records=None, block=None):
        self.records = [] if records is None else records
        self.block = [] if block is None else block

    def box(self):
        block = []
        self.records.append(('box', block))
        return Layout(self.records, block)

    def panel(self, identifier, **kwargs):
        self.records.append(('panel', identifier, kwargs.get('default_closed', False)))
        box = self.box()
        return box, box

    def row(self, **kwargs):
        return self

    column = row
    split = row

    def label(self, **kwargs):
        self.block.append(kwargs.get('text', ''))
        self.records.append(('label', kwargs.get('text', '')))

    def prop(self, obj, key, **kwargs):
        if key.startswith('["'):
            assert key[2:-2] in obj
        else:
            assert hasattr(obj, key), key
        self.records.append(('prop', key, self.block))

    def operator(self, name, **kwargs):
        namespace, op = name.split('.')
        getattr(getattr(bpy.ops, namespace), op).get_rna_type()
        self.records.append(('op', name, self.block))
        return type('Operator', (), {})()

    def template_list(self, kind, list_id, data, prop, active, active_prop, **kwargs):
        assert hasattr(data, prop) and hasattr(active, active_prop)
        self.records.append(('list', kind, len(getattr(data, prop))))

    def separator(self, **kwargs):
        pass


class ClosedLayout(Layout):
    def box(self):
        block = []
        self.records.append(('box', block))
        return ClosedLayout(self.records, block)

    def panel(self, identifier, **kwargs):
        closed = kwargs.get('default_closed', False)
        self.records.append(('panel', identifier, closed))
        box = self.box()
        return box, None if closed else box


def select(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    project = bpy.context.scene.pm_vr_project
    root = bpy.data.collections.new('Original List Root')
    bpy.context.scene.collection.children.link(root)
    project.source_root_collection = root
    bpy.ops.pmvr.initialize_project()
    bpy.context.scene.pm_vr_ui_state.stage = 'SETUP'
    layer_index = {layer.layer_type: i for i, layer in enumerate(project.render_layers)}
    made = {}
    for kind, count in (('GLASS', 25), ('RUNTIME', 35), ('EMISSIVE', 2)):
        layer = project.render_layers[layer_index[kind]]
        made[kind] = []
        for i in range(count):
            obj = bpy.data.objects.new(f'{kind}_{i:02}', None)
            root.objects.link(obj)
            obj.pm_vr_pipeline.is_registered_source = True
            obj.pm_vr_pipeline.source_id = new_id()
            obj.pm_vr_pipeline.render_layer_id = layer.layer_id
            obj.pm_vr_pipeline.processing_role = 'EXPORT_ORIGINAL'
            made[kind].append(obj)
    bpy.context.view_layer.update()
    glass, other = made['GLASS'][-1], made['RUNTIME'][-1]
    glass['opacity'] = .18
    before = [(obj, obj.pm_vr_pipeline.source_id, obj.pm_vr_pipeline.render_layer_id,
               obj.pm_vr_pipeline.processing_role, obj.hide_render) for obj in root.objects]
    project.active_render_layer_index = layer_index['GLASS']
    project.preview_mode = 'GENERATED'
    project.active_original_object_index = bpy.data.objects.find(glass.name)
    assert bpy.context.object == glass and bpy.context.selected_objects == [glass]
    assert project.preview_mode == 'SOURCES' and not glass.hide_get()
    assert selection_sync.original_object(project) == glass
    # Adding a property has no redo modes and affects the active source only.
    from PM_VR.modules.pipeline.authoring import PMVR_OT_PlatformProperty
    assert 'REGISTER' not in PMVR_OT_PlatformProperty.bl_options
    props = bpy.ops.pmvr.platform_property.get_rna_type().properties
    assert all(props[key].is_hidden for key in ('key', 'action', 'bulk', 'value', 'target_uid'))
    missing, untouched = made['GLASS'][4:6]
    select(missing, untouched)
    before_selection = set(bpy.context.selected_objects)
    assert bpy.ops.pmvr.platform_property('INVOKE_DEFAULT', key='opacity', target_uid=str(missing.session_uid)) == {'FINISHED'}
    assert missing['opacity'] == 1 and 'opacity' not in untouched
    assert set(bpy.context.selected_objects) == before_selection and bpy.context.object == missing
    missing['opacity'] = .27
    assert bpy.ops.pmvr.platform_property('INVOKE_DEFAULT', key='opacity', target_uid=str(missing.session_uid)) == {'FINISHED'}
    assert abs(missing['opacity'] - .27) < 1e-6, 'Add replaced an existing value'
    select(glass)
    assert bpy.ops.pmvr.platform_property('INVOKE_DEFAULT', key='opacity', target_uid=str(missing.session_uid)) == {'CANCELLED'}
    assert bpy.context.object == glass and abs(glass['opacity'] - .18) < 1e-6
    glass.name = 'AAAA_RenamedGlass'
    assert project.active_original_object_index == bpy.data.objects.find(glass.name)
    select(glass, made['GLASS'][0])
    selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
    assert len(bpy.context.selected_objects) == 2, 'Reverse sync expanded/replaced the selection'
    project.active_render_layer_index = layer_index['RUNTIME']
    assert project.active_original_object_index == -1, 'Wrong-layer row highlighted'
    project.active_original_object_index = bpy.data.objects.find(other.name)
    assert bpy.context.object == other and bpy.context.selected_objects == [other]
    select(glass)
    selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
    assert project.active_render_layer_index == layer_index['GLASS']
    assert selection_sync.original_object(project) == glass
    # Programmatic navigation and bake/Edit Mode selection must not be disturbed.
    selection_sync.set_index(project, 'active_original_object_index', bpy.data.objects.find(made['GLASS'][0].name))
    assert bpy.context.object == glass
    project.operation_running = True
    project.active_original_object_index = bpy.data.objects.find(made['GLASS'][1].name)
    assert bpy.context.object == glass
    project.operation_running = False
    excluded = bpy.data.collections.new('Excluded originals')
    root.children.link(excluded)
    hidden = made['GLASS'][2]
    root.objects.unlink(hidden)
    excluded.objects.link(hidden)
    bpy.context.view_layer.update()
    bpy.context.view_layer.layer_collection.children[root.name].children[excluded.name].exclude = True
    project.active_original_object_index = bpy.data.objects.find(hidden.name)
    assert bpy.context.object == glass
    assert bpy.context.view_layer.layer_collection.children[root.name].children[excluded.name].exclude
    # Each authoring purpose occupies its own box and appears only in Setup.
    for stage in ('SETUP', 'BAKE', 'EXPORT'):
        bpy.context.scene.pm_vr_ui_state.stage = stage
        layout = Layout()
        pipeline.draw_stage(layout, bpy.context, stage)
        labels = [r[1] for r in layout.records if r[0] == 'label']
        ops = [r[1] for r in layout.records if r[0] == 'op']
        if stage == 'SETUP':
            assert all(name in labels for name in ('Viewport', 'Runtime', 'Object Properties'))
            boxes = [r[1] for r in layout.records if r[0] == 'box']
            properties = next(block for block in boxes if 'Object Properties' in block)
            runtime = next(block for block in boxes if 'Runtime' in block)
            assert properties is not runtime
            assert all(r[2] is properties for r in layout.records if r[0] == 'op' and r[1] == 'pmvr.platform_property')
            assert all(r[2] is runtime for r in layout.records if r[0] == 'op' and r[1] in ('pmvr.runtime_role', 'pmvr.check_runtime'))
            assert any(r[0] == 'list' and r[1] == 'PMVR_UL_OriginalObjects' for r in layout.records)
        else:
            assert 'Object Properties' not in labels and 'Runtime' not in labels
            assert not set(ops) & {'pmvr.platform_property', 'pmvr.runtime_role', 'pmvr.check_runtime'}
    for obj, source_id, layer_id, role, render in before:
        assert (obj.pm_vr_pipeline.source_id, obj.pm_vr_pipeline.render_layer_id,
                obj.pm_vr_pipeline.processing_role, obj.hide_render) == (source_id, layer_id, role, render)
    # Folding secondary sections must never hide the work lists or Bake button.
    for stage in ('SETUP', 'BAKE'):
        bpy.context.scene.pm_vr_ui_state.stage = stage
        layout = ClosedLayout()
        pipeline.draw_stage(layout, bpy.context, stage)
        records = layout.records
        panels = {r[1]: r[2] for r in records if r[0] == 'panel'}
        ops = [r[1] for r in records if r[0] == 'op']
        lists = [r[1] for r in records if r[0] == 'list']
        if stage == 'SETUP':
            assert 'PMVR_UL_RenderLayers' in lists and 'PMVR_UL_OriginalObjects' in lists
            assert panels['pmvr_setup_runtime'] and panels['pmvr_setup_viewport']
            assert not panels['pmvr_setup_properties']
            assert 'pmvr.runtime_role' not in ops
        else:
            assert 'PMVR_UL_BakeQueue' in lists and 'pmvr.bake_queue' in ops
            assert all(panels[k] for k in ('pmvr_bake_scenarios', 'pmvr_bake_probes', 'pmvr_bake_viewport'))
            assert not set(ops) & {'pmvr.add_bake_scenario', 'pmvr.render_probes'}
            run = next(i for i, r in enumerate(records) if r[0] == 'op' and r[1] == 'pmvr.bake_queue')
            assert all(i > run for i, r in enumerate(records) if r[0] == 'panel')
    doomed = made['GLASS'][3]
    selection_sync.set_original_object(project, doomed)
    bpy.data.objects.remove(doomed, do_unlink=True)
    assert project.active_original_object_index == -1
    # Generated objects never enter this list, even with copied source metadata.
    dummy = bpy.data.objects.new('Generated dummy', None)
    root.objects.link(dummy)
    dummy[TAG_GENERATED] = True
    dummy.pm_vr_pipeline.is_registered_source = True
    dummy.pm_vr_pipeline.render_layer_id = glass.pm_vr_pipeline.render_layer_id
    assert not selection_sync.original_list_member(project, dummy)
    # UI focus never keeps a deleted source alive in the file or export list.
    victim = made['RUNTIME'][0]
    project.active_render_layer_index = layer_index['RUNTIME']
    users = victim.users
    project.active_original_object_index = bpy.data.objects.find(victim.name)
    assert victim.users == users, 'UI focus added an ID user'
    victim_name = victim.name
    bpy.ops.object.delete()
    assert victim_name not in bpy.data.objects and project.active_original_object_index == -1
    replacement = bpy.data.objects.new(victim_name, None)
    root.objects.link(replacement)
    assert selection_sync.original_object(project) is None, 'Name reused by a new object stole the selection'
    # Loading reconstructs UI navigation from viewport selection, without saved IDs.
    bpy.data.objects.remove(dummy, do_unlink=True)
    blend = str(Path(tempfile.mkdtemp(prefix='pmvr_original_list_')) / 'originals.blend')
    project.active_render_layer_index = layer_index['GLASS']
    bpy.context.scene.pm_vr_ui_state.stage = 'SETUP'
    select(glass)
    selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
    glass_name = glass.name
    members = sorted(obj.name for obj in layer_members(glass.pm_vr_pipeline.render_layer_id))
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    project = bpy.context.scene.pm_vr_project
    assert selection_sync.original_object(project) is None
    selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
    assert selection_sync.original_object(project).name == glass_name
    assert project.active_original_object_index == bpy.data.objects.find(glass_name)
    assert sorted(obj.name for obj in layer_members(project.render_layers[layer_index['GLASS']].layer_id)) == members
    print('PMVR_ORIGINAL_LIST_SMOKE_OK')
    PM_VR.unregister()


if __name__ == '__main__':
    main()
