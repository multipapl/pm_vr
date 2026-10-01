"""Configured nested cameras: all looks, local previews and temporary USD Empties."""
from pathlib import Path
import math
import json
import struct
import sys
from unittest.mock import patch

import bpy
from pxr import Usd, UsdGeom

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo.parent))
sys.path.insert(0, str(repo / 'tests'))
import blender_probes_smoke as base
base.PM_VR.register()
from PM_VR.modules.pipeline import export, looks, probes, validation
from PM_VR.modules.pipeline.state import activate_state

project, output = base.build()
output = Path(output)
collection = bpy.data.collections['PrRuntime']
collection.name = 'Probes'
project.probe_collection_version = 0
probes.migrate(project)
assert project.probe_collection == collection, 'Legacy Probes collection did not migrate'
nested = bpy.data.collections.new('NestedProbes')
collection.children.link(nested)
base.camera(nested, 'Probe_Unassigned', 'PERSP')
project.probe_width = '1024'
project.cycles_samples = 1
night = bpy.data.collections.new('NightLights')
project.source_root_collection.children.link(night)
world = base.world('NightWorld', .04)
bpy.ops.pmvr.add_lighting_look(name='Night', lighting_collection=night.name, world=world.name)
project.bake_day = project.bake_evening = False
assert not looks.checked(project)
assert len(probes.probe_cameras(project, 'DAY')) == 4
assert len(probes.probe_states(project)) == 3, 'Probes must render unchecked looks too'
assert ('Probe_Unassigned', 'Probe camera is not assigned to a Runtime layer') in probes.runtime_warnings(project)
assert any(x.severity == 'WARNING' and x.object_name == 'Probe_Unassigned'
           for x in validation.validate_all(bpy.context))
before = base.settings(bpy.context.scene)
camera_data = {obj.name: (obj.data.as_pointer(), tuple(obj.rotation_euler), tuple(obj.scale))
               for obj in probes.probe_cameras(project, 'DAY')}
if '--export-only' not in sys.argv:
    assert bpy.ops.pmvr.render_probes() == {'FINISHED'}
    assert '12 ready, 0 failed' in project.last_operation_summary
    assert base.settings(bpy.context.scene) == before
    assert not any(name.startswith('__PMVR_PROBE_CAMERA') for name in bpy.data.cameras.keys())
    previews = output / 'PMVR' / 'ProbePreviews'
    assert len(list(previews.glob('*.jpg'))) == 12
    assert len(list((output / 'probes').glob('*.exr'))) == 12
    assert not list((output / 'probes').glob('*.jpg'))
    for path in (output / 'probes').glob('*.exr'):
        spec, pixels = base.read(str(path))
        assert (spec.width, spec.height) == (1024, 512)
        assert spec.get_string_attribute('compression') == 'zip'
        assert list(spec.channelnames) == ['R', 'G', 'B']
        assert all(str(spec.channelformat(i)) == 'half' for i in range(3))
    for path in previews.glob('*.jpg'):
        spec, pixels = base.read(str(path))
        assert (spec.width, spec.height) == (1024, 512) and pixels.max() > 0
    for name, original in camera_data.items():
        obj = bpy.data.objects[name]
        assert (obj.data.as_pointer(), tuple(obj.rotation_euler), tuple(obj.scale)) == original

layer = next(x for x in project.render_layers if x.layer_type == 'RUNTIME')
camera = bpy.data.objects['Probe_Test']
parent = bpy.data.objects.new('StartPosition', None)
project.source_root_collection.objects.link(parent)
base.register(parent, layer.layer_id)
parent.location = (1, 2, 3)
parent.rotation_euler.z = .7
parent.scale = (2, 3, 4)
camera.parent = parent
camera.location = (.2, .3, .4)
child = bpy.data.objects['RuntimePerspective']
child.parent = camera
child.location = (.4, .2, .1)
bpy.context.view_layer.update()
position = tuple(camera.matrix_world.translation)
child_position = tuple(child.matrix_world.translation)
original = (camera.name, camera.data.as_pointer(), camera.parent.as_pointer(),
            tuple(camera.matrix_basis), dict(camera.items()))
activate_state(bpy.context, 'DAY')
status, path = export.export_semantic_layer(bpy.context, layer, 'USDZ')
assert status == 'SUCCESS'
stage = Usd.Stage.Open(path)
assert not any(prim.IsA(UsdGeom.Camera) for prim in stage.Traverse()), 'Probe cameras must export as Empties'
prim = next(x for x in stage.Traverse() if x.GetName() == 'Probe_Test')
matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
assert all(abs(a - b) < 1e-5 for a, b in zip(matrix.ExtractTranslation(), position))
assert all(abs(matrix[i][j] - (1 if i == j else 0)) < 1e-5 for i in range(3) for j in range(3)), matrix
child_prim = next(x for x in stage.Traverse() if x.GetName() == child.name)
child_matrix = UsdGeom.Xformable(child_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
assert all(abs(a-b) < 1e-5 for a,b in zip(child_matrix.ExtractTranslation(), child_position))
assert all(abs(child_matrix[i][j] - (1 if i == j else 0)) < 1e-5 for i in range(3) for j in range(3)), child_matrix
assert (camera.name, camera.data.as_pointer(), camera.parent.as_pointer(),
        tuple(camera.matrix_basis), dict(camera.items())) == original
status, glb_path = export.export_semantic_layer(bpy.context, layer, 'GLB')
assert status == 'SUCCESS'
data = Path(glb_path).read_bytes()
assert data[:4] == b'glTF'
length, kind = struct.unpack_from('<II', data, 12)
assert kind == 0x4E4F534A
document = json.loads(data[20:20+length])
assert not document.get('cameras'), 'Camera rig leaked into GLB'
for name, world in ((camera.name, position), (child.name, child_position)):
    node = next(item for item in document['nodes'] if item['name'] == name)
    assert 'camera' not in node
    assert all(abs(a-b) < 1e-5 for a,b in zip(node.get('translation', (0, 0, 0)), (world[0], world[2], -world[1])))
    assert node.get('rotation', [0, 0, 0, 1]) == [0, 0, 0, 1]
    assert node.get('scale', [1, 1, 1]) == [1, 1, 1]
assert (camera.name, camera.data.as_pointer(), camera.parent.as_pointer(),
        tuple(camera.matrix_basis), dict(camera.items())) == original
with patch.object(export.collection_export, 'export_usdz', side_effect=RuntimeError('injected export failure')):
    try:
        export.export_semantic_layer(bpy.context, layer, 'USDZ')
        raise AssertionError('Injected export failure was ignored')
    except RuntimeError as exc:
        assert 'injected export failure' in str(exc)
assert (camera.name, camera.data.as_pointer(), camera.parent.as_pointer(),
        tuple(camera.matrix_basis), dict(camera.items())) == original
assert not any(obj.name.startswith(('__PMVR_PROBE_SOURCE_', '__PMVR_PROBE_EXPORT_')) for obj in bpy.data.objects)
assert not list(output.rglob('*.pmvr_tmp*'))
print('PROBE_COLLECTION_EVIDENCE', output, flush=True)
print('PMVR_PROBE_COLLECTION_SMOKE_OK')
