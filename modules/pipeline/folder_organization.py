"""Explicit, verified local-folder migration; never runs when loading a file.

Legacy files remain live until the remapped blend is saved. They are then
archived inside PMVR, with the pre-migration blend and a rollback journal.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

import bpy

from . import working_directory

FOLDERS = {
    'Beauty_Bakes': 'Bakes', 'PMVR_Flattened': 'Flattened',
    'PMVR_Logs': 'Logs', 'Lightmaps': 'Lightmaps',
    'PM_Selected_Textures': 'Textures',
}


class OrganizationError(RuntimeError):
    pass


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _inventory(folder):
    files = {}
    if not folder.exists():
        return files
    def linked(path):
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
        return path.is_symlink() or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))

    if linked(folder):
        raise OrganizationError(f'Cannot organize a linked directory: {folder}')
    for path in folder.rglob('*'):
        if linked(path):
            raise OrganizationError(f'Cannot organize a linked path: {path}')
        if path.is_file():
            file_stat = path.stat()
            files[path.relative_to(folder).as_posix()] = (file_stat.st_size, file_stat.st_mtime_ns, _digest(path))
    return files


def plan():
    if not bpy.data.filepath:
        raise OrganizationError('Save the project before organizing its folders')
    root = Path(bpy.data.filepath).resolve().parent
    if any(scene.pm_vr_project.operation_running for scene in bpy.data.scenes):
        raise OrganizationError('Wait for the bake, probe render or export to finish')
    configured = {Path(bpy.path.abspath(getattr(scene.pm_vr_project, field))).resolve()
                  for scene in bpy.data.scenes
                  for field in working_directory.NEW_DEFAULTS}
    jobs = []
    for old_name, new_name in FOLDERS.items():
        source, target = root / old_name, root / 'PMVR' / new_name
        if not source.exists() and source not in configured:
            continue
        if source.exists() and (not source.is_dir() or source.resolve() != source):
            raise OrganizationError(f'Legacy folder is not an ordinary project directory: {source}')
        if target.exists():
            raise OrganizationError(f'Destination already exists; nothing was moved: {target}')
        if target.parent.exists() and target.parent.resolve() != target.parent:
            raise OrganizationError('PMVR must be an ordinary project-local directory')
        jobs.append((source, target))
    return root, jobs


def _path_changes(jobs):
    changes = []

    def add(owner, key, custom=False):
        value = owner.get(key, '') if custom else getattr(owner, key)
        if not value or not isinstance(value, str) or value.startswith('<'):
            return
        datablock = getattr(owner, 'id_data', owner)
        library = getattr(datablock, 'library', None)
        resolved = Path(bpy.path.abspath(value, library=library)).resolve()
        for source, target in jobs:
            if resolved.is_relative_to(source):
                if library or not getattr(datablock, 'is_editable', True):
                    raise OrganizationError(f'A linked datablock uses {value}; localize it before organizing')
                destination = str(target / resolved.relative_to(source))
                # Legacy lightmap metadata is read by os.path, not bpy.path.
                # Custom ID strings do not participate in Blender Save As
                # remapping and must retain a real absolute file path.
                mapped = destination if custom or key in {'file', 'day_file', 'evening_file'} else bpy.path.relpath(destination).replace(os.sep, '/')
                changes.append((owner, key, custom, value, mapped))
                break

    for name in ('images', 'libraries', 'movieclips', 'sounds', 'fonts', 'cache_files'):
        for owner in getattr(bpy.data, name):
            add(owner, 'filepath')
            if name == 'images' and 'pm_lightmap_export_path' in owner:
                add(owner, 'pm_lightmap_export_path', True)
    trees = {tree.as_pointer(): tree for tree in bpy.data.node_groups}
    for name in ('materials', 'worlds', 'lights', 'scenes'):
        for owner in getattr(bpy.data, name):
            tree = getattr(owner, 'node_tree', None)
            if tree:
                trees[tree.as_pointer()] = tree
    for tree in trees.values():
        for node in tree.nodes:
            if hasattr(node, 'filepath'):
                add(node, 'filepath')
    for scene in bpy.data.scenes:
        project = scene.pm_vr_project
        for field in working_directory.NEW_DEFAULTS:
            add(project, field)
        for unit in project.bake_units:
            for variant in unit.variants:
                for field in ('day_file', 'evening_file'):
                    add(variant, field)
                for result in variant.look_results:
                    add(result, 'file')
        if hasattr(scene, 'pm_lightmap_settings'):
            add(scene.pm_lightmap_settings, 'output_directory')
        add(scene.render, 'filepath')
    return changes


def _set(changes, restore=False):
    for owner, key, custom, old, new in changes:
        value = old if restore else new
        if custom:
            owner[key] = value
        else:
            setattr(owner, key, value)


def _journal(path, document):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _save_blend(path, copy=False):
    result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=copy, relative_remap=False)
    if 'FINISHED' not in result:
        raise OrganizationError('Could not save the project or its rollback backup')


def organize(context):
    root, jobs = plan()
    if not jobs:
        return None
    changes = _path_changes(jobs)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    backup_root = root / 'PMVR' / 'Backups' / ('BeforeOrganization_' + stamp)
    backup_root.mkdir(parents=True, exist_ok=False)
    blend = Path(bpy.data.filepath).resolve()
    backup = backup_root / blend.name
    journal = backup_root / 'organization.json'
    document = {'format': 1, 'createdAt': datetime.now(timezone.utc).isoformat(),
                'blend': str(blend), 'backupBlend': str(backup), 'phase': 'COPYING',
                'folders': [{'source': str(source), 'target': str(target),
                             'archive': str(backup_root / source.name)} for source, target in jobs]}
    _journal(journal, document)
    # copy=True keeps the active filepath. The backup must be restored to the
    # original location before opening: its old blend-relative paths stay exact.
    _save_blend(backup, copy=True)
    document['backupBlendSHA256'] = _digest(backup)
    _journal(journal, document)
    original_version = context.preferences.filepaths.save_version
    published = []
    saved = False
    try:
        for source, target in jobs:
            inventory = _inventory(source)
            staged = backup_root / ('Copy_' + target.name)
            if source.exists():
                shutil.copytree(source, staged, copy_function=shutil.copy2)
            else:
                staged.mkdir()
            if _inventory(staged) != inventory or _inventory(source) != inventory:
                raise OrganizationError(f'Files changed or failed verification: {source.name}')
            document['folders'][len(published)]['files'] = inventory
            _journal(journal, document)
            staged.rename(target)
            published.append((source, target, staged))
        _set(changes)
        for value in bpy.utils.blend_paths(absolute=True, packed=False):
            path = Path(value).resolve()
            if any(path.is_relative_to(source) for source, target in jobs):
                raise OrganizationError(f'An unsupported file reference still uses a legacy folder: {value}')
        document['phase'] = 'SAVING'
        _journal(journal, document)
        # Keep Blender's automatic .blend1 out of the project root: our complete
        # explicit backup was saved above. Do not persist preference changes.
        context.preferences.filepaths.save_version = 0
        _save_blend(blend)
        saved = True
        document['phase'] = 'SAVED'
        _journal(journal, document)
    except Exception:
        if not saved:
            _set(changes, restore=True)
            for source, target, staged in reversed(published):
                target.rename(staged)
            # The backup serializes the live pre-migration scene, including
            # unsaved edits; restore it if a failed save replaced the main file.
            shutil.copy2(backup, blend)
            document['phase'] = 'ROLLED_BACK'
            _journal(journal, document)
        raise
    finally:
        context.preferences.filepaths.save_version = original_version
    # A failure here cannot invalidate the new blend: both verified copies
    # remain. The journal records exactly which old directories still exist.
    try:
        for source, target, staged in published:
            expected = next(item['files'] for item in document['folders'] if item['source'] == str(source))
            if _inventory(source) != {key: tuple(value) for key, value in expected.items()}:
                raise OrganizationError(f'Legacy files changed before archiving: {source.name}')
            if source.exists():
                source.rename(backup_root / source.name)
        document['phase'] = 'COMPLETE'
    except OSError as exc:
        document['phase'] = 'SAVED_ARCHIVE_PENDING'
        document['warning'] = str(exc)
    except OrganizationError as exc:
        document['phase'] = 'SAVED_ARCHIVE_PENDING'
        document['warning'] = str(exc)
    _journal(journal, document)
    return document


def rollback(journal_path):
    """Restore old paths with the project closed; preserve organized files too."""
    journal_path = Path(journal_path).resolve()
    document = json.loads(journal_path.read_text(encoding='utf-8'))
    blend = Path(document['blend']).resolve()
    root, backup_root = blend.parent, journal_path.parent
    if (document.get('format') != 1 or document.get('phase') not in
            {'COPYING', 'SAVING', 'COMPLETE', 'SAVED', 'SAVED_ARCHIVE_PENDING', 'ROLLED_BACK'} or
            not backup_root.is_relative_to(root / 'PMVR' / 'Backups')):
        raise OrganizationError('This is not a completed local organization journal')
    if bpy.data.filepath and Path(bpy.data.filepath).resolve() == blend:
        raise OrganizationError('Close the organized project before restoring its backup')
    jobs = []
    for item in document['folders']:
        source, archive, target = (Path(item[key]).resolve() for key in ('source', 'archive', 'target'))
        if (source.parent != root or source.name not in FOLDERS or
                archive != backup_root / source.name or target != root / 'PMVR' / FOLDERS[source.name]):
            raise OrganizationError('Invalid folder paths in the organization journal')
        expected = {key: tuple(value) for key, value in item.get('files', {}).items()}
        if source.exists():
            if 'files' in item and _inventory(source) != expected:
                raise OrganizationError(f'Will not overwrite changed legacy files: {source}')
        elif archive.exists():
            if _inventory(archive) != expected:
                raise OrganizationError(f'Backup files failed verification: {archive}')
            jobs.append((archive, source))
        elif expected:
            raise OrganizationError(f'Legacy backup is missing: {archive}')
    backup = Path(document['backupBlend']).resolve()
    if backup != backup_root / blend.name or not backup.is_file():
        raise OrganizationError('Pre-organization blend backup is missing')
    if document.get('backupBlendSHA256') and _digest(backup) != document['backupBlendSHA256']:
        raise OrganizationError('Pre-organization blend backup failed verification')
    preserved = backup_root / ('BeforeRollback_' + uuid.uuid4().hex[:8] + '.blend')
    shutil.copy2(blend, preserved)
    for archive, source in jobs:
        shutil.copytree(archive, source, copy_function=shutil.copy2)
    shutil.copy2(backup, blend)
    document['phase'] = 'RESTORED'
    document['preservedOrganizedBlend'] = str(preserved)
    _journal(journal_path, document)
    return document


class PMVR_OT_OrganizeProjectFolders(bpy.types.Operator):
    bl_idname = 'pmvr.organize_project_folders'
    bl_label = 'Organize Local Files'
    bl_description = 'Verify and move legacy working folders into PMVR, save the project and keep a complete rollback backup'

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath and context.scene.pm_vr_project.initialized
                    and not any(s.pm_vr_project.operation_running for s in bpy.data.scenes))

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event,
            title='Organize Local Files', message='Saves this project; verified old files and a blend backup stay in PMVR/Backups.')

    def execute(self, context):
        try:
            result = organize(context)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if result is None:
            self.report({'INFO'}, 'No legacy working folders to organize')
        elif result['phase'] != 'COMPLETE':
            self.report({'WARNING'}, 'Project saved with organized paths; some old folders remain. See PMVR/Backups/organization.json')
        else:
            self.report({'INFO'}, 'Local files organized; rollback backup is in PMVR/Backups')
        return {'FINISHED'}


CLASSES = (PMVR_OT_OrganizeProjectFolders,)
