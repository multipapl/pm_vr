"""Real single-look/third-look/legacy bakes, variants, exports and queue resume."""
from pathlib import Path
import json
import runpy
import sys
import tempfile

import bpy
from pxr import Usd, UsdGeom

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
import PM_VR
PM_VR.register()
from PM_VR.modules.pipeline import bake, export, looks, scenarios, variants
from PM_VR.modules.pipeline import export_description
from PM_VR.modules.pipeline.constants import TAG_SOURCE_ID, TAG_UNIT_ID
from PM_VR.modules.pipeline.generated import find_generated
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state, unique_layer_collection

base = runpy.run_path(str(repo / 'tests/blender_export_visibility_smoke.py'), run_name='looks_fixture')
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
output = Path(tempfile.mkdtemp(prefix='pmvr_lighting_looks_'))
scene, project = bpy.context.scene, bpy.context.scene.pm_vr_project
bpy.ops.pmvr.initialize_project()
assert len(project.lighting_looks) == 1, 'New projects must not require an empty Evening'
root = bpy.data.collections.new('LookSources')
scene.collection.children.link(root)
day = base['child'](root, 'LookDay')
nested = base['child'](day, 'NestedDay')
project.source_root_collection = root
project.day_lighting_collection = day
project.day_world = bpy.data.worlds.new('LookDayWorld')
project.day_world.color = (.25, .25, .25)
project.cycles_samples = 1
project.bake_resolution = '256'
project.beauty_denoise = 'OFF'
project.fill_empty_uv = False
scene.cycles.device = 'CPU'
for field in ('beauty_output_directory', 'usdz_output_directory', 'glb_output_directory'):
    setattr(project, field, str(output))
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'Looks.blend'))
layer = next(x for x in project.render_layers if x.layer_type == 'UNLIT')
for x in project.render_layers:
    x.enabled = x.layer_id == layer.layer_id
obj = base['add_plane'](root, 'LookFloor', 0)
unit = project.bake_units.add()
unit.unit_id = unit.artifact_key = new_id()
unit.display_name, unit.render_layer_id, unit.resolution = 'Floor', layer.layer_id, '256'
base['register'](obj, layer.layer_id, 'BAKE', unit.unit_id)
unit.variant_material = obj.data.materials[0]
variant = unit.variants.add()
variant.variant_id, variant.title = new_id(), 'Blue'
variant.material = obj.data.materials[0].copy()
variant.material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (.1, .2, .9, 1)
unit_id, source_id = unit.unit_id, obj.pm_vr_pipeline.source_id

def bake_look(state):
    activate_state(bpy.context, state)
    for variant_id in ('', variant.variant_id):
        runtime = bake.BeautyBakeRuntime(bpy.context, unit, variant_id=variant_id)
        assert runtime.prepare() == 'READY'
        for i in range(len(runtime.receivers)):
            runtime.select_receiver(i)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()
    assert variants.variant_status(unit, variant, state) == 'Ready'
    result = looks.result_value(unit, state, 'image_name')
    assert Path(bpy.path.abspath(bpy.data.images[result].filepath)).is_file()
    success, path = export.export_semantic_layer(bpy.context, layer, 'USDZ')
    assert success == 'SUCCESS', (success, path)
    suffix = looks.suffix(project, state)
    assert Path(path).name == 'Unlit' + suffix + '.usdz'
    stage = Usd.Stage.Open(path)
    assert any(prim.IsA(UsdGeom.Mesh) for prim in stage.Traverse())
    info = json.loads((output / ('PMVR_Export_' + looks.name(project, state) + '.json')).read_text())
    assert info['look']['id'] == state and info['look']['suffix'] == suffix
    assert any(x['type'] == 'VARIANT' for x in info['files'])
    return looks.result_value(unit, state, 'signature')

day_signature = bake_look('DAY')
assert unit.day_signature == day_signature and unit.day_status == 'Ready'
assert next(x for x in unit.beauty_results if x.look_id == 'DAY').signature == day_signature
assert variant.day_file == looks.variant_file(variant, 'DAY')
generated = find_generated(unit_id, source_id)
generated_id, mesh_name = generated.as_pointer(), generated.data.name

# Nested lighting always comes on as a complete subtree.
unique_layer_collection(bpy.context.view_layer, nested, 'Nested')[0].exclude = True
activate_state(bpy.context, 'DAY')
assert not unique_layer_collection(bpy.context.view_layer, nested, 'Nested')[0].exclude
night = base['child'](root, 'NightLighting')
night_nested = base['child'](night, 'NestedNight')
night_world = bpy.data.worlds.new('NightWorld')
assert bpy.ops.pmvr.add_lighting_look(name='Night', lighting_collection=night.name, world=night_world.name) == {'FINISHED'}
night_id = project.lighting_looks[-1].look_id
project.lighting_looks[-1].bake_enabled = True
assert looks.checked(project) == ['DAY', night_id]
assert scenarios.Scope(project, bpy.context.view_layer).lighting == {day.name, night.name}
assert bake_look(night_id) == day_signature
assert looks.active_id(project) == night_id and unit.day_signature == day_signature
assert generated.as_pointer() == generated_id and generated.data.name == mesh_name
assert generated[TAG_SOURCE_ID] == source_id and generated[TAG_UNIT_ID] == unit_id

# Legacy pointer edits still work and new results dual-write v2 fields.
evening = base['child'](root, 'EveningLighting')
project.evening_lighting_collection = evening
project.evening_world = bpy.data.worlds.new('EveningWorld')
assert looks.find(project, 'EVENING')
assert bake_look('EVENING') == day_signature
assert unit.evening_status == 'Ready' and unit.evening_signature == day_signature
assert variant.evening_file.endswith('_Evening_Beauty.png')
assert looks.record_id(project.build_records[-1]) == 'EVENING'

entry = project.bake_queue.add()
entry.unit_id = unit_id
states = ['DAY', night_id, 'EVENING']
bake.mark_queue_done(project, unit_id, 'DAY', states)
bake.mark_queue_done(project, unit_id, night_id, states)
assert len(project.bake_queue) == 1 and looks.queue_done(project.bake_queue[0], night_id)
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'Looks.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output / 'Looks.blend'))
project = bpy.context.scene.pm_vr_project
assert len(project.lighting_looks) == 3
assert looks.queue_done(project.bake_queue[0], night_id)
assert looks.result_value(project.bake_units[0], night_id, 'status') == 'Ready'
night_look = looks.find(project, night_id)
night_look.display_name = 'Dusk'
export_description.write(project, night_id)
assert not (output / 'PMVR_Export_Night.json').exists()
assert (output / 'PMVR_Export_Dusk.json').is_file()
night_look.display_name = 'Night'
export_description.write(project, night_id)
project.bake_units[0].variants[0].title = 'Night'
try:
    looks.validate_names(project)
    raise AssertionError('Look/variant bake filename collision was accepted')
except ValueError as exc:
    assert 'duplicate bake filenames' in str(exc)
project.bake_units[0].variants[0].title = 'Blue'
bake.mark_queue_done(project, unit_id, 'EVENING', states)
assert not project.bake_queue
for name in ('Bad_Name', 'Glass', 'Night'):
    before = len(project.lighting_looks)
    try:
        result = bpy.ops.pmvr.add_lighting_look(name=name)
        assert result == {'CANCELLED'}
    except RuntimeError:
        pass
    assert len(project.lighting_looks) == before
unit = project.bake_units[0]
expected = {'unit_id': unit.unit_id, 'source_id': source_id,
            'mesh_name': find_generated(unit_id, source_id).data.name,
            'legacy': {field: getattr(unit, field) for field in (
                'day_signature', 'evening_signature', 'day_beauty_image', 'evening_beauty_image',
                'day_status', 'evening_status', 'day_baked_resolution', 'evening_baked_resolution')},
            'variants': {field: getattr(unit.variants[0], field) for field in (
                'day_file', 'evening_file', 'day_signature', 'evening_signature')}}
(output / 'rollback_expected.json').write_text(json.dumps(expected), encoding='utf-8')
print('LIGHTING_LOOKS_EVIDENCE', str(output), flush=True)
print('PMVR_LIGHTING_LOOKS_SMOKE_OK')
