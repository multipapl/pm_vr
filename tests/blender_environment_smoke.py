"""Verify the run log identifies the actual worktree and survives absent Git."""
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent))
import PM_VR
PM_VR.register()
from PM_VR.modules.pipeline import log

expected = subprocess.check_output(
    ['git', '-C', str(root), 'rev-parse', '--short=12', 'HEAD'], text=True).strip()
message = log.environment(bpy.context)
assert 'commit ' + expected in message, message
version = '.'.join(map(str, PM_VR.bl_info['version']))
assert 'PM VR ' + version in message, message
log.info('EnvironmentTest', message)
assert message in Path(log.log_file_path()).read_text(encoding='utf-8')
with patch.object(log.subprocess, 'run', side_effect=FileNotFoundError('no Git')):
    assert 'commit unknown' in log.environment(bpy.context)
with patch.object(log.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 2)):
    assert 'commit unknown' in log.environment(bpy.context)
print('PMVR_ENVIRONMENT_SMOKE_OK')
