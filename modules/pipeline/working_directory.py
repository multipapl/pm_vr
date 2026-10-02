"""Project-local working files, with explicit preservation of legacy paths."""
from pathlib import Path

import bpy

FORMAT_VERSION = 2
NEW_DEFAULTS = {
    'beauty_output_directory': '//PMVR/Bakes/',
    'flattened_output_directory': '//PMVR/Flattened/',
    'log_output_directory': '//PMVR/Logs/',
    'probe_preview_directory': '//PMVR/ProbePreviews/',
    'lightmap_output_directory': '//PMVR/Lightmaps/',
    'external_texture_directory': '//PMVR/Textures/',
}
LEGACY_DEFAULTS = {
    'beauty_output_directory': '//Beauty_Bakes/',
    'flattened_output_directory': '//PMVR_Flattened/',
    'log_output_directory': '//PMVR_Logs/',
    'lightmap_output_directory': '//Lightmaps/',
    'external_texture_directory': '//PM_Selected_Textures/',
}


def migrate(project):
    """Pin defaults before new RNA defaults can redirect an existing project.

    This only records paths. It never creates, moves or copies legacy files.
    """
    if project.initialized and project.working_directory_version == 0:
        for field, value in LEGACY_DEFAULTS.items():
            if not project.is_property_set(field):
                setattr(project, field, value)
        project.legacy_working_directory = True
    if project.initialized and project.working_directory_version < FORMAT_VERSION:
        # Earlier v3 also had an implicit //Lightmaps RNA default. Pin it
        # before adopting the new PMVR/Lightmaps default for NEW projects.
        for field in ('lightmap_output_directory', 'external_texture_directory'):
            if not project.is_property_set(field):
                setattr(project, field, LEGACY_DEFAULTS[field])
        project.working_directory_version = FORMAT_VERSION


def ensure_new_folders(project):
    migrate(project)
    if not bpy.data.filepath or not project.initialized:
        return
    if project.legacy_working_directory:
        return
    root = Path(bpy.data.filepath).parent / 'PMVR'
    for name in ('Bakes', 'Flattened', 'Logs', 'ProbePreviews', 'Lightmaps', 'Textures'):
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
