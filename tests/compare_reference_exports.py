"""Compare actual USD prims/UV/shaders and packaged media, ignoring ZIP dates.

Run with verification Python: script REFERENCE_USD CANDIDATE_USD REPORT_JSON.
No production files are opened or modified. New export descriptions are expected.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

from pxr import Sdf, Usd, UsdGeom


def compare_layers(before, after):
    """Compare authored data by spec path; sibling serialization order is irrelevant.

    Values retain their native USD equality, including array order, list operations,
    connections, prim/property order when explicitly authored, and all metadata.
    """
    def specs(layer):
        result = {}
        def visit(path):
            spec = layer.GetObjectAtPath(path)
            # Traverse also visits connection/relationship target paths. These
            # are values on their owning property, not independent specs.
            if spec is None:
                return
            result[str(path)] = {key: spec.GetInfo(key) for key in spec.ListInfoKeys()}
        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
        return result
    left, right = specs(before), specs(after)
    differences = []
    for path in sorted(left.keys() | right.keys()):
        if path not in left or path not in right:
            differences.append({'path': path, 'missing_from': 'v2' if path not in left else 'v3'})
            continue
        for key in sorted(left[path].keys() | right[path].keys()):
            if key not in left[path] or key not in right[path] or left[path][key] != right[path][key]:
                differences.append({'path': path, 'field': key,
                                    'v2': repr(left[path].get(key))[:2000],
                                    'v3': repr(right[path].get(key))[:2000]})
    return differences, len(left)


def compare_probe_representations(before_path, after_path, roots):
    """Allow only probe transform/schema changes, proving position/data intact."""
    before, after = Usd.Stage.Open(str(before_path)), Usd.Stage.Open(str(after_path))
    assert roots and before and after
    removed_cameras = []
    for root in roots:
        old, new = before.GetPrimAtPath(root), after.GetPrimAtPath(root)
        assert old and new and new.IsA(UsdGeom.Xform), root
        old_matrix = UsdGeom.Xformable(old).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        new_matrix = UsdGeom.Xformable(new).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        assert all(abs(a-b) < 1e-5 for a,b in zip(old_matrix.ExtractTranslation(), new_matrix.ExtractTranslation())), root
        assert all(abs(new_matrix[i][j] - (1 if i == j else 0)) < 1e-5 for i in range(3) for j in range(3)), root
        old_attributes = {a.GetName(): a.Get() for a in old.GetAttributes() if not a.GetName().startswith('xformOp')}
        new_attributes = {a.GetName(): a.Get() for a in new.GetAttributes() if not a.GetName().startswith('xformOp')}
        assert old_attributes == new_attributes, (root, 'Non-transform probe attributes changed')
        assert not any(p.IsA(UsdGeom.Camera) for p in Usd.PrimRange(new)), root
        removed_cameras += [str(p.GetPath()) for p in Usd.PrimRange(old) if p.IsA(UsdGeom.Camera)]
    differences, count = compare_layers(before.GetRootLayer(), after.GetRootLayer())
    allowed, unexpected = [], []
    for item in differences:
        path = item['path']
        transform = any(path.startswith(root + '.xformOp') for root in roots)
        removed = any(path == camera or path.startswith(camera + '.') or path.startswith(camera + '/')
                      for camera in removed_cameras)
        (allowed if transform or removed else unexpected).append(item)
    return unexpected, count, allowed


def self_test():
    left, right = Sdf.Layer.CreateAnonymous(), Sdf.Layer.CreateAnonymous()
    for layer, names in ((left, ('A', 'B')), (right, ('B', 'A'))):
        for name in names:
            prim = Sdf.CreatePrimInLayer(layer, '/' + name)
            attr = Sdf.AttributeSpec(prim, 'points', Sdf.ValueTypeNames.Point3fArray)
            attr.default = [(0, 0, 0), (1, 2, 3)]
    assert not compare_layers(left, right)[0], 'Sibling order must not change authored content'
    right.GetAttributeAtPath('/A.points').default = [(1, 2, 3), (0, 0, 0)]
    assert compare_layers(left, right)[0], 'Array order must be compared'
    right.GetAttributeAtPath('/A.points').default = [(0, 0, 0), (1, 2, 3)]
    right.GetPrimAtPath('/A').SetInfo('primOrder', ['B', 'A'])
    assert compare_layers(left, right)[0], 'Explicit authored ordering must be compared'
    with tempfile.TemporaryDirectory(prefix='pmvr_probe_comparison_') as folder:
        paths = [Path(folder) / (name + '.usda') for name in ('before', 'after')]
        for index, path in enumerate(paths):
            stage = Usd.Stage.CreateNew(str(path))
            probe = UsdGeom.Xform.Define(stage, '/Probe_A')
            probe.AddTranslateOp().Set((1, 2, 3))
            probe.AddRotateXYZOp().Set((12, 25, 30) if index == 0 else (0, 0, 0))
            probe.GetPrim().CreateAttribute('userProperties:volume', Sdf.ValueTypeNames.Double).Set(.7)
            stage.GetRootLayer().Save()
        assert not compare_probe_representations(*paths, ['/Probe_A'])[0]
        stage = Usd.Stage.Open(str(paths[1]))
        for field, value, restored in (('xformOp:translate', (9, 2, 3), (1, 2, 3)),
                                       ('userProperties:volume', .2, .7)):
            attr = stage.GetPrimAtPath('/Probe_A').GetAttribute(field)
            attr.Set(value)
            stage.GetRootLayer().Save()
            try:
                compare_probe_representations(*paths, ['/Probe_A'])
            except AssertionError:
                pass
            else:
                raise AssertionError('Changed probe position/property was accepted')
            attr.Set(restored)
            stage.GetRootLayer().Save()
        UsdGeom.Xform.Define(stage, '/Unrelated')
        stage.GetRootLayer().Save()
        assert compare_probe_representations(*paths, ['/Probe_A'])[0], 'Non-probe changes accepted'
    print('PMVR_REFERENCE_COMPARATOR_SELF_TEST_OK', flush=True)


self_test()
reference, candidate, report_path = map(Path, sys.argv[1:4])
probe_review = None
if len(sys.argv) > 4:
    assert sys.argv[4] == '--probe-review' and len(sys.argv) == 6
    probe_review = json.loads(Path(sys.argv[5]).read_text(encoding='utf-8'))
reference_files = {p.relative_to(reference).as_posix(): p for p in reference.rglob('*.usdz')}
candidate_files = {p.relative_to(candidate).as_posix(): p for p in candidate.rglob('*.usdz')}
report = {'reference': str(reference), 'candidate': str(candidate), 'files': [],
          'missing': sorted(reference_files.keys() - candidate_files.keys()),
          'unexpected': sorted(candidate_files.keys() - reference_files.keys()), 'manifest_equal': False}
for name in sorted(reference_files.keys() & candidate_files.keys()):
    before = Sdf.Layer.FindOrOpen(str(reference_files[name]))
    after = Sdf.Layer.FindOrOpen(str(candidate_files[name]))
    allowed = []
    reviewed = next((item for item in probe_review['exports'] if Path(item['file']).name == Path(name).name), None) if probe_review else None
    if reviewed:
        differences, spec_count, allowed = compare_probe_representations(
            reference_files[name], candidate_files[name], reviewed['intentional_probe_changes'])
    else:
        differences, spec_count = compare_layers(before, after)
    def media(path):
        with zipfile.ZipFile(path) as archive:
            return {member: hashlib.sha256(archive.read(member)).hexdigest()
                    for member in archive.namelist()
                    if not member.lower().endswith(('.usdc', '.usda', '.usd'))}
    before_media, after_media = media(reference_files[name]), media(candidate_files[name])
    entry = {'file': name, 'usd_equal': not differences and not allowed, 'authored_specs': spec_count,
             'media_equal': before_media == after_media, 'media_files': len(before_media)}
    if reviewed:
        entry['usd_equal_except_validated_probe_representations'] = not differences
        entry['validated_probe_count'] = len(reviewed['intentional_probe_changes'])
        entry['intentional_probe_differences'] = allowed
    if not entry['usd_equal']:
        entry['differences'] = differences
    if not entry['media_equal']:
        entry['changed_media'] = sorted(k for k in before_media.keys() | after_media.keys()
                                        if before_media.get(k) != after_media.get(k))
    report['files'].append(entry)
    print('REFERENCE_COMPARE', name, entry['usd_equal'], entry['media_equal'], flush=True)
manifest = 'Variants/materialVariants.json'
before_manifest, after_manifest = reference / manifest, candidate / manifest
report['manifest_equal'] = (before_manifest.exists() == after_manifest.exists()
                           and (not before_manifest.exists() or
                                json.loads(before_manifest.read_text(encoding='utf-8')) ==
                                json.loads(after_manifest.read_text(encoding='utf-8'))))
report['passed'] = (not report['missing'] and not report['unexpected'] and report['manifest_equal']
                    and all((item['usd_equal'] or item.get('usd_equal_except_validated_probe_representations', False))
                            and item['media_equal'] for item in report['files']))
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
assert report['passed'], 'Reference comparison failed: ' + str(report_path)
print('PMVR_REFERENCE_COMPARE_OK', len(report['files']), flush=True)
