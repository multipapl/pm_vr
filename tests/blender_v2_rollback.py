"""Open only an isolated v3 fixture with actual v2 code, verify and export it."""
import argparse
from pathlib import Path
import json
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--fixture', required=True)
parser.add_argument('--addon-parent', required=True)
parser.add_argument('--test-root', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
root, fixture = Path(args.test_root).resolve(), Path(args.fixture).resolve()
assert fixture.is_relative_to(root)
sys.path.insert(0, args.addon_parent)
import PM_VR
PM_VR.register()
assert PM_VR.bl_info['version'] == (2, 0, 0)
bpy.ops.wm.open_mainfile(filepath=str(fixture / 'Looks.blend'))
from PM_VR.modules.pipeline import export, generated
from PM_VR.modules.pipeline.constants import SCHEMA_VERSION
from PM_VR.modules.pipeline.state import activate_state
assert SCHEMA_VERSION == 1
expected = json.loads((fixture / 'rollback_expected.json').read_text())
project = bpy.context.scene.pm_vr_project
unit = next(x for x in project.bake_units if x.unit_id == expected['unit_id'])
for field, value in expected['legacy'].items():
    assert getattr(unit, field) == value, field
for field, value in expected['variants'].items():
    assert getattr(unit.variants[0], field) == value, field
obj = generated.find_generated(unit.unit_id, expected['source_id'])
assert obj and obj.data.name == expected['mesh_name']
project.usdz_output_directory = str(fixture / 'Rollback_v2')
layer = next(x for x in project.render_layers if x.layer_id == unit.render_layer_id)
for state in ('DAY', 'EVENING'):
    activate_state(bpy.context, state)
    assert export.export_semantic_layer(bpy.context, layer, 'USDZ')[0] == 'SUCCESS'
print('PMVR_V2_ROLLBACK_OK')
