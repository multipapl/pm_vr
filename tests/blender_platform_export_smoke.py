"""Real USDZ: late properties, Glass, full partial inventory and safe failures."""
import hashlib
import json
import math
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

import bpy
from pxr import Sdf, Usd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
PM_VR.register()
from PM_VR.modules.pipeline import bake, export, export_description, generated, platform, variants
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state

fixture = runpy.run_path(str(Path(__file__).with_name('blender_export_visibility_smoke.py')))
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
scene = bpy.context.scene
project = scene.pm_vr_project
project.initialized = True
project.cycles_samples = 1
project.bake_resolution = '256'
project.beauty_denoise = 'OFF'
project.fill_empty_uv = False
output = Path(tempfile.mkdtemp(prefix='pmvr_platform_export_'))
for name in ('beauty_output_directory', 'usdz_output_directory', 'glb_output_directory'):
    setattr(project, name, str(output))
root = bpy.data.collections.new('PlatformSources')
scene.collection.children.link(root)
project.source_root_collection = root
project.day_lighting_collection = fixture['child'](root, 'Day')
project.evening_lighting_collection = fixture['child'](root, 'Evening')
project.day_world = bpy.data.worlds.new('Day')
project.evening_world = bpy.data.worlds.new('Evening')
project.day_lighting_collection.objects.link(bpy.data.objects.new('Sun', bpy.data.lights.new('Sun', 'SUN')))
layer = project.render_layers.add()
layer.layer_id, layer.display_name, layer.layer_type = new_id(), 'Cloth', 'UNLIT'
source = fixture['add_plane'](root, 'AuthoredCloth', 0)
unit = project.bake_units.add()
unit.unit_id, unit.artifact_key = new_id(), new_id()
unit.display_name, unit.render_layer_id, unit.resolution = 'ClothUnit', layer.layer_id, '256'
fixture['register'](source, layer.layer_id, 'BAKE', unit.unit_id)
activate_state(bpy.context, 'DAY')
runtime = bake.BeautyBakeRuntime(bpy.context, unit)
try:
    assert runtime.prepare() != 'SKIPPED'
    runtime.select_receiver(0)
    assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    runtime.finish()
finally:
    runtime.cleanup()
obj = generated.find_generated(unit.unit_id, source.pm_vr_pipeline.source_id)
obj['opacity'] = 0.11
obj['brightness'] = 0.4
source['opacity'] = 0.82
source['brightness'] = 1.4
source['title'] = 'Стільниця'
source['order'] = 3
source['volume'] = 0.7
source['emissiveIntensity'] = 2.0
scene['reflectionIntensity'] = 1.7
original_tags = dict(obj.items())
original_ids = obj.as_pointer(), obj.data.as_pointer(), obj.name, obj.data.name
original_signature = unit.day_signature
png = Path(bpy.path.abspath(bpy.data.images[unit.day_beauty_image].filepath))
png_hash = hashlib.sha256(png.read_bytes()).hexdigest()

def properties(path):
    layer = Sdf.Layer.FindOrOpen(str(path))
    layer.Reload()
    stage = Usd.Stage.Open(layer)
    return {name: prim.GetAttribute('userProperties:' + name).Get()
            for prim in stage.Traverse()
            for name in platform.OBJECT_PROPERTIES
            if prim.GetAttribute('userProperties:' + name)}

def description(name='Day'):
    return json.loads((output / ('PMVR_Export_' + name + '.json')).read_text(encoding='utf-8'))

status, cloth_path = export.export_semantic_layer(bpy.context, layer, 'USDZ')
assert status == 'SUCCESS'
values = properties(cloth_path)
for key in platform.OBJECT_PROPERTIES:
    actual, wanted = values[key], source[key]
    assert actual == wanted if isinstance(wanted, str) else math.isclose(actual, wanted, abs_tol=1e-6)
assert dict(obj.items()) == original_tags
source['opacity'] = 0.45
export.export_semantic_layer(bpy.context, layer, 'USDZ')
assert math.isclose(properties(cloth_path)['opacity'], 0.45, abs_tol=1e-6)
del source['opacity']
export.export_semantic_layer(bpy.context, layer, 'USDZ')
assert 'opacity' not in properties(cloth_path)
assert obj['opacity'] == 0.11
source['opacity'] = 0.45

glass_layer = project.render_layers.add()
glass_layer.layer_id, glass_layer.display_name, glass_layer.layer_type = new_id(), 'Glass', 'GLASS'
glass = fixture['add_plane'](root, 'AuthoredGlass', 2)
fixture['register'](glass, glass_layer.layer_id, 'EXPORT_ORIGINAL')
glass['opacity'] = 0.18
status, glass_path = export.export_semantic_layer(bpy.context, glass_layer, 'USDZ')
assert math.isclose(properties(glass_path)['opacity'], 0.18, abs_tol=1e-6)
doc = description()
assert doc['schema'] == 1 and doc['look']['id'] == 'DAY' and doc['look']['default']
assert doc['settings'] == {'reflectionIntensity': 1.7}
assert {item['file'] for item in doc['files']} == {'Cloth.usdz', 'Glass.usdz'}

unit.variant_material = source.material_slots[0].material
unit.variant_group_title = 'Камінь'
variant = unit.variants.add()
variant.variant_id, variant.title, variant.material = new_id(), 'Stone', unit.variant_material.copy()
variants.set_result(variant, 'DAY', str(png), unit.day_signature)
export.export_semantic_layer(bpy.context, layer, 'USDZ')
variant_rows = [item for item in description()['files'] if item['type'] == 'VARIANT']
assert len(variant_rows) == 1 and Path(output / variant_rows[0]['file']).is_file()
export.write_variant_manifest(project)
manifest = json.loads((output / 'Variants/materialVariants.json').read_text(encoding='utf-8'))
assert manifest['materialVariants'][0]['title'] == 'Камінь'
assert manifest['materialVariants'][0]['id'] == 'clothunit'

before = Path(cloth_path).read_bytes()
before_doc = (output / 'PMVR_Export_Day.json').read_bytes()
with patch.object(export, '_check_written', side_effect=export.PipelineExportError('injected invalid USD')):
    try:
        export.export_semantic_layer(bpy.context, layer, 'USDZ')
        raise AssertionError('Invalid USD was published')
    except export.PipelineExportError:
        pass
assert Path(cloth_path).read_bytes() == before
assert (output / 'PMVR_Export_Day.json').read_bytes() == before_doc
assert dict(obj.items()) == original_tags
with patch.object(export_description.os, 'replace', side_effect=OSError('injected publishing error')):
    try:
        export_description.write(project, 'DAY')
        raise AssertionError('Description publishing error was ignored')
    except OSError:
        pass
assert (output / 'PMVR_Export_Day.json').read_bytes() == before_doc
assert not list(output.glob('.pmvr_description_*'))

layer.display_name = 'RenamedCloth'
export.export_semantic_layer(bpy.context, glass_layer, 'USDZ')
assert 'Cloth.usdz' not in {item['file'] for item in description()['files']}
assert Path(cloth_path).read_bytes() == before
project.render_layers.remove(0)
glass_layer = project.render_layers[0]
export.export_semantic_layer(bpy.context, glass_layer, 'USDZ')
assert {item['file'] for item in description()['files']} == {'Glass.usdz'}
activate_state(bpy.context, 'EVENING')
export.export_semantic_layer(bpy.context, glass_layer, 'USDZ')
assert description('Evening')['look'] == {'id': 'EVENING', 'name': 'Evening', 'suffix': '_Evening', 'default': False}
assert [item['file'] for item in description('Evening')['files']] == ['Glass_Evening.usdz']
unknown = bpy.data.objects.new('Cube_Authoring', None)
root.objects.link(unknown)
unknown_before = unknown.name, dict(unknown.items())
assert platform.runtime_warnings([unknown])
assert (unknown.name, dict(unknown.items())) == unknown_before
assert (obj.as_pointer(), obj.data.as_pointer(), obj.name, obj.data.name) == original_ids
assert unit.day_signature == original_signature
assert hashlib.sha256(png.read_bytes()).hexdigest() == png_hash
print('PLATFORM_EXPORT_EVIDENCE', str(output), flush=True)
print('PMVR_PLATFORM_EXPORT_SMOKE_OK')
