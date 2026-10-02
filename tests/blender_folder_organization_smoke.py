"""Legacy migration, save/reopen, rollback, collisions and interrupted saves."""
import json
from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
PM_VR.register()
from PM_VR.modules.pipeline import folder_organization as folders, looks

root = Path(tempfile.mkdtemp(prefix='pmvr_organize_'))
scene = bpy.context.scene
project = scene.pm_vr_project
assert bpy.ops.pmvr.initialize_project() == {'FINISHED'}
project.legacy_working_directory = True
project.beauty_output_directory = '//Beauty_Bakes/'
project.flattened_output_directory = '//PMVR_Flattened/'
project.log_output_directory = '//PMVR_Logs/'
project.lightmap_output_directory = '//Lightmaps/'
project.usdz_output_directory = '//OwnerSync/USD/'
project.glb_output_directory = '//OwnerSync/GLB/'
project.probe_output_directory = '//OwnerSync/probes/'
external = root / 'OwnerTextures'
external.mkdir()
(external / 'keep.txt').write_text('owner texture', encoding='utf-8')
for name in ('Beauty_Bakes', 'PMVR_Flattened', 'PMVR_Logs'):
    (root / name).mkdir()
    (root / name / 'keep.txt').write_text(name, encoding='utf-8')
# A real file-backed image, shared by original/generated-style materials.
image = bpy.data.images.new('Existing atlas', 4, 4)
image.filepath_raw = str(root / 'Beauty_Bakes' / 'atlas.png')
image.file_format = 'PNG'
image.save()
image.filepath = '//Beauty_Bakes/atlas.png'
image['pm_lightmap_export_path'] = str(root / 'Beauty_Bakes' / 'atlas.png')
obj = bpy.context.object
object_name = obj.name
obj['stable_id'] = 'unchanged-source'
material = bpy.data.materials.new('Source using external atlas')
material.use_nodes = True
material.node_tree.nodes.new('ShaderNodeTexImage').image = image
obj.data.materials.append(material)
unit = project.bake_units.add()
unit.unit_id = 'stable-unit'
looks.set_result(unit, 'DAY', signature='v2:unchanged', image_name=image.name, status='Ready')
variant = unit.variants.add()
looks.set_variant_result(variant, 'DAY', '//Beauty_Bakes/atlas.png', 'v2:unchanged')
looks.set_variant_result(variant, 'NIGHT', '//Beauty_Bakes/atlas.png', 'v2:unchanged')
other = bpy.data.scenes.new('Second scene')
other.pm_vr_project.beauty_output_directory = '//Beauty_Bakes/'
other.pm_vr_project.legacy_working_directory = True
blend = root / 'Legacy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
prefs = bpy.context.preferences.filepaths.save_version

# No overwrites, including a previously existing empty destination.
destination = root / 'PMVR' / 'Bakes'
destination.mkdir(parents=True)
try:
    folders.organize(bpy.context)
    raise AssertionError('Existing destination was overwritten')
except folders.OrganizationError:
    pass
destination.rmdir()
assert image.filepath == '//Beauty_Bakes/atlas.png'

# Failure while saving rolls back live paths and leaves originals available.
save = folders._save_blend
def failed_save(path, copy=False):
    if not copy:
        raise OSError('Injected save failure')
    return save(path, copy)
folders._save_blend = failed_save
try:
    folders.organize(bpy.context)
    raise AssertionError('Save failure was swallowed')
except OSError:
    pass
finally:
    folders._save_blend = save
assert image.filepath == '//Beauty_Bakes/atlas.png'
assert (root / 'Beauty_Bakes' / 'atlas.png').is_file()
assert not destination.exists()
assert bpy.context.preferences.filepaths.save_version == prefs

report = folders.organize(bpy.context)
assert report['phase'] == 'COMPLETE'
assert not any((root / name).exists() for name in folders.FOLDERS)
assert image.filepath == '//PMVR/Bakes/atlas.png'
assert Path(bpy.path.abspath(image['pm_lightmap_export_path'])).is_file()
assert project.beauty_output_directory.rstrip('/\\') == '//PMVR/Bakes'
assert project.lightmap_output_directory.rstrip('/\\') == '//PMVR/Lightmaps'
assert project.usdz_output_directory == '//OwnerSync/USD/'
assert project.glb_output_directory == '//OwnerSync/GLB/'
assert project.probe_output_directory == '//OwnerSync/probes/'
assert other.pm_vr_project.beauty_output_directory.rstrip('/\\') == '//PMVR/Bakes'
assert looks.variant_file(variant, 'DAY') == '//PMVR/Bakes/atlas.png'
assert looks.variant_file(variant, 'NIGHT') == '//PMVR/Bakes/atlas.png'
assert looks.result_value(unit, 'DAY', 'signature') == 'v2:unchanged'
assert (external / 'keep.txt').read_text() == 'owner texture'
assert bpy.context.preferences.filepaths.save_version == prefs
assert folders.organize(bpy.context) is None, 'Repeated migration must be a no-op'
bpy.ops.wm.open_mainfile(filepath=str(blend))
assert bpy.data.images['Existing atlas'].filepath.replace('\\', '/') == '//PMVR/Bakes/atlas.png'
assert bpy.data.objects[object_name]['stable_id'] == 'unchanged-source'
assert bpy.context.scene.pm_vr_project.bake_units[0].day_signature == 'v2:unchanged'
journal = Path(report['backupBlend']).parent / 'organization.json'
assert json.loads(journal.read_text())['phase'] == 'COMPLETE'
try:
    folders.rollback(journal)
    raise AssertionError('Restored an open project')
except folders.OrganizationError:
    pass
bpy.ops.wm.read_factory_settings(use_empty=True)
assert folders.rollback(journal)['phase'] == 'RESTORED'
assert (root / 'Beauty_Bakes' / 'atlas.png').is_file()
assert (root / 'PMVR' / 'Bakes' / 'atlas.png').is_file(), 'Rollback lost organized files'
bpy.ops.wm.open_mainfile(filepath=str(blend))
assert bpy.data.images['Existing atlas'].filepath.replace('\\', '/') == '//Beauty_Bakes/atlas.png'
assert bpy.context.scene.pm_vr_project.bake_units[0].day_signature == 'v2:unchanged'

# External paths on nodes of linked materials cannot be redirected locally.
# Refuse before copying anything or touching the linked datablock.
linked_root = root / 'LinkedSafety'
(linked_root / 'Beauty_Bakes').mkdir(parents=True)
local = bpy.data.materials.new('Linked IES safety')
local.use_nodes = True
ies = local.node_tree.nodes.new('ShaderNodeTexIES')
ies.mode = 'EXTERNAL'
ies.filepath = str(linked_root / 'Beauty_Bakes' / 'profile.ies')
library_file = linked_root / 'Material.blend'
bpy.data.libraries.write(str(library_file), {local})
with bpy.data.libraries.load(str(library_file), link=True) as (source, destination):
    destination.materials = ['Linked IES safety']
linked = destination.materials[0]
assert linked.library
bpy.ops.wm.save_as_mainfile(filepath=str(linked_root / 'LinkedReview.blend'), relative_remap=False)
try:
    folders.organize(bpy.context)
    raise AssertionError('Attempted to edit a linked material path')
except folders.OrganizationError as exc:
    assert 'linked datablock' in str(exc), str(exc)
assert not (linked_root / 'PMVR/Backups').exists()
print('PMVR_FOLDER_ORGANIZATION_SMOKE_OK', flush=True)
