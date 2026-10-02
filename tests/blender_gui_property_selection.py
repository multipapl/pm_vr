"""Adding/editing linked-opacity Glass does not reselect a remembered bake unit."""
from pathlib import Path
import sys
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import authoring, selection_sync
from PM_VR.modules.pipeline.identity import ensure_source_id

STATE = {'step': 0, 'errors': []}


def override():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return {'window': window, 'area': area,
            'region': next(r for r in area.regions if r.type == 'WINDOW')}


def select(obj):
    for current in bpy.context.selected_objects:
        current.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def check_selection():
    assert bpy.context.object.name == 'GlassTarget', 'Active object jumped to another source'
    assert [o.name for o in bpy.context.selected_objects] == ['GlassTarget'], 'Selection changed'
    assert bpy.data.objects['GlassTarget'].pm_vr_pipeline.source_id == STATE['glass_id']
    assert bpy.data.objects['RememberedUnit'].pm_vr_pipeline.source_id == STATE['decoy_id']


def finish():
    for error in STATE['errors']:
        print('PROPERTY_SELECTION_FAIL', error, flush=True)
    print('PMVR_GUI_PROPERTY_SELECTION_FAILED' if STATE['errors'] else 'PMVR_GUI_PROPERTY_SELECTION_OK', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = STATE['step']
        if step == 0:
            PM_VR.register()
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete()
            root = bpy.data.collections.new('Property selection root')
            bpy.context.scene.collection.children.link(root)
            project = bpy.context.scene.pm_vr_project
            project.source_root_collection = root
            bpy.ops.pmvr.initialize_project()
            bpy.ops.mesh.primitive_cube_add()
            decoy = bpy.context.object
            decoy.name = 'RememberedUnit'
            for collection in list(decoy.users_collection):
                collection.objects.unlink(decoy)
            root.objects.link(decoy)
            decoy.data.uv_layers[0].name = 'UVMap'
            decoy.data.uv_layers.new(name='SimpleBake')
            project.active_render_layer_index = next(i for i, l in enumerate(project.render_layers) if l.layer_type == 'PBR')
            bpy.ops.pmvr.add_bake_unit()
            STATE['decoy_id'] = decoy.pm_vr_pipeline.source_id
            bpy.ops.mesh.primitive_cube_add()
            glass = bpy.context.object
            glass.name = 'GlassTarget'
            for collection in list(glass.users_collection):
                collection.objects.unlink(glass)
            root.objects.link(glass)
            material = bpy.data.materials.new('Linked opacity')
            material.use_nodes = True
            value = material.node_tree.nodes.new('ShaderNodeValue')
            value.outputs[0].default_value = .18
            principled = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
            material.node_tree.links.new(value.outputs[0], principled.inputs['Alpha'])
            glass.data.materials.append(material)
            meta = glass.pm_vr_pipeline
            STATE['glass_id'] = ensure_source_id(glass)
            meta.processing_role = 'EXPORT_ORIGINAL'
            meta.render_layer_id = next(l.layer_id for l in project.render_layers if l.layer_type == 'GLASS')
            bpy.context.scene.pm_vr_ui_state.stage = 'SETUP'
            select(glass)
            selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
            assert authoring.fallback(glass, 'opacity') is None, 'Fixture is not the formerly modal path'
            bpy.ops.ed.undo_push(message='Glass selected')
        elif step == 1:
            glass = bpy.data.objects['GlassTarget']
            with bpy.context.temp_override(**override()):
                result = bpy.ops.pmvr.platform_property('INVOKE_DEFAULT', True, key='opacity', target_uid=str(glass.session_uid))
            assert result == {'FINISHED'}, 'Add opened a mode dialog'
            check_selection()
            assert glass['opacity'] == 1
        elif step == 2:
            check_selection()
            bpy.data.objects['GlassTarget']['opacity'] = .27
            bpy.ops.ed.undo_push(message='Opacity edited')
        elif step == 3:
            with bpy.context.temp_override(**override()):
                bpy.ops.ed.undo()
        elif step == 4:
            check_selection()
            assert bpy.data.objects['GlassTarget']['opacity'] == 1, 'Undo did not restore the added value'
        elif step == 5:
            with bpy.context.temp_override(**override()):
                bpy.ops.ed.redo()
        else:
            check_selection()
            assert abs(bpy.data.objects['GlassTarget']['opacity'] - .27) < 1e-6
            assert 'opacity' not in bpy.data.objects['RememberedUnit']
            print('PROPERTY_SELECTION_ADD_EDIT_UNDO_REDO_OK', flush=True)
            finish()
            return None
        STATE['step'] += 1
        return .5
    except Exception:
        STATE['errors'].append(traceback.format_exc())
        finish()
    return None


bpy.app.timers.register(tick, first_interval=1, persistent=True)
