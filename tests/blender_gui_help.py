"""Open every real help tab; close only the owned popup with simulated Esc."""
from pathlib import Path
import sys
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import ui

CASES = [('RULES', 'Scene'), ('RULES', 'Bake'), ('RUNTIME', 'Scene'), ('AFTER', 'Scene')]
STATE = {'index': 0, 'open': False, 'registered': False}
DRAWN, ERRORS = set(), []
original_draw = ui.PMVR_OT_ShowHelp.draw


def checked_draw(self, context):
    try:
        original_draw(self, context)
        DRAWN.add((self.help_tab, self.rule_section))
    except Exception:
        ERRORS.append(traceback.format_exc())
        raise


ui.PMVR_OT_ShowHelp.draw = checked_draw


def tick():
    try:
        if not STATE['registered']:
            PM_VR.register()
            STATE['registered'] = True
        window = bpy.context.window_manager.windows[0]
        if STATE['open']:
            window.event_simulate(type='ESC', value='PRESS')
            window.event_simulate(type='ESC', value='RELEASE')
            STATE['open'] = False
            STATE['index'] += 1
            return .4
        if STATE['index'] == len(CASES):
            assert not ERRORS and set(CASES).issubset(DRAWN), (ERRORS, DRAWN)
            print('PMVR_GUI_HELP_OK', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        tab, topic = CASES[STATE['index']]
        area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
        region = next(region for region in area.regions if region.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            result = bpy.ops.pmvr.show_help('INVOKE_DEFAULT', help_tab=tab, rule_section=topic)
        assert result == {'RUNNING_MODAL'}, result
        print('HELP_TAB_DRAW', tab, topic, flush=True)
        STATE['open'] = True
        return .8
    except Exception:
        traceback.print_exc()
        print('PMVR_GUI_HELP_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
