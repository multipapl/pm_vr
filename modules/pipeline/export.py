"""Semantic layer export assembled from generated and original representations."""

import os
import shutil
import uuid

import bpy

from .. import collection_export
from .bake_scene import PipelineBakeError
from .constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID
from .generated import (
    bind_generated_state,
    restore_generated_bindings,
    snapshot_generated_bindings,
)
from .identity import duplicate_source_ids, export_layer_members, find_layer, find_unit, safe_stem
from . import log
from .state import activate_state
from .validation import object_render_visible


class PipelineExportError(RuntimeError):
    pass


class PipelineExportCancelled(PipelineExportError):
    pass


def _generated_beauty_index():
    index = {}
    for obj in bpy.data.objects:
        if obj.get(TAG_GENERATED) and obj.get(TAG_MODE) == 'BEAUTY':
            key = (obj.get(TAG_UNIT_ID), obj.get(TAG_SOURCE_ID))
            index.setdefault(key, []).append(obj)
    return index


def _generated_for_source(source, unit_id, index):
    matches = index.get((unit_id, source.pm_vr_pipeline.source_id), [])
    if len(matches) > 1:
        names = ", ".join(obj.name for obj in matches)
        raise PipelineExportError(
            f'{source.name}: several generated Beauty objects claim this source '
            f'({names}); delete the extra copies'
        )
    return matches[0] if matches else None


def _unit_ready(unit, state):
    signature = unit.day_signature if state == 'DAY' else unit.evening_signature
    status = unit.day_status if state == 'DAY' else unit.evening_status
    other = unit.evening_signature if state == 'DAY' else unit.day_signature
    if not signature or status != "Ready":
        return False, f'{unit.display_name}: {state.title()} Beauty is not ready'
    if other and other != signature:
        return False, f'{unit.display_name}: Day/Evening structure is incompatible; rebake both states'
    return True, ""


def resolve_layer_objects(context, layer):
    project = context.scene.pm_vr_project
    state = project.active_lighting_state
    resolved = []
    seen = set()
    bound_units = set()
    generated_index = _generated_beauty_index()
    for source in export_layer_members(layer.layer_id):
        metadata = source.pm_vr_pipeline
        if not metadata.source_id:
            raise PipelineExportError(f'{source.name}: registered source has no ID')
        if metadata.render_layer_id != layer.layer_id:
            owner = find_layer(project, metadata.render_layer_id)
            if not owner or owner.layer_type != layer.layer_type:
                raise PipelineExportError(
                    f'{source.name}: additional export to {layer.display_name} has an incompatible source layer'
                )
        if metadata.processing_role == 'UNASSIGNED':
            raise PipelineExportError(f'{source.name}: role is Unassigned')
        if metadata.processing_role == 'EXPORT_ORIGINAL':
            if object_render_visible(source, context.view_layer) and source.as_pointer() not in seen:
                resolved.append(source)
                seen.add(source.as_pointer())
            continue
        unit = find_unit(project, metadata.bake_unit_id)
        if not unit:
            raise PipelineExportError(f'{source.name}: bake unit is missing')
        ready, message = _unit_ready(unit, state)
        if not ready:
            raise PipelineExportError(message)
        if unit.unit_id not in bound_units:
            try:
                bind_generated_state(unit, state, strict=True)
            except PipelineBakeError as exc:
                raise PipelineExportError(f'{unit.display_name}: {exc}') from exc
            bound_units.add(unit.unit_id)
        if not object_render_visible(source, context.view_layer):
            continue
        generated = _generated_for_source(source, unit.unit_id, generated_index)
        if not generated:
            raise PipelineExportError(f'{source.name}: generated Beauty object is missing')
        if generated.as_pointer() not in seen:
            resolved.append(generated)
            seen.add(generated.as_pointer())
    return resolved


def _make_assembly(scene, layer, objects):
    collection = bpy.data.collections.new(f"__PMVR_EXPORT_{layer.layer_id[:8]}_{uuid.uuid4().hex[:8]}")
    scene.collection.children.link(collection)
    for obj in objects:
        collection.objects.link(obj)
    return collection


def _remove_assembly(scene, assembly):
    if assembly and bpy.data.collections.get(assembly.name) is assembly:
        if assembly.name in {child.name for child in scene.collection.children}:
            scene.collection.children.unlink(assembly)
        bpy.data.collections.remove(assembly)


def _export_path(project, layer, state, format_name):
    directory_value = project.usdz_output_directory if format_name == 'USDZ' else project.glb_output_directory
    if directory_value.startswith("//") and not bpy.data.filepath:
        raise PipelineExportError(f"Save the .blend file before using a relative {format_name} directory")
    directory = bpy.path.abspath(directory_value)
    os.makedirs(directory, exist_ok=True)
    suffix = "" if state == 'DAY' else "_Evening"
    extension = ".usdz" if format_name == 'USDZ' else ".glb"
    return os.path.join(directory, f"{safe_stem(layer.display_name)}{suffix}{extension}")


def _temporary_export_path(final_path):
    # Export under the final filename inside a private folder: USDZ stores the
    # file stem as its root layer name, so a renamed temporary file would leak
    # into the published package.
    directory, filename = os.path.split(final_path)
    folder = os.path.join(directory, f".pmvr_export_{uuid.uuid4().hex[:12]}")
    os.makedirs(folder)
    return folder, os.path.join(folder, filename)


def export_semantic_layer(context, layer, format_name):
    project = context.scene.pm_vr_project
    # Export binds the active state's materials to canonical generated objects;
    # put the viewport preview binding back afterwards.
    bindings = snapshot_generated_bindings()
    assembly = None
    folder = None
    try:
        objects = resolve_layer_objects(context, layer)
        if not objects:
            return "SKIPPED", "no visible objects for active state"
        final_path = _export_path(project, layer, project.active_lighting_state, format_name)
        folder, temporary_path = _temporary_export_path(final_path)
        assembly = _make_assembly(context.scene, layer, objects)
        exporter = collection_export.export_usdz if format_name == 'USDZ' else collection_export.export_glb
        result = exporter(assembly, temporary_path)
        if 'CANCELLED' in result:
            raise PipelineExportCancelled(f"{format_name} export was cancelled")
        if 'FINISHED' not in result or not os.path.exists(temporary_path):
            raise PipelineExportError(f"Blender did not produce {format_name}")
        os.replace(temporary_path, final_path)
        log.info("Export", f"{layer.display_name} ({format_name}): {len(objects)} object(s) -> {final_path}")
        return "SUCCESS", final_path
    finally:
        _remove_assembly(context.scene, assembly)
        restore_generated_bindings(bindings)
        if folder:
            shutil.rmtree(folder, ignore_errors=True)


class PMVR_OT_ExportSemanticLayers(bpy.types.Operator):
    bl_idname = "pmvr.export_semantic_layers"
    bl_label = "Export Checked Layers"
    bl_description = "Export enabled semantic layers for the active lighting state"

    export_format: bpy.props.EnumProperty(
        name="Format",
        items=(('USDZ', "USDZ", ""), ('GLB', "GLB", ""), ('BOTH', "Both", "")),
        default='USDZ',
    )

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(project.render_layers and not project.operation_running)

    def execute(self, context):
        project = context.scene.pm_vr_project
        duplicate_ids = duplicate_source_ids()
        if duplicate_ids:
            self.report({'ERROR'}, f"Export blocked: {len(duplicate_ids)} duplicate source ID(s); repair them first")
            return {'CANCELLED'}
        try:
            activate_state(context, project.active_lighting_state)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        formats = ('USDZ', 'GLB') if self.export_format == 'BOTH' else (self.export_format,)
        jobs = []
        for layer in project.render_layers:
            if not layer.enabled:
                continue
            for format_name in formats:
                if (format_name == 'USDZ' and layer.export_usdz) or (format_name == 'GLB' and layer.export_glb):
                    jobs.append((layer, format_name))
        if not jobs:
            self.report({'ERROR'}, "No enabled layer/format jobs")
            return {'CANCELLED'}
        names = [(fmt, safe_stem(layer.display_name).casefold()) for layer, fmt in jobs]
        if len(names) != len(set(names)):
            self.report({'ERROR'}, "Enabled render layers contain duplicate output names")
            return {'CANCELLED'}
        succeeded = skipped = failed = 0
        first_error = ""
        cancelled = False
        log.info(
            "Export",
            f"Start: {len(jobs)} file(s), {project.active_lighting_state.title()}, "
            f"{bpy.data.filepath or 'unsaved file'}",
        )
        project.operation_running = True
        try:
            context.window_manager.progress_begin(0, len(jobs))
            for index, (layer, format_name) in enumerate(jobs):
                context.window_manager.progress_update(index)
                project.operation_progress = index / max(1, len(jobs))
                try:
                    status, message = export_semantic_layer(context, layer, format_name)
                    if status == 'SKIPPED':
                        skipped += 1
                        log.info("Export", f'Skipped "{layer.display_name}" ({format_name}): {message}')
                    else:
                        succeeded += 1
                except PipelineExportCancelled:
                    cancelled = True
                    log.warning("Export", f'Cancelled at "{layer.display_name}" ({format_name})')
                    break
                except Exception as exc:
                    failed += 1
                    if not first_error:
                        first_error = f'{layer.display_name} ({format_name}): {exc}'
                    log.error(
                        "Export",
                        f'Failed "{layer.display_name}" ({format_name}): {exc}',
                        with_traceback=not isinstance(exc, PipelineExportError),
                    )
            project.operation_progress = 1.0
        finally:
            context.window_manager.progress_end()
            project.operation_running = False
        summary = f"Export: {succeeded} ready, {skipped} skipped, {failed} failed"
        if cancelled:
            summary += ", cancelled"
        project.last_operation_summary = summary
        log.info("Export", summary)
        self.report(
            {'WARNING'} if failed or cancelled else {'INFO'},
            summary + (f"; {first_error}" if first_error else ""),
        )
        return {'FINISHED'} if (succeeded or skipped) and not cancelled else {'CANCELLED'}


CLASSES = (PMVR_OT_ExportSemanticLayers,)
