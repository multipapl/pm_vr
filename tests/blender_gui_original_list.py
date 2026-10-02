"""Click and wheel-scroll the actual Original Objects UIList in a real window."""
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import selection_sync, ui
from PM_VR.modules.pipeline.identity import layer_members, new_id

STATE = {'step': 0, 'drawn': set(), 'errors': []}
original_draw_item = ui.PMVR_UL_OriginalObjects.draw_item


def draw_item(self, context, layout, data, item, icon, active, active_prop, index):
    STATE['list'] = self
    STATE['drawn'].add(item.name)
    try:
        original_draw_item(self, context, layout, data, item, icon, active, active_prop, index)
    except Exception:
        STATE['errors'].append(traceback.format_exc())
        raise


def panel_draw(self, context):
    # Minimal host panel keeps test coordinates independent of other tools.
    # The list and its filtering, item drawing and selection are production UI.
    project = context.scene.pm_vr_project
    layer = project.render_layers[project.active_render_layer_index]
    STATE['panels'] = STATE.get('panels', 0) + 1
    try:
        ui.draw_original_objects(self.layout, project, layer_members(layer.layer_id))
    except Exception:
        STATE['errors'].append(traceback.format_exc())
        raise


ui.PMVR_UL_OriginalObjects.draw_item = draw_item
PM_VR.PMVR_PT_MainPanel.draw = panel_draw
PM_VR.PMVR_PT_MainPanel.bl_category = 'PMVR Test'
PM_VR.PMVR_PT_MainPanel.bl_order = -1000


def event(kind, value='PRESS', x=None, y=None):
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'UI')
    window.event_simulate(type=kind, value=value,
                          x=region.x + 120 if x is None else x,
                          y=region.y + region.height - STATE.get('row_offset', 190) if y is None else y)


def click():
    event('MOUSEMOVE', 'NOTHING')
    event('LEFTMOUSE')
    event('LEFTMOUSE', 'RELEASE')


def finish():
    for error in STATE['errors']:
        print('ORIGINAL_LIST_GUI_FAIL', error, flush=True)
    print('PMVR_GUI_ORIGINAL_LIST_FAILED' if STATE['errors'] else 'PMVR_GUI_ORIGINAL_LIST_OK', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = STATE['step']
        if step > 0:
            # Fresh Blender opens Tool; activate the isolated tab in this test's
            # own window before exercising its list. Category state is read-only.
            window = bpy.context.window_manager.windows[0]
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'UI')
            if region.active_panel_category != 'PMVR Test':
                offset = STATE.get('tab_offset', 50)
                assert offset < 500, 'Could not activate test sidebar tab'
                STATE['tab_offset'] = offset + 15
                event('MOUSEMOVE', 'NOTHING', region.x + region.width - 12, region.y + region.height - offset)
                for value in ('PRESS', 'RELEASE'):
                    event('LEFTMOUSE', value, region.x + region.width - 12, region.y + region.height - offset)
                return .1
        if step == 0:
            PM_VR.register()
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            project = bpy.context.scene.pm_vr_project
            root = bpy.data.collections.new('Original Scroll Root')
            bpy.context.scene.collection.children.link(root)
            project.source_root_collection = root
            bpy.ops.pmvr.initialize_project()
            for kind, count in (('GLASS', 30), ('RUNTIME', 45)):
                layer = next(l for l in project.render_layers if l.layer_type == kind)
                for i in range(count):
                    obj = bpy.data.objects.new(f'{kind}_{i:02}', None)
                    root.objects.link(obj)
                    meta = obj.pm_vr_pipeline
                    meta.is_registered_source, meta.source_id = True, new_id()
                    meta.render_layer_id, meta.processing_role = layer.layer_id, 'EXPORT_ORIGINAL'
            project.active_render_layer_index = next(i for i, l in enumerate(project.render_layers) if l.layer_type == 'GLASS')
            bpy.context.scene.pm_vr_ui_state.stage = 'SETUP'
            window = bpy.context.window_manager.windows[0]
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            area.spaces.active.show_region_ui = True
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.screen.screen_full_area()
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'UI')
        elif step == 1:
            STATE['screenshot'] = str(Path(tempfile.mkdtemp(prefix='pmvr_original_gui_')) / 'list.png')
            bpy.ops.screen.screenshot(filepath=STATE['screenshot'])
            print('ORIGINAL_LIST_SCREENSHOT', STATE['screenshot'], flush=True)
            assert STATE['drawn'], f'Native object rows never drawn; panels={STATE.get("panels", 0)}'
            print('ORIGINAL_LIST_VISIBLE_START', sorted(STATE['drawn']), flush=True)
            STATE['first'] = set(STATE['drawn'])
            click()
        elif step == 2:
            project = bpy.context.scene.pm_vr_project
            if selection_sync.original_object(project) is None:
                offset = STATE.get('row_offset', 190) + 20
                assert offset < 450, 'Mouse click could not reach original rows'
                STATE['row_offset'] = offset
                click()
                return .1
            bpy.ops.screen.screenshot(filepath=STATE['screenshot'].replace('list.png', 'clicked.png'))
            assert bpy.context.object and bpy.context.object.name.startswith('GLASS_'), 'Mouse click did not select Glass row'
            assert bpy.context.selected_objects == [bpy.context.object]
            print('ORIGINAL_LIST_GLASS_CLICK', bpy.context.object.name, flush=True)
            STATE['drawn'].clear()
            event('MOUSEMOVE', 'NOTHING')
            event('WHEELDOWNMOUSE')
        elif step == 3:
            if max((int(n.split('_')[-1]) for n in STATE['drawn']), default=-1) < 29:
                STATE['glass_wheels'] = STATE.get('glass_wheels', 0) + 1
                assert STATE['glass_wheels'] < 40, 'Later Glass rows inaccessible'
                event('WHEELDOWNMOUSE')
                return .08
            print('ORIGINAL_LIST_GLASS_AFTER_WHEEL', sorted(STATE['drawn']), flush=True)
            bpy.ops.screen.screenshot(filepath=STATE['screenshot'].replace('list.png', 'scrolled.png'))
            assert STATE['drawn'] - STATE['first'], 'Mouse wheel did not reveal more Glass objects'
            assert max(int(n.split('_')[-1]) for n in STATE['drawn']) >= 20, 'Later Glass rows inaccessible'
            click()
        elif step == 4:
            assert int(bpy.context.object.name.split('_')[-1]) >= 15, 'Could not click scrolled Glass row'
            print('ORIGINAL_LIST_GLASS_SCROLLED_CLICK', bpy.context.object.name, flush=True)
            project = bpy.context.scene.pm_vr_project
            project.active_render_layer_index = next(i for i, l in enumerate(project.render_layers) if l.layer_type == 'RUNTIME')
            STATE['drawn'].clear()
        elif step == 5:
            assert STATE['drawn'] and all(n.startswith('RUNTIME_') for n in STATE['drawn']), 'Layer filter leaked other objects'
            STATE['first'] = set(STATE['drawn'])
            click()
        elif step == 6:
            assert bpy.context.object.name.startswith('RUNTIME_'), 'Could not click Runtime row'
            print('ORIGINAL_LIST_RUNTIME_CLICK', bpy.context.object.name, flush=True)
            STATE['drawn'].clear()
            event('WHEELDOWNMOUSE')
        elif step == 7:
            if max((int(n.split('_')[-1]) for n in STATE['drawn']), default=-1) < 44:
                STATE['runtime_wheels'] = STATE.get('runtime_wheels', 0) + 1
                assert STATE['runtime_wheels'] < 55, 'Last Runtime rows inaccessible'
                event('WHEELDOWNMOUSE')
                return .08
            assert STATE['drawn'] - STATE['first'], 'Mouse wheel did not reveal more Runtime objects'
            assert max(int(n.split('_')[-1]) for n in STATE['drawn']) >= 40, 'Last Runtime rows inaccessible'
            click()
        elif step == 8:
            assert int(bpy.context.object.name.split('_')[-1]) >= 35, 'Could not click last Runtime rows'
            print('ORIGINAL_LIST_RUNTIME_SCROLLED_CLICK', bpy.context.object.name, flush=True)
            STATE['list'].use_filter_show = True
            STATE['list'].filter_name = 'runtime_04'
            STATE['drawn'].clear()
            area.tag_redraw()
        else:
            assert STATE['drawn'] == {'RUNTIME_04'}, f'Search showed wrong rows: {STATE["drawn"]}'
            print('ORIGINAL_LIST_SEARCH_OK', flush=True)
            assert not STATE['errors']
            finish()
            return None
        STATE['step'] += 1
        return .6
    except Exception:
        STATE['errors'].append(traceback.format_exc())
        finish()
    return None


bpy.app.timers.register(tick, first_interval=1)
