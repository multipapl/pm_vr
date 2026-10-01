"""Pipeline log: console, an in-blend Text for quick reading, and a log file
next to the .blend that survives a crash."""

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

import bpy


LOG_TEXT_NAME = "PMVR Pipeline Log"
LOG_DIRECTORY = "PMVR_Logs"
MAX_LINES = 500


def log_file_path():
    blend = bpy.data.filepath
    if blend:
        project = getattr(bpy.context.scene, 'pm_vr_project', None)
        if project is not None:
            from .working_directory import migrate
            migrate(project)
            directory = bpy.path.abspath(project.log_output_directory)
        else:
            directory = os.path.join(os.path.dirname(blend), LOG_DIRECTORY)
        stem = os.path.splitext(os.path.basename(blend))[0]
    else:
        directory = os.path.join(tempfile.gettempdir(), LOG_DIRECTORY)
        stem = "untitled"
    return os.path.join(directory, f"{stem}_{datetime.now():%Y-%m-%d}.log")


def _append_file(lines):
    # Opened and closed per call so every line is on disk before Blender
    # continues; an overnight crash cannot lose what was already written.
    try:
        path = log_file_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"[PM VR][Log] Could not write log file: {exc}")


def _append_text(line):
    try:
        text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(LOG_TEXT_NAME)
        existing = text.as_string().splitlines()
        existing.append(line)
        if len(existing) > MAX_LINES:
            existing = existing[-MAX_LINES:]
        text.clear()
        text.write("\n".join(existing) + "\n")
    except Exception as exc:
        print(f"[PM VR][Log] Could not update Text datablock: {exc}")


def write(category, message, level="INFO", with_traceback=False):
    now = datetime.now()
    line = f"[{level}] [{category}] {message}"
    details = traceback.format_exc().rstrip() if with_traceback else ""
    print(f"[PM VR] [{now:%H:%M:%S}] {line}")
    if details:
        print(details)
    _append_file([f"{now:%Y-%m-%d %H:%M:%S} {line}"] + ([details] if details else []))
    _append_text(f"[{now:%H:%M:%S}] {line}" + (" (traceback in log file)" if details else ""))


def info(category, message):
    write(category, message, "INFO")


def warning(category, message):
    write(category, message, "WARNING")


def error(category, message, with_traceback=False):
    """Pass with_traceback=True inside an except block for unexpected errors."""
    write(category, message, "ERROR", with_traceback)


def duration(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def environment(context):
    """One line describing the setup a run used, for reproducing problems."""
    addon = sys.modules.get(__package__.split(".")[0])
    version = ".".join(str(part) for part in getattr(addon, "bl_info", {}).get("version", ()))
    scene = context.scene
    device = getattr(getattr(scene, "cycles", None), "device", "?")
    try:
        preferences = context.preferences.addons["cycles"].preferences
        backend = preferences.compute_device_type
        gpus = [item.name for item in preferences.devices if item.use and item.type != 'CPU']
    except (KeyError, AttributeError):
        backend, gpus = "?", []
    return (
        f"Blender {bpy.app.version_string}, PM VR {version or '?'} (commit {addon_commit()}), "
        f"file {bpy.data.filepath or 'unsaved'}, Cycles {device} "
        f"({backend}: {', '.join(gpus) or 'no GPU enabled'})"
    )


def addon_commit():
    """Identify the actual addon checkout, including a linked Git worktree.

    Installed release archives may not contain Git metadata. Logging must still
    work there, and a missing/slow Git executable must never block a bake.
    """
    root = Path(__file__).resolve().parents[2]
    if not (root / '.git').exists():
        return 'unknown'
    try:
        result = subprocess.run(
            ['git', '-C', str(root), 'rev-parse', '--short=12', 'HEAD'],
            capture_output=True, text=True, timeout=2,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value and all(c in '0123456789abcdef' for c in value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return 'unknown'
