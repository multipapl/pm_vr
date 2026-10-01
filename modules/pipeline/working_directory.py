"""Project-local working files, with explicit preservation of legacy paths."""
from pathlib import Path

import bpy

FORMAT_VERSION = 1
NEW_DEFAULTS = {
    'beauty_output_directory': '//PMVR/Bakes/',
    'flattened_output_directory': '//PMVR/Flattened/',
    'log_output_directory': '//PMVR/Logs/',
    'probe_preview_directory': '//PMVR/ProbePreviews/',
}
LEGACY_DEFAULTS = {
    'beauty_output_directory': '//Beauty_Bakes/',
    'flattened_output_directory': '//PMVR_Flattened/',
    'log_output_directory': '//PMVR_Logs/',
}


def migrate(project):
    """Pin defaults before new RNA defaults can redirect an existing project.

    This only records paths. It never creates, moves or copies legacy files.
    """
    if project.initialized and project.working_directory_version == 0:
        for field, value in LEGACY_DEFAULTS.items():
            if not project.is_property_set(field):
                setattr(project, field, value)
        project.working_directory_version = FORMAT_VERSION
        project.legacy_working_directory = True


def ensure_new_folders(project):
    migrate(project)
    if not bpy.data.filepath or not project.initialized:
        return
    if project.legacy_working_directory:
        return
    root = Path(bpy.data.filepath).parent / 'PMVR'
    for name in ('Bakes', 'Flattened', 'Logs', 'ProbePreviews'):
        (root / name).mkdir(parents=True, exist_ok=True)


def initialize(project):
    if project.initialized:
        migrate(project)
        return
    for field, value in NEW_DEFAULTS.items():
        if not project.is_property_set(field):
            setattr(project, field, value)
    project.working_directory_version = FORMAT_VERSION


def migrate_all():
    for scene in bpy.data.scenes:
        project = getattr(scene, 'pm_vr_project', None)
        if project is not None:
            migrate(project)


def flattened_directory():
    project = getattr(bpy.context.scene, 'pm_vr_project', None)
    if project is None:
        return NEW_DEFAULTS['flattened_output_directory']
    migrate(project)
    return project.flattened_output_directory
