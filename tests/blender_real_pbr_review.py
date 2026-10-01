"""Bake one PBR unit in an audited scene copy into two private output folders."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import bpy
import numpy as np
from pxr import Sdf, Usd

parser = argparse.ArgumentParser()
parser.add_argument('--copy', required=True)
parser.add_argument('--test-root', required=True)
parser.add_argument('--source-root', required=True)
parser.add_argument('--report-root', required=True)
parser.add_argument('--unit')
parser.add_argument('--gpu', action='store_true')
parser.add_argument('--samples', type=int, default=8)
parser.add_argument('--denoise', choices=('OFF', 'GUIDED', 'IMAGE'), default='OFF')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
copy, root, source, output = (Path(v).resolve() for v in (args.copy, args.test_root, args.source_root, args.report_root))
assert copy.is_relative_to(root) and output.is_relative_to(root) and not copy.is_relative_to(source)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
PM_VR.register()
bpy.ops.wm.open_mainfile(filepath=str(copy))
from PM_VR.modules.pipeline import bake, export, generated
from PM_VR.modules.pipeline.identity import unit_members, find_layer
from PM_VR.modules.pipeline.state import activate_state
from PM_VR.modules.pipeline.scenarios import ScenarioSession
scene = bpy.context.scene
project = scene.pm_vr_project
for field, folder in (('beauty_output_directory', 'Bakes'), ('lightmap_output_directory', 'Lightmaps'),
                      ('usdz_output_directory', 'USD'), ('glb_output_directory', 'GLB'),
                      ('probe_output_directory', 'probes'), ('probe_preview_directory', 'PMVR/ProbePreviews'),
                      ('log_output_directory', 'PMVR/Logs'), ('flattened_output_directory', 'PMVR/Flattened')):
    setattr(project, field, str(output / folder))
paths = list(bpy.utils.blend_paths(absolute=True, packed=False))
trees = {tree.as_pointer(): tree for tree in bpy.data.node_groups}
for collection in ('materials', 'worlds', 'lights', 'scenes'):
    for item in getattr(bpy.data, collection):
        tree = getattr(item, 'node_tree', None)
        if tree:
            trees[tree.as_pointer()] = tree
for tree in trees.values():
    for node in tree.nodes:
        if hasattr(node, 'filepath') and node.filepath:
            assert not node.filepath.startswith('//'), node.filepath
            paths.append(node.filepath)
            if node.type == 'TEX_IES' and node.mode == 'EXTERNAL':
                assert Path(node.filepath).is_file(), node.filepath
for unit in project.bake_units:
    for variant in unit.variants:
        paths.extend([variant.day_file, variant.evening_file, *(x.file for x in variant.look_results)])
for value in filter(None, paths):
    resolved = Path(bpy.path.abspath(value)).resolve()
    assert not any(resolved.is_relative_to(source / folder) for folder in ('Beauty_Bakes', 'PMVR_Flattened', 'UniPlace_Sync')), value
candidates = [unit for unit in project.bake_units if find_layer(project, unit.render_layer_id).layer_type == 'PBR']
if not args.unit:
    print(json.dumps([{'unit': unit.display_name, 'objects': [x.name for x in unit_members(unit.unit_id)]}
                      for unit in candidates], ensure_ascii=False), flush=True)
else:
    unit = next(unit for unit in candidates if unit.display_name == args.unit)
    members = unit_members(unit.unit_id)
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('pbr_checks', Path(__file__).with_name('blender_pbr_diffuse_smoke.py'))
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    materials = {material.name: checks.material_state(material) for obj in members for material in obj.data.materials if material}
    original_files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (root / 'Beauty_Bakes').rglob('*.png') if unit.display_name in path.name}
    other_records = {x.unit_id: (x.day_signature, x.evening_signature, x.day_beauty_image, x.evening_beauty_image)
                     for x in project.bake_units if x.unit_id != unit.unit_id}
    scene.cycles.device = 'GPU' if args.gpu else 'CPU'
    if args.gpu:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for device in prefs.devices:
            device.use = device.type == 'OPTIX'
        assert any(device.use and device.type == 'OPTIX' for device in prefs.devices)
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.seed = 29
    project.cycles_samples, project.bake_resolution = args.samples, '512'
    project.beauty_denoise, project.fill_empty_uv = args.denoise, False
    # Neither branch overwrites the snapshot's valid reference atlases.
    activate_state(bpy.context, 'DAY')
    products = []
    for label, diffuse in (('Combined', False), ('Diffuse', True)):
        project.pbr_diffuse_only = diffuse
        project.beauty_output_directory = str(output / label)
        scenario = ScenarioSession(bpy.context)
        scenario.apply(bpy.context, unit)
        runtime = bake.BeautyBakeRuntime(bpy.context, unit)
        try:
            assert runtime.prepare() == 'READY'
            for index in range(len(runtime.receivers)):
                runtime.select_receiver(index)
                assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
            pixels = np.array(runtime.image.pixels[:], dtype=np.float32).reshape(-1, 4)
            runtime.finish()
            image = bpy.data.images[unit.day_beauty_image]
            objects = [generated.find_generated(unit.unit_id, obj.pm_vr_pipeline.source_id) for obj in members]
            assert all(objects)
            for obj, gen in zip(members, objects):
                for original, result in zip(obj.data.materials, gen.data.materials):
                    assert checks.material_state(original) == checks.material_state(result)
            package = output / label / 'PBR.usdz'
            textures = export.ExportTextures()
            try:
                export._write_usdz(bpy.context, project, runtime.layer, objects, str(package), textures)
            finally:
                textures.cleanup()
            products.append({'mode': label, 'pixels': pixels, 'package': str(package),
                             'atlas': bpy.path.abspath(image.filepath), 'signature': runtime.signature,
                             'resolution': runtime.bake_size,
                             'object_pointers': [obj.as_pointer() for obj in objects]})
        finally:
            runtime.cleanup(keep_image=runtime.finished)
            scenario.restore()
    before, after = products
    assert before['signature'] == after['signature']
    assert before['object_pointers'] == after['object_pointers']
    assert checks.authored_specs(before['package']) == checks.authored_specs(after['package'])
    def media(path):
        with zipfile.ZipFile(path) as archive:
            return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                    if not name.endswith(('.usd', '.usda', '.usdc'))}
    old, new = media(before['package']), media(after['package'])
    assert old.keys() == new.keys()
    changed = [name for name in old if old[name] != new[name]]
    assert len(changed) == 1 and Path(changed[0]).name == Path(before['atlas']).name, changed
    mask = np.max(before['pixels'][:, :3], axis=1) > .005
    delta = np.mean(np.abs(before['pixels'][mask, :3] - after['pixels'][mask, :3]))
    assert delta > .0001, delta
    assert materials == {material.name: checks.material_state(material) for obj in members for material in obj.data.materials if material}
    assert other_records == {x.unit_id: (x.day_signature, x.evening_signature, x.day_beauty_image, x.evening_beauty_image)
                             for x in project.bake_units if x.unit_id != unit.unit_id}
    assert original_files == {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in original_files}
    for item in products:
        del item['pixels']
    report = {'copy': str(copy), 'original_opened': False, 'audited_paths': len(paths),
              'unit': unit.display_name, 'members': [obj.name for obj in members], 'samples': args.samples,
              'denoise': args.denoise,
              'resolution': before['resolution'], 'device': scene.cycles.device, 'products': products,
              'mean_linear_rgb_delta': float(delta), 'changed_packaged_media': changed,
              'authored_usd_equal': True, 'original_pbr_channels_preserved': True,
              'other_units_unchanged': True, 'reference_atlases_unchanged': True}
    (output / 'pbr_review.json').write_text(json.dumps(report, indent=2))
    print('PMVR_REAL_PBR_REVIEW_OK', flush=True)
