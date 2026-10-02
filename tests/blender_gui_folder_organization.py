"""Real confirmation and saved legacy-folder migration in an isolated window."""
import json
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR

STATE = {'step': 0}


def tick():
    try:
        step = STATE['step']
        window = bpy.context.window_manager.windows[0]
        if step == 0:
            PM_VR.register()
            bpy.ops.pmvr.initialize_project()
            project = bpy.context.scene.pm_vr_project
            project.legacy_working_directory = True
            for field, folder in (('beauty_output_directory', 'Beauty_Bakes'),
                                  ('flattened_output_directory', 'PMVR_Flattened'),
                                  ('log_output_directory', 'PMVR_Logs'),
                                  ('lightmap_output_directory', 'Lightmaps')):
                setattr(project, field, '//' + folder + '/')
            root = Path(tempfile.mkdtemp(prefix='pmvr_gui_organize_'))
            (root / 'Beauty_Bakes').mkdir()
            (root / 'Beauty_Bakes' / 'keep.txt').write_text('existing atlas', encoding='utf-8')
            bpy.ops.wm.save_as_mainfile(filepath=str(root / 'Review.blend'))
            STATE['root'], STATE['active'] = root, bpy.context.object.name
        elif step == 1:
            area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.pmvr.organize_project_folders('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
        elif step == 2:
            window.event_simulate(type='RET', value='PRESS')
            window.event_simulate(type='RET', value='RELEASE')
        else:
            root = STATE['root']
            assert not (root / 'Beauty_Bakes').exists(), 'Confirm did not organize the folder'
            assert (root / 'PMVR/Bakes/keep.txt').read_text() == 'existing atlas'
            assert bpy.context.object.name == STATE['active'], 'Migration changed the active selection'
            journals = list((root / 'PMVR/Backups').glob('BeforeOrganization_*/organization.json'))
            assert len(journals) == 1 and json.loads(journals[0].read_text())['phase'] == 'COMPLETE'
            print('PMVR_GUI_FOLDER_ORGANIZATION_OK', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        STATE['step'] += 1
        return 1
    except Exception:
        traceback.print_exc()
        print('PMVR_GUI_FOLDER_ORGANIZATION_FAILED', flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
