"""Review an isolated real project across v3 saves and actual v2 rollback.

Run with --python-exit-code 1. Arguments after -- select an explicit add-on
parent, an already audited copy, a private report folder, and optionally a
baseline manifest, save/reopen cycles and full Day/Evening USDZ export.
No bake unit is rebaked; this checks preservation of an existing project.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument('--addon-parent', required=True)
parser.add_argument('--copy', required=True)
parser.add_argument('--test-root', required=True)
parser.add_argument('--source-root', required=True)
parser.add_argument('--report-root', required=True)
parser.add_argument('--expected')
parser.add_argument('--save-cycles', type=int, default=0)
parser.add_argument('--export', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, copy = Path(args.test_root).resolve(), Path(args.copy).resolve()
source, output = Path(args.source_root).resolve(), Path(args.report_root).resolve()
assert copy.is_relative_to(root) and output.is_relative_to(root)
assert not copy.is_relative_to(source) and copy.is_file()
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, args.addon_parent)
import PM_VR
assert Path(PM_VR.__file__).resolve().parent.parent == Path(args.addon_parent).resolve()
if not hasattr(bpy.types.Scene, 'pm_vr_project'):
    PM_VR.register()
from PM_VR.modules.pipeline import export, generated, state
from PM_VR.modules.pipeline.constants import SCHEMA_VERSION, TAG_GENERATED
assert SCHEMA_VERSION == 1


def audit_paths():
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
                paths.append(bpy.path.abspath(node.filepath))
                if node.type == 'TEX_IES' and node.mode == 'EXTERNAL':
                    assert Path(bpy.path.abspath(node.filepath)).is_file(), node.filepath
    for scene in bpy.data.scenes:
        project = scene.pm_vr_project
        for field in ('beauty_output_directory', 'lightmap_output_directory',
                      'usdz_output_directory', 'glb_output_directory', 'probe_output_directory',
                      'flattened_output_directory', 'log_output_directory', 'probe_preview_directory'):
            if hasattr(project, field):
                path = Path(bpy.path.abspath(getattr(project, field))).resolve()
                assert path.is_relative_to(root), (field, path)
        for unit in project.bake_units:
            for variant in unit.variants:
                paths += [value for value in (variant.day_file, variant.evening_file) if value]
                paths += [item.file for item in getattr(variant, 'look_results', ()) if item.file]
    for value in paths:
        path = Path(bpy.path.abspath(value)).resolve()
        assert not any(path.is_relative_to(source / folder) for folder in (
            'Beauty_Bakes', 'PMVR_Flattened', 'UniPlace_Sync', 'PMVR_Logs')), value
    return len(paths)


def value(item):
    if isinstance(item, bpy.types.ID):
        return {'type': item.bl_rna.identifier, 'name': item.name_full}
    if item is None or isinstance(item, (str, bool, int, float)):
        return item
    if isinstance(item, set):
        return sorted(item)
    return [value(x) for x in item]


def schema(rna):
    result = {}
    for prop in rna.properties:
        key = prop.identifier
        if key == 'rna_type' or (prop.is_readonly and prop.type != 'COLLECTION') or getattr(prop, 'is_skip_save', False):
            continue
        # Output folders are deliberately isolated/redirected for each run.
        if key.endswith('_directory') or key == 'batch_selected':
            continue
        result[key] = schema(prop.fixed_type) if prop.type == 'COLLECTION' else None
    return result


def properties(item, fields):
    return {key: [properties(child, nested) for child in getattr(item, key)]
            if nested is not None else path_value(item, key)
            for key, nested in fields.items()}


def path_value(owner, key):
    item = getattr(owner, key)
    prop = owner.bl_rna.properties[key]
    if (prop.type == 'STRING' and item and
            (prop.subtype in {'FILE_PATH', 'DIR_PATH'} or key in {'file', 'day_file', 'evening_file'})):
        datablock = getattr(owner, 'id_data', owner)
        return str(Path(bpy.path.abspath(item, library=getattr(datablock, 'library', None))).resolve())
    return value(item)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def array(collection, field, width=1, dtype=np.float32):
    data = np.empty(len(collection) * width, dtype=dtype)
    collection.foreach_get(field, data)
    return hashlib.sha256(data.tobytes()).hexdigest()


def mesh_data(mesh):
    return {'vertices': array(mesh.vertices, 'co', 3),
            'edges': array(mesh.edges, 'vertices', 2, np.int32),
            'loops': array(mesh.loops, 'vertex_index', dtype=np.int32),
            'polygon_starts': array(mesh.polygons, 'loop_start', dtype=np.int32),
            'polygon_sizes': array(mesh.polygons, 'loop_total', dtype=np.int32),
            'material_indices': array(mesh.polygons, 'material_index', dtype=np.int32),
            'smooth': array(mesh.polygons, 'use_smooth', dtype=np.bool_),
            'uvs': {uv.name: array(uv.uv, 'vector', 2) for uv in mesh.uv_layers}}


def node_tree(tree):
    if not tree:
        return None
    nodes = {}
    ignored = {'location', 'width', 'height', 'dimensions', 'select', 'hide',
               'show_options', 'show_preview', 'show_texture', 'parent', 'color',
               'use_custom_color', 'label', 'warning_propagation'}
    for node in tree.nodes:
        fields = {}
        for prop in node.bl_rna.properties:
            if prop.is_readonly or prop.identifier == 'rna_type' or prop.identifier in ignored:
                continue
            if prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM', 'POINTER'}:
                continue
            item = getattr(node, prop.identifier)
            if prop.type == 'POINTER' and item is not None and not isinstance(item, bpy.types.ID):
                continue
            fields[prop.identifier] = path_value(node, prop.identifier)
        nodes[node.name] = {'type': node.bl_idname, 'properties': fields,
                            'inputs': {f'{i}:{socket.identifier}': value(socket.default_value)
                                       for i, socket in enumerate(node.inputs)
                                       if hasattr(socket, 'default_value')}}
    links = sorted((link.from_node.name, link.from_socket.identifier,
                    link.to_node.name, link.to_socket.identifier) for link in tree.links)
    return {'nodes': nodes, 'links': links}


def fingerprint(fields):
    objects = {}
    for obj in bpy.data.objects:
        objects[obj.name] = {
            'type': obj.type, 'data': value(obj.data), 'parent': value(obj.parent),
            'parent_type': obj.parent_type, 'parent_bone': obj.parent_bone,
            'matrix_basis': value(obj.matrix_basis), 'matrix_parent_inverse': value(obj.matrix_parent_inverse),
            'hide_render': obj.hide_render, 'collections': sorted(x.name for x in obj.users_collection),
            'pipeline': properties(obj.pm_vr_pipeline, fields['object']),
            'tags': {key: value(obj[key]) for key in obj.keys() if key.startswith('pmvr_')},
            'slots': [[slot.link, value(slot.material)] for slot in obj.material_slots]}
    return {
        'projects': {scene.name: properties(scene.pm_vr_project, fields['project']) for scene in bpy.data.scenes},
        'objects': objects,
        'meshes': {mesh.name: mesh_data(mesh) for mesh in bpy.data.meshes},
        'materials': {mat.name: {'fake_user': mat.use_fake_user, 'tree': digest(node_tree(mat.node_tree)),
                                 'diffuse_color': value(mat.diffuse_color)} for mat in bpy.data.materials},
        'worlds': {world.name: digest(node_tree(world.node_tree)) for world in bpy.data.worlds},
        'lights': {light.name: digest(node_tree(light.node_tree)) for light in bpy.data.lights},
        'node_groups': {tree.name: digest(node_tree(tree)) for tree in bpy.data.node_groups},
        'images': {image.name: {'path': str(Path(bpy.path.abspath(image.filepath)).resolve()) if image.filepath else '',
                                'source': image.source, 'colorspace': image.colorspace_settings.name,
                                'fake_user': image.use_fake_user} for image in bpy.data.images
                   if image.name != 'Render Result'}}


def check_bindings():
    project = bpy.context.scene.pm_vr_project
    original_state = project.active_lighting_state
    saved = generated.snapshot_generated_bindings()
    report = {}
    try:
        for look in ('DAY', 'EVENING'):
            state.activate_state(bpy.context, look)
            report[look] = {}
            for layer in project.render_layers:
                objects = export.resolve_layer_objects(bpy.context, layer)
                report[look][layer.display_name] = len(objects)
    finally:
        state.activate_state(bpy.context, original_state)
        generated.restore_generated_bindings(saved)
    return report


def differences(expected, actual, prefix=''):
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            path = prefix + '/' + str(key)
            if key not in expected or key not in actual:
                result.append({'path': path, 'missing': 'baseline' if key not in expected else 'candidate'})
            else:
                result.extend(differences(expected[key], actual[key], path))
        return result
    if expected != actual:
        return [{'path': prefix, 'before': repr(expected)[:2000], 'after': repr(actual)[:2000]}]
    return []


bpy.ops.wm.open_mainfile(filepath=str(copy))
assert audit_paths()
expected = json.loads(Path(args.expected).read_text(encoding='utf-8')) if args.expected else None
fields = expected['schema'] if expected else {
    'project': schema(bpy.context.scene.pm_vr_project.bl_rna),
    'object': schema(bpy.data.objects[0].pm_vr_pipeline.bl_rna)}
report = {'copy': str(copy), 'original_opened': False, 'addon': PM_VR.__file__,
          'version': list(PM_VR.bl_info['version']), 'schema_version': SCHEMA_VERSION,
          'private_config': os.environ.get('BLENDER_USER_CONFIG'),
          'gui': not bpy.app.background, 'enabled_addons': len(bpy.context.preferences.addons),
          'schema': fields, 'cycles': [], 'export': []}
project = bpy.context.scene.pm_vr_project
if hasattr(project, 'lighting_looks'):
    from PM_VR.modules.pipeline import platform
    runtime_ids = {layer.layer_id for layer in project.render_layers if layer.layer_type == 'RUNTIME'}
    runtime_objects = [obj for obj in bpy.data.objects if obj.pm_vr_pipeline.is_registered_source
                       and obj.pm_vr_pipeline.render_layer_id in runtime_ids]
    report['v3_configuration'] = {
        'pbr_diffuse_only': project.pbr_diffuse_only,
        'looks': [{'id': item.look_id, 'name': item.display_name, 'default': item.is_default}
                  for item in project.lighting_looks],
        'runtime_warnings': platform.runtime_warnings(runtime_objects),
        'start_position_present': any(obj.name == 'StartPosition' and obj.type == 'EMPTY'
                                      for obj in runtime_objects)}
baseline = expected['fingerprint'] if expected else fingerprint(fields)
report['fingerprint'] = baseline

for cycle in range(args.save_cycles + 1):
    bindings = check_bindings()
    current = fingerprint(fields)
    changed = differences(baseline, current)
    report['cycles'].append({'cycle': cycle, 'audited_paths': audit_paths(),
                             'bindings': bindings, 'differences': changed})
    (output / 'transition_review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not changed, changed[:15]
    print('PMVR_TRANSITION_CYCLE_OK', cycle, len(current['objects']), len(current['meshes']), flush=True)
    if cycle < args.save_cycles:
        saved_blend = output / f'UniPlace_v3_saved_{cycle + 1}.blend'
        assert bpy.ops.wm.save_as_mainfile(filepath=str(saved_blend), relative_remap=True) == {'FINISHED'}
        bpy.ops.wm.open_mainfile(filepath=str(saved_blend))

if args.export:
    project = bpy.context.scene.pm_vr_project
    project.usdz_output_directory = str(output / 'USD')
    original_state = project.active_lighting_state
    saved = generated.snapshot_generated_bindings()
    try:
        for look in ('DAY', 'EVENING'):
            state.activate_state(bpy.context, look)
            textures = export.ExportTextures()
            try:
                for layer in project.render_layers:
                    status, message = export.export_semantic_layer(bpy.context, layer, 'USDZ', textures)
                    assert status in {'SUCCESS', 'SKIPPED'}, (look, layer.display_name, status, message)
                    report['export'].append({'look': look, 'layer': layer.display_name, 'status': status})
                    print('PMVR_TRANSITION_EXPORT', look, layer.display_name, status, flush=True)
            finally:
                textures.cleanup()
            export.write_variant_manifest(project)
    finally:
        state.activate_state(bpy.context, original_state)
        generated.restore_generated_bindings(saved)
    changed = differences(baseline, fingerprint(fields))
    report['after_export_differences'] = changed
    assert not changed, changed[:15]
(output / 'transition_review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('PMVR_REAL_TRANSITION_REVIEW_OK', flush=True)
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
