"""An actual modal bake queue uses an arbitrary third look without v2 writes."""
from pathlib import Path
import sys
import time
import traceback

import bpy

path = Path(__file__).with_name('blender_gui_bake_resume.py')
exec(path.read_text(encoding='utf-8').split('def tick():')[0])
from PM_VR.modules.pipeline import looks


def finish():
    for problem in STATE['problems']:
        print('GUI_LOOKS_FAIL', problem)
    print('PMVR_GUI_LIGHTING_LOOKS_FAILED' if STATE['problems'] else 'PMVR_GUI_LIGHTING_LOOKS_OK', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE['phase'] == 'setup':
            setup()
            item = project()
            item.cycles_samples = 1
            item.beauty_denoise = 'OFF'
            item.bake_day = item.bake_evening = False
            night = bpy.data.collections.new('NightLights')
            item.source_root_collection.children.link(night)
            world = bpy.data.worlds.new('NightWorld')
            bpy.ops.pmvr.add_lighting_look(name='Night', lighting_collection=night.name, world=world.name)
            look = item.lighting_looks[-1]
            STATE['look_id'] = look.look_id
            look.bake_enabled = True
            check(looks.checked(item) == [look.look_id], 'Bake checks did not select the custom look')
            STATE['t0'] = time.monotonic()
            check(bake() == {'RUNNING_MODAL'}, 'The modal queue did not start')
            STATE['phase'] = 'baking'
            return .2
        if time.monotonic() - STATE['t0'] > 120:
            check(False, 'Custom-look queue timed out')
            finish()
            return None
        if project().operation_running:
            return .2
        check(not project().bake_queue, 'Finished custom look remained in the queue')
        for unit in project().bake_units:
            check(looks.result_value(unit, STATE['look_id'], 'status') == 'Ready', unit.display_name + ' not ready')
            check(not unit.day_signature and not unit.evening_signature, 'A custom look overwrote legacy results')
        check('Night' in project().last_operation_summary and '3 ready, 0 skipped, 0 failed' in project().last_operation_summary,
              project().last_operation_summary)
        finish()
    except Exception:
        check(False, traceback.format_exc())
        finish()
    return None


bpy.app.timers.register(tick, first_interval=1)
