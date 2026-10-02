"""Isolate a plain-file production copy, then optionally export the v2 baseline.

Run in factory-startup Blender with --python-exit-code 1 and arguments after --:
  --addon-parent DIR --source-root DIR --copy BLEND --test-root DIR [--export]
The production blend is NEVER opened. This tool refuses any input outside test-root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--addon-parent', required=True)
parser.add_argument('--source-root', required=True)
parser.add_argument('--copy', required=True)
parser.add_argument('--test-root', required=True)
parser.add_argument('--export', action='store_true')
parser.add_argument('--sync-name', default='Sync_v2')
parser.add_argument('--isolated-name', default='Uniplace_v2_reference_isolated.blend')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
source = Path(args.source_root).resolve()
root = Path(args.test_root).resolve()
sync = (root / args.sync_name).resolve()
isolated = (root / args.isolated_name).resolve()
assert sync.is_relative_to(root) and isolated.is_relative_to(root)
copy = Path(args.copy).resolve()
assert copy.is_relative_to(root) and not copy.is_relative_to(source), copy
sys.path.insert(0, args.addon_parent)
import PM_VR
PM_VR.register()
assert bpy.ops.wm.open_mainfile(filepath=str(copy)) == {'FINISHED'}

changed = []
def remap(value):
    if not value or value.startswith('<'):
        return value
    # Relative paths in the byte-for-byte copy are relative to ORIGINAL root.
    path = Path(bpy.path.abspath(value, start=str(source))).resolve()
    for folder, destination in (('Beauty_Bakes', root / 'Beauty_Bakes'),
                                ('PMVR_Flattened', root / 'PMVR_Flattened'),
                                ('UniPlace_Sync', sync)):
        old = source / folder
        if path.is_relative_to(old):
            result = destination / path.relative_to(old)
            # Some authored images may live in Sync. Copy referenced inputs only.
            if folder == 'UniPlace_Sync' and path.is_file() and not result.exists():
                import shutil
                result.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, result)
            return str(result)
    return str(path)

def rewrite(owner, field):
    value = getattr(owner, field)
    mapped = remap(value)
    if value != mapped:
        setattr(owner, field, mapped)
        changed.append((getattr(owner, 'name', type(owner).__name__), field, value, mapped))

for collection in ('libraries', 'images', 'movieclips', 'sounds', 'fonts', 'cache_files'):
    for item in getattr(bpy.data, collection):
        rewrite(item, 'filepath')

# bpy.utils.blend_paths omits external shader node files (notably IES).
# Include embedded light/world/material trees and shared nested node groups.
trees = {tree.as_pointer(): tree for tree in bpy.data.node_groups}
for collection in ('materials', 'worlds', 'lights', 'scenes'):
    for item in getattr(bpy.data, collection):
        tree = getattr(item, 'node_tree', None)
        if tree:
            trees[tree.as_pointer()] = tree
node_paths = []
for tree in trees.values():
    for node in tree.nodes:
        if hasattr(node, 'filepath') and node.filepath:
            rewrite(node, 'filepath')
            node_paths.append(node)
for scene in bpy.data.scenes:
    project = scene.pm_vr_project
    for field, folder in (('beauty_output_directory', 'Beauty_Bakes'),
                          ('lightmap_output_directory', 'Lightmaps'),
                          ('flattened_output_directory', 'PMVR_Flattened'),
                          ('log_output_directory', 'PMVR_Logs'),
                          ('probe_preview_directory', 'PMVR/ProbePreviews'),
                          ('usdz_output_directory', args.sync_name + '/USD'),
                          ('glb_output_directory', args.sync_name + '/GLB'),
                          ('probe_output_directory', args.sync_name + '/probes')):
        destination = root / folder
        destination.mkdir(parents=True, exist_ok=True)
        setattr(project, field, str(destination) + os.sep)
    for unit in project.bake_units:
        for variant in unit.variants:
            for field in ('day_file', 'evening_file'):
                rewrite(variant, field)
            for result in getattr(variant, 'look_results', ()):
                rewrite(result, 'file')
    scene.render.filepath = str(root / 'Renders' / scene.name)
    compositor = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
    if compositor:
        for node in compositor.nodes:
            if node.type == 'OUTPUT_FILE':
                node.base_path = str(root / 'Renders' / scene.name)
for obj in bpy.data.objects:
    for modifier in obj.modifiers:
        if hasattr(modifier, 'filepath'):
            rewrite(modifier, 'filepath')
        domain = getattr(modifier, 'domain_settings', None)
        if domain and hasattr(domain, 'cache_directory'):
            rewrite(domain, 'cache_directory')

def audit():
    paths = list(bpy.utils.blend_paths(absolute=True, packed=False))
    paths.extend(node.filepath for node in node_paths)
    for node in node_paths:
        if node.type == 'TEX_IES' and node.mode == 'EXTERNAL':
            assert Path(bpy.path.abspath(node.filepath)).is_file(), ('Missing IES', node.filepath)
    for scene in bpy.data.scenes:
        project = scene.pm_vr_project
        for field in ('beauty_output_directory', 'lightmap_output_directory',
                      'usdz_output_directory', 'glb_output_directory', 'probe_output_directory',
                      'flattened_output_directory', 'log_output_directory', 'probe_preview_directory'):
            if not hasattr(project, field):
                continue
            path = Path(bpy.path.abspath(getattr(project, field))).resolve()
            assert path.is_relative_to(root), (field, path)
        for unit in project.bake_units:
            for variant in unit.variants:
                paths += [p for p in (variant.day_file, variant.evening_file) if p]
                paths += [result.file for result in getattr(variant, 'look_results', ()) if result.file]
    for value in paths:
        path = Path(bpy.path.abspath(value)).resolve()
        assert not any(path.is_relative_to(source / folder)
                       for folder in ('Beauty_Bakes', 'PMVR_Flattened', 'UniPlace_Sync')), value
    return len(paths)

path_count = audit()
assert bpy.ops.wm.save_as_mainfile(filepath=str(isolated), relative_remap=False) == {'FINISHED'}
assert audit() == path_count
report = {'source_opened': False, 'isolated_blend': str(isolated),
          'addon_version': list(PM_VR.bl_info['version']), 'audited_paths': path_count,
          'remapped_paths': changed, 'export': []}
(root / (args.sync_name + '_isolation.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('PMVR_REFERENCE_ISOLATION_OK', path_count, flush=True)

if args.export:
    from PM_VR.modules.pipeline import export, state
    project = bpy.context.scene.pm_vr_project
    for look in ('DAY', 'EVENING'):
        state.activate_state(bpy.context, look)
        textures = export.ExportTextures()
        try:
            for layer in project.render_layers:
                # Baseline contains ALL configured layers, irrespective of checked rows.
                try:
                    status, message = export.export_semantic_layer(bpy.context, layer, 'USDZ', textures)
                    report['export'].append({'look': look, 'layer': layer.display_name,
                                             'status': status, 'message': message})
                    print('REFERENCE_EXPORT', look, layer.display_name, status, flush=True)
                except Exception as exc:
                    report['export'].append({'look': look, 'layer': layer.display_name,
                                             'status': 'FAILED', 'message': str(exc)})
                    print('REFERENCE_EXPORT_FAILED', look, layer.display_name, str(exc), flush=True)
        finally:
            textures.cleanup()
        export.write_variant_manifest(project)
    files = {}
    for path in sorted(sync.rglob('*')):
        if path.is_file() and (path.suffix.lower() in ('.usdz', '.json')):
            files[str(path.relative_to(sync))] = {
                'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    report['files'] = files
    (root / (args.sync_name + '_export.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    failures = [item for item in report['export'] if item['status'] == 'FAILED']
    assert not failures, failures
    assert files, 'No reference files written'
    print('PMVR_REFERENCE_EXPORT_OK', len(files), flush=True)
