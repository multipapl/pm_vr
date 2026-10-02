"""New, unsaved and legacy projects keep separate and persistent output paths."""
from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
PM_VR.register()
from PM_VR.modules import node_flatten, vr_project_tools
from PM_VR.modules.pipeline import log, working_directory

root = Path(tempfile.mkdtemp(prefix='pmvr_working_dirs_'))
project = bpy.context.scene.pm_vr_project
assert bpy.ops.pmvr.initialize_project() == {'FINISHED'}
assert project.beauty_output_directory == '//PMVR/Bakes/'
assert project.lightmap_output_directory == '//PMVR/Lightmaps/'
assert project.external_texture_directory == '//PMVR/Textures/'
assert project.working_directory_version == working_directory.FORMAT_VERSION and not project.legacy_working_directory
assert not (root / 'PMVR').exists(), 'Unsaved initialize must not create project folders'
path = root / 'New.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
assert all((root / 'PMVR' / folder).is_dir() for folder in ('Bakes', 'Flattened', 'Logs', 'ProbePreviews', 'Lightmaps'))
assert Path(log.log_file_path()).parent == root / 'PMVR' / 'Logs'
assert Path(node_flatten._output_path('test', set())).parent == root / 'PMVR' / 'Flattened'
assert Path(vr_project_tools.get_selected_texture_export_directory()) == root / 'PMVR' / 'Textures'
project.beauty_output_directory = '//CustomBakes/'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
project = bpy.context.scene.pm_vr_project
assert project.beauty_output_directory == '//CustomBakes/'
assert not project.legacy_working_directory

# A pre-organization v3 file may have the old Lightmaps default implicitly.
project.working_directory_version = 1
project.property_unset('lightmap_output_directory')
project.property_unset('external_texture_directory')
working_directory.migrate(project)
assert project.lightmap_output_directory == '//Lightmaps/'
assert not project.legacy_working_directory
assert project.external_texture_directory == '//PM_Selected_Textures/'

# Model the serialized properties of a v2 project: new properties absent;
# old defaults were often implicit, so checking their current value is unsafe.
for field in ('working_directory_version', 'legacy_working_directory',
              'beauty_output_directory', 'flattened_output_directory', 'log_output_directory', 'lightmap_output_directory'):
    project.property_unset(field)
assert project.initialized
legacy_dir = root / 'Legacy'
legacy_dir.mkdir()
sentinel = legacy_dir / 'Beauty_Bakes' / 'keep.png'
sentinel.parent.mkdir()
sentinel.write_bytes(b'previous atlas')
legacy_file = legacy_dir / 'Legacy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(legacy_file))
bpy.ops.wm.open_mainfile(filepath=str(legacy_file))
project = bpy.context.scene.pm_vr_project
assert project.legacy_working_directory
assert project.beauty_output_directory == '//Beauty_Bakes/'
assert project.flattened_output_directory == '//PMVR_Flattened/'
assert project.log_output_directory == '//PMVR_Logs/'
assert project.lightmap_output_directory == '//Lightmaps/'
assert Path(log.log_file_path()).parent == legacy_dir / 'PMVR_Logs'
assert Path(node_flatten._output_path('test', set())).parent == legacy_dir / 'PMVR_Flattened'
assert sentinel.read_bytes() == b'previous atlas'
assert not (legacy_dir / 'PMVR').exists(), 'Legacy migration must not create or move working directories'
project.beauty_output_directory = str(root / 'CustomExisting')
project.working_directory_version = 0
working_directory.migrate(project)
assert project.beauty_output_directory == str(root / 'CustomExisting')
bpy.ops.wm.save_as_mainfile(filepath=str(legacy_file))
bpy.ops.wm.open_mainfile(filepath=str(legacy_file))
assert bpy.context.scene.pm_vr_project.beauty_output_directory == str(root / 'CustomExisting')
print('PMVR_WORKING_DIRECTORY_SMOKE_OK')
