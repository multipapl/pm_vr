"""Compare actual USD prims/UV/shaders and packaged media, ignoring ZIP dates.

Run with verification Python: script REFERENCE_USD CANDIDATE_USD REPORT_JSON.
No production files are opened or modified. New export descriptions are expected.
"""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from pxr import Sdf


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
    print('PMVR_REFERENCE_COMPARATOR_SELF_TEST_OK', flush=True)


self_test()
reference, candidate, report_path = map(Path, sys.argv[1:4])
reference_files = {p.relative_to(reference).as_posix(): p for p in reference.rglob('*.usdz')}
candidate_files = {p.relative_to(candidate).as_posix(): p for p in candidate.rglob('*.usdz')}
report = {'reference': str(reference), 'candidate': str(candidate), 'files': [],
          'missing': sorted(reference_files.keys() - candidate_files.keys()),
          'unexpected': sorted(candidate_files.keys() - reference_files.keys()), 'manifest_equal': False}
for name in sorted(reference_files.keys() & candidate_files.keys()):
    before = Sdf.Layer.FindOrOpen(str(reference_files[name]))
    after = Sdf.Layer.FindOrOpen(str(candidate_files[name]))
    differences, spec_count = compare_layers(before, after)
    def media(path):
        with zipfile.ZipFile(path) as archive:
            return {member: hashlib.sha256(archive.read(member)).hexdigest()
                    for member in archive.namelist()
                    if not member.lower().endswith(('.usdc', '.usda', '.usd'))}
    before_media, after_media = media(reference_files[name]), media(candidate_files[name])
    entry = {'file': name, 'usd_equal': not differences, 'authored_specs': spec_count,
             'media_equal': before_media == after_media, 'media_files': len(before_media)}
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
                    and all(item['usd_equal'] and item['media_equal'] for item in report['files']))
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
assert report['passed'], 'Reference comparison failed: ' + str(report_path)
print('PMVR_REFERENCE_COMPARE_OK', len(report['files']), flush=True)
