"""Real modal queue: only committed results, Evening preview, selection isolation."""
from pathlib import Path
import time
import traceback

import bpy

path = Path(__file__).with_name('blender_gui_bake_resume.py')
exec(path.read_text(encoding='utf-8').split('def tick():')[0])
from PM_VR.modules.pipeline import authoring, generated, looks


def finish():
    for problem in STATE['problems']:
        print('GUI_AUTHORING_FAIL', problem)
    print('PMVR_GUI_AUTHORING_FAILED' if STATE['problems'] else 'PMVR_GUI_AUTHORING_OK', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if STATE['phase'] == 'setup':
            setup()
            project().beauty_denoise = 'OFF'
            project().cycles_samples = 1
            project().bake_autosave_minutes = 0
            bpy.context.scene.pm_vr_ui_state.stage = 'BAKE'
            # First successful queue; the default preview switches must change.
            check(project().preview_mode == 'SOURCES', 'Default preview is mixed')
            STATE['t0'] = time.monotonic()
            STATE['phase'] = 'initial'
            bake()
            return .2
        if time.monotonic() - STATE['t0'] > 150:
            check(False, 'Queue timed out')
            finish()
            return None
        if project().operation_running:
            # Clicking indices while a job owns selection must leave its receiver.
            active = bpy.context.view_layer.objects.active
            project().active_bake_queue_index = 0
            project().active_bake_unit_index = 2
            check(bpy.context.view_layer.objects.active == active, 'List selection disturbed a bake receiver')
            return .2
        if STATE['phase'] == 'initial':
            check(not project().bake_queue, 'Initial successful queue did not clear')
            check(project().preview_mode == 'GENERATED' and project().preview_last_queue, 'Queue did not select Generated/focus')
            check(len(project().preview_results) == 6, 'Missing Day/Evening committed results')
            check(bpy.context.object and bpy.context.object.get('pmvr_generated'), 'Shader Editor still follows a hidden original')
            for name in 'ABC':
                source = bpy.data.objects[name + '0']
                result = generated.find_generated(unit_id(name), source.pm_vr_pipeline.source_id)
                check(source.hide_get() and not result.hide_get(), name + ' visibility is mixed')
                check(result.data.materials[0].node_tree.nodes.active.name == 'PMVR Baked Beauty', name + ' active image')
            # Evening only; B fails, its old evening result must stay hidden.
            project().bake_day = False
            project().bake_evening = True
            project().bake_queue.add().unit_id = unit_id('A')
            project().bake_queue.add().unit_id = unit_id('B')
            source_b = bpy.data.objects['B0']
            source_b.data.uv_layers.remove(source_b.data.uv_layers['SimpleBake'])
            STATE['phase'] = 'partial'
            bake()
            return .2
        if STATE['phase'] == 'partial':
            check(looks.active_id(project()) == 'EVENING', 'Evening-only rebake showed old Day')
            check(project().preview_mode == 'GENERATED' and project().preview_last_queue, 'Partial result did not focus')
            check(len(project().preview_results) == 1 and project().preview_results[0].unit_id == unit_id('A'), 'Failed old result looks fresh')
            for name in 'ABC':
                source = bpy.data.objects[name + '0']
                result = generated.find_generated(unit_id(name), source.pm_vr_pipeline.source_id)
                check(source.hide_get(), name + ' source remains visible')
                check(result.hide_get() == (name != 'A'), name + ' generated focus is wrong')
            check('1 ready, 0 skipped, 1 failed' in project().last_operation_summary, project().last_operation_summary)
            project().preview_last_queue = False
            for name in 'ABC':
                source = bpy.data.objects[name + '0']
                result = generated.find_generated(unit_id(name), source.pm_vr_pipeline.source_id)
                check(not result.hide_get(), 'Removing queue focus did not reveal ' + name)
            project().preview_mode = 'SOURCES'
            check(not bpy.data.objects['A0'].hide_get(), 'Sources mode did not return originals')
            STATE['phase'] = 'all_failed'
            bake()  # Only the previously failed B remains queued.
            return .2
        if STATE['phase'] == 'all_failed':
            check(not project().preview_results and project().preview_last_queue, 'All-failed queue displayed prior commits as fresh')
            check(project().preview_mode == 'GENERATED', 'Failed queue lost the clear preview mode')
            for name in 'ABC':
                source = bpy.data.objects[name + '0']
                result = generated.find_generated(unit_id(name), source.pm_vr_pipeline.source_id)
                check(result.hide_get(), name + ' old result looks freshly baked after all failures')
            project().preview_last_queue = False
            finish()
            return None
    except Exception:
        check(False, traceback.format_exc())
        finish()
    return None


bpy.app.timers.register(tick, first_interval=1)
