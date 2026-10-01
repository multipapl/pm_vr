"""Actual UniPlace copy: review only intentional probe USD changes; optional render."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import bpy
from pxr import Sdf, Usd, UsdGeom

parser = argparse.ArgumentParser()
parser.add_argument('--copy', required=True)
parser.add_argument('--test-root', required=True)
parser.add_argument('--source-root', required=True)
parser.add_argument('--report-root', required=True)
parser.add_argument('--render-one', action='store_true')
parser.add_argument('--gpu', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
test_root, copy = Path(args.test_root).resolve(), Path(args.copy).resolve()
output, source = Path(args.report_root).resolve(), Path(args.source_root).resolve()
assert copy.is_relative_to(test_root) and output.is_relative_to(test_root)
assert not copy.is_relative_to(source)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
PM_VR.register()
bpy.ops.wm.open_mainfile(filepath=str(copy))
from PM_VR.modules.pipeline import export, looks, probes, variants
from PM_VR.modules.pipeline.state import activate_state
project = bpy.context.scene.pm_vr_project
for field, folder in (('beauty_output_directory', 'Bakes'), ('lightmap_output_directory', 'Lightmaps'),
                      ('usdz_output_directory', 'USD'), ('glb_output_directory', 'GLB'),
                      ('probe_output_directory', 'probes'), ('probe_preview_directory', 'PMVR/ProbePreviews'),
                      ('log_output_directory', 'PMVR/Logs'), ('flattened_output_directory', 'PMVR/Flattened')):
    setattr(project, field, str(output / folder))
paths = list(bpy.utils.blend_paths(absolute=True, packed=False))
for unit in project.bake_units:
    for variant in unit.variants:
        paths.extend([variant.day_file, variant.evening_file, *(x.file for x in variant.look_results)])
for value in filter(None, paths):
    resolved = Path(bpy.path.abspath(value)).resolve()
    assert not any(resolved.is_relative_to(source / folder) for folder in ('Beauty_Bakes', 'PMVR_Flattened', 'UniPlace_Sync')), value
assert project.probe_collection and project.probe_collection.name == 'Probes'
report = {'copy': str(copy), 'original_opened': False, 'audited_paths': len(paths),
          'probes': [obj.name for obj in probes.probe_cameras(project, 'DAY')], 'exports': []}

def specs(layer):
    result = {}
    def visit(path):
        spec = layer.GetObjectAtPath(path)
        if spec:
            result[str(path)] = {key: spec.GetInfo(key) for key in spec.ListInfoKeys()}
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return result


def media(path):
    with zipfile.ZipFile(path) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                if not name.endswith(('.usd', '.usdc', '.usda'))}


for look in project.lighting_looks:
    activate_state(bpy.context, look.look_id)
    for layer in project.render_layers:
        if layer.layer_type != 'RUNTIME':
            continue
        status, path = export.export_semantic_layer(bpy.context, layer, 'USDZ')
        assert status == 'SUCCESS', (status, path)
        before_path = test_root / 'Sync_v2/USD' / Path(path).name
        before_stage, after_stage = Usd.Stage.Open(str(before_path)), Usd.Stage.Open(path)
        before_specs, after_specs = specs(before_stage.GetRootLayer()), specs(after_stage.GetRootLayer())
        probe_names = {variants.usd_name(obj.name) for obj in probes.probe_cameras(project, look.look_id)}
        roots = []
        for old in before_stage.Traverse():
            # Blender represents unsupported panoramic cameras as Xforms
            # even in v2; perspective cameras also have a Camera child.
            if old.GetName() not in probe_names:
                continue
            root = old
            roots.append(str(root.GetPath()))
            new = after_stage.GetPrimAtPath(root.GetPath())
            assert new and new.IsA(UsdGeom.Xform)
            old_matrix = UsdGeom.Xformable(root).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            new_matrix = UsdGeom.Xformable(new).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            assert all(abs(a-b) < 1e-4 for a,b in zip(old_matrix.ExtractTranslation(), new_matrix.ExtractTranslation()))
            assert all(abs(new_matrix[i][j] - (1 if i == j else 0)) < 1e-4 for i in range(3) for j in range(3))
            assert {a.GetName(): a.Get() for a in root.GetAttributes() if a.GetName().startswith('userProperties:')} == {
                    a.GetName(): a.Get() for a in new.GetAttributes() if a.GetName().startswith('userProperties:')}
        assert roots, 'No baseline probe cameras reviewed'
        for path_key in before_specs.keys() | after_specs.keys():
            if any(path_key == root or path_key.startswith(root + '/') or path_key.startswith(root + '.') for root in roots):
                continue
            assert before_specs.get(path_key) == after_specs.get(path_key), path_key
        assert media(before_path) == media(path), 'Runtime media changed'
        assert not any(p.IsA(UsdGeom.Camera) and p.GetParent().GetName() in probe_names for p in after_stage.Traverse())
        report['exports'].append({'look': look.look_id, 'file': path, 'intentional_probe_changes': roots,
                                  'other_authored_data_and_media_equal': True})

if args.render_one:
    bpy.context.scene.cycles.device = 'GPU' if args.gpu else 'CPU'
    if args.gpu:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for device in prefs.devices:
            device.use = device.type == 'OPTIX'
        assert any(device.use and device.type == 'OPTIX' for device in prefs.devices)
    project.probe_width, project.cycles_samples = '1024', 1
    camera = probes.probe_cameras(project, 'DAY')[0]
    session = probes.ProbeSession(bpy.context)
    try:
        session.begin()
        session.aim(camera, 'DAY')
        bpy.ops.render.render(write_still=False)
        path = probes.probe_path(project, camera, 'DAY')
        session.save(path)
        import OpenImageIO as oiio
        spec = oiio.ImageBuf(path).spec()
        assert (spec.width, spec.height) == (1024, 512)
        assert list(spec.channelnames) == ['R', 'G', 'B'] and spec.get_string_attribute('compression') == 'zip'
        assert all(str(spec.channelformat(i)) == 'half' for i in range(3))
        assert Path(probes.preview_path(project, path)).is_file()
        report['render'] = {'file': path, 'device': bpy.context.scene.cycles.device, 'samples': 1}
    finally:
        session.end()
output.mkdir(parents=True, exist_ok=True)
(output / 'probe_review.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('PMVR_REAL_PROBE_REVIEW_OK', flush=True)
