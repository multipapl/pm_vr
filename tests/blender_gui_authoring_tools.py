"""Draw the actual contextual fields and Runtime/settings dialogs in a window."""
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import authoring, setup_ops, ui

STATE = {'step': 0, 'draws': {}, 'errors': []}


def recording(function, name):
    def draw(first, second):
        STATE['draws'][name] = STATE['draws'].get(name, 0) + 1
        try:
            return function(first, second)
        except Exception:
            STATE['errors'].append(name + ': ' + traceback.format_exc())
            raise
    return draw


authoring.draw = recording(authoring.draw, 'fields')
authoring.PMVR_OT_RuntimeRole.draw = recording(authoring.PMVR_OT_RuntimeRole.draw, 'role')
authoring.PMVR_OT_CheckRuntime.draw = recording(authoring.PMVR_OT_CheckRuntime.draw, 'check')
setup_ops.PMVR_OT_ProjectSettings.draw = recording(setup_ops.PMVR_OT_ProjectSettings.draw, 'settings')
ui.draw_bake = recording(ui.draw_bake, 'bake')
ui.draw_export = recording(ui.draw_export, 'export')
PM_VR.PMVR_PT_MainPanel.bl_category = 'PMVR Test'


def finish():
    for error in STATE['errors']:
        print('AUTHORING_TOOLS_FAIL', error, flush=True)
    print('PMVR_GUI_AUTHORING_TOOLS_FAILED' if STATE['errors'] else 'PMVR_GUI_AUTHORING_TOOLS_OK', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = STATE['step']
        if step > 0:
            window = bpy.context.window_manager.windows[0]
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'UI')
            if region.active_panel_category != 'PMVR Test':
                offset = STATE.get('tab_offset', 50)
                assert offset < 500, 'Could not activate authoring test tab'
                STATE['tab_offset'] = offset + 15
                x, y = region.x + region.width - 12, region.y + region.height - offset
                window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
                for value in ('PRESS', 'RELEASE'):
                    window.event_simulate(type='LEFTMOUSE', value=value, x=x, y=y)
                return .1
        if step == 0:
            PM_VR.register()
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            root = bpy.data.collections.new('Artist Root')
            bpy.context.scene.collection.children.link(root)
            bpy.context.scene.pm_vr_project.source_root_collection = root
            bpy.ops.pmvr.initialize_project()
            bpy.ops.pmvr.runtime_role(create=True, role='SFX', tail='Street01')
            bpy.ops.pmvr.platform_property(key='title', action='ADD')
            bpy.ops.pmvr.platform_property(key='volume', action='ADD')
            bpy.ops.pmvr.platform_property(key='reflectionIntensity', action='ADD')
            bpy.context.object['title'] = 'Вулиця'
            bpy.context.scene.pm_vr_ui_state.stage = 'SETUP'
            window = bpy.context.window_manager.windows[0]
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            area.spaces.active.show_region_ui = True
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.screen_full_area()
        elif step == 1:
            assert STATE['draws'].get('fields'), 'Context fields were never drawn'
            screenshot = str(Path(tempfile.mkdtemp(prefix='pmvr_setup_ui_')) / 'setup.png')
            bpy.ops.screen.screenshot(filepath=screenshot)
            print('AUTHORING_SETUP_SCREENSHOT', screenshot, flush=True)
            assert bpy.ops.pmvr.runtime_role('INVOKE_DEFAULT', create=False) == {'RUNNING_MODAL'}
        elif step == 2:
            assert STATE['draws'].get('role'), 'Runtime role dialog was never drawn'
            bpy.context.window.event_simulate(type='ESC', value='PRESS')
        elif step == 3:
            bpy.ops.pmvr.check_runtime('INVOKE_DEFAULT')
        elif step == 4:
            assert STATE['draws'].get('check'), 'Runtime issues were never drawn'
            bpy.context.window.event_simulate(type='ESC', value='PRESS')
        elif step == 5:
            bpy.ops.pmvr.project_settings('INVOKE_DEFAULT')
        elif step == 6:
            assert STATE['draws'].get('settings'), 'Scene reflection field was never drawn'
            bpy.context.window.event_simulate(type='ESC', value='PRESS')
        elif step == 7:
            STATE['setup_draws'] = STATE['draws']['fields']
            bpy.context.scene.pm_vr_ui_state.stage = 'BAKE'
        elif step == 8:
            assert STATE['draws'].get('bake'), 'Bake stage was never drawn'
            assert STATE['draws']['fields'] == STATE['setup_draws'], 'Authoring tools drawn in Bake'
            bpy.context.scene.pm_vr_ui_state.stage = 'EXPORT'
        elif step == 9:
            assert STATE['draws'].get('export'), 'Export stage was never drawn'
            assert STATE['draws']['fields'] == STATE['setup_draws'], 'Authoring tools drawn in Export'
        else:
            assert not STATE['errors'], STATE['errors']
            print('AUTHORING_UI_DRAWS', STATE['draws'], flush=True)
            finish()
            return None
        STATE['step'] += 1
        return .6
    except Exception:
        STATE['errors'].append(traceback.format_exc())
        finish()
    return None


bpy.app.timers.register(tick, first_interval=1)
