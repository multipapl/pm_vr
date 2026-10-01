"""Semantic layer export assembled from generated and original representations."""

from contextlib import contextmanager, ExitStack
import os
import shutil
import tempfile
import uuid

import bpy
from . import looks

from .. import collection_export
from .bake_files import png_size, scale_atlas
from .bake_scene import PipelineBakeError, same_structure
from .constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID
from .generated import (
    bind_generated_state,
    restore_generated_bindings,
    snapshot_generated_bindings,
    use_bake_uv,
)
from .identity import duplicate_source_ids, export_layer_members, find_layer, find_unit, safe_stem, unit_members
from . import export_colours, export_description, log, platform, usd_check, variants
from .setup_ops import baked_resolution
from .state import activate_state


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
    signature = looks.result_value(unit, state, 'signature')
    status = looks.result_value(unit, state, 'status')
    if not signature or status != "Ready":
        return False, f'{unit.display_name}: {looks.name(bpy.context.scene.pm_vr_project, state)} Beauty is not ready'
    if any(looks.result_value(unit, other, 'signature') and
           not same_structure(looks.result_value(unit, other, 'signature'), signature)
           for other in looks.result_ids(unit) if other != state):
        return False, f'{unit.display_name}: lighting structure is incompatible; rebake the affected looks'
    return True, ""


def other_state_objects(project, state):
    """Pointers of objects that live only inside the other state's lighting
    collection (evening-only lamps, say). They are the only objects a state's
    export leaves out: export follows the setup, not what happens to be
    visible, excluded or render-disabled in the file."""
    looks.ensure(project)
    others = [look.lighting_collection for look in project.lighting_looks
              if look.look_id != state and look.lighting_collection]
    inside = {collection.name_full for other in others for collection in (other, *other.children_recursive)}
    return {
        obj.as_pointer() for other in others for obj in other.all_objects
        if all(collection.name_full in inside for collection in obj.users_collection)
    }


def resolve_layer_objects(context, layer):
    project = context.scene.pm_vr_project
    state = looks.active_id(project)
    resolved = []
    seen = set()
    bound_units = set()
    generated_index = _generated_beauty_index()
    skipped = other_state_objects(project, state)
    for source in export_layer_members(layer.layer_id):
        if source.as_pointer() in skipped:
            continue
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
            if source.as_pointer() not in seen:
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
        generated = _generated_for_source(source, unit.unit_id, generated_index)
        if not generated:
            raise PipelineExportError(f'{source.name}: generated Beauty object is missing')
        if generated.as_pointer() not in seen:
            resolved.append(generated)
            seen.add(generated.as_pointer())
    return resolved


class ExportTextures:
    """Beauty atlases at their units' resolution, for one export run.

    The baked files stay at the Bake Resolution. While a layer is written,
    each Beauty image points at a copy averaged down to its unit's
    resolution, then back at the baked file. A copy keeps the baked file's
    name, so the USDZ texture names do not change."""

    def __init__(self):
        self.folder = tempfile.mkdtemp(prefix="pmvr_export_textures_")
        self.copies = {}

    def cleanup(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def summary(self):
        pairs = {}
        for (_path, size), (_copy, baked) in self.copies.items():
            pairs[(baked, size)] = pairs.get((baked, size), 0) + 1
        return ", ".join(
            f"{count} x {baked} to {size}" for (baked, size), count in sorted(pairs.items(), reverse=True)
        )

    def _copy(self, path, size):
        key = (os.path.normcase(os.path.abspath(path)), size)
        if key not in self.copies:
            folder = os.path.join(self.folder, str(len(self.copies)))
            os.makedirs(folder)
            copy = os.path.join(folder, os.path.basename(path))
            baked = png_size(path)[0]
            scale_atlas(path, copy, size)
            self.copies[key] = (copy, baked)
        return self.copies[key][0]

    @contextmanager
    def scaled(self, project, objects):
        images = {}
        for obj in objects:
            if not obj.get(TAG_GENERATED):
                continue
            for slot in obj.material_slots:
                material = slot.material
                if not material or not material.node_tree:
                    continue
                for node in material.node_tree.nodes:
                    image = getattr(node, "image", None)
                    if node.type == 'TEX_IMAGE' and image and image.get(TAG_MODE) == 'BEAUTY':
                        images[image.name_full] = image
        swapped = []
        try:
            for image in images.values():
                unit = find_unit(project, image.get(TAG_UNIT_ID, ""))
                path = bpy.path.abspath(image.filepath)
                size = png_size(path) if unit and image.source == 'FILE' else None
                if not size or size[0] <= int(unit.resolution):
                    # Already at (or below) the unit's resolution: as baked.
                    continue
                copy = self._copy(path, int(unit.resolution))
                swapped.append((image, image.filepath))
                image.filepath = copy
                image.reload()
            yield
        finally:
            for image, filepath in swapped:
                try:
                    image.filepath = filepath
                    image.reload()
                except ReferenceError:
                    pass


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
    suffix = looks.suffix(project, state)
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


def _unit_generated(project, unit, index):
    members = unit_members(unit.unit_id)
    return _generated_for_source(members[0], unit.unit_id, index) if len(members) == 1 else None


@contextmanager
def _variant_marker(unit):
    """The unit's marker as an Empty named exactly VariantMarker (the name
    the app looks for), for the length of one export."""
    marker = unit.variant_marker
    if not marker:
        yield []
        return
    if marker.name == variants.MARKER_NAME:
        yield [marker]
        return
    holder = bpy.data.objects.get(variants.MARKER_NAME)
    if holder:
        holder.name = f"{variants.MARKER_NAME}__pmvr_{uuid.uuid4().hex[:6]}"
    temporary = bpy.data.objects.new(variants.MARKER_NAME, None)
    temporary.matrix_world = marker.matrix_world.copy()
    try:
        yield [temporary]
    finally:
        bpy.data.objects.remove(temporary)
        if holder:
            holder.name = variants.MARKER_NAME


def _export_variants(context, project, layer, state, textures, written_paths=None):
    """Variants/<Object>_<Variant>[_Evening].usdz for the layer's units with
    variants: the generated object itself (so its name is the scene's) with
    the variant's baked colour. Swatches are made by hand and placed in
    Variants/; export never writes them. Returns (written, problems)."""
    units = [unit for unit in project.bake_units if unit.render_layer_id == layer.layer_id and len(unit.variants)]
    if not units:
        return 0, []
    staging = os.path.dirname(variants.staging_folder(project))
    index = _generated_beauty_index()
    written, problems = 0, []
    for unit in units:
        problem = variants.variant_problem(project, unit)
        generated = None if problem else _unit_generated(project, unit, index)
        if problem or not generated:
            problems.append(f'"{unit.display_name}": {problem or "generated object is missing"}')
            continue
        entity = variants.usd_name(generated.name)
        for variant in unit.variants:
            status = variants.variant_status(unit, variant, state)
            if status != "Ready":
                problems.append(
                    f'"{unit.display_name}" variant "{variant.title}": '
                    + (f"no {looks.name(project, state)} bake" if not status else "baked before the unit's last bake; rebake the unit")
                )
                continue
            image = bpy.data.images.load(bpy.path.abspath(variants.variant_file(variant, state)), check_existing=False)
            # Tagged like a Beauty atlas, so export scales it to the unit's resolution.
            image[TAG_GENERATED] = True
            image[TAG_UNIT_ID] = unit.unit_id
            image[TAG_MODE] = 'BEAUTY'
            bound = list(generated.data.materials)
            copies = []
            try:
                for slot, material in enumerate(bound):
                    if not material:
                        continue
                    copy = material.copy()
                    copies.append(copy)
                    for node in copy.node_tree.nodes if copy.node_tree else ():
                        if (
                            node.type == 'TEX_IMAGE' and node.image
                            and node.image.get(TAG_MODE) == 'BEAUTY'
                            and node.image.get(TAG_UNIT_ID) == unit.unit_id
                        ):
                            node.image = image
                    generated.data.materials[slot] = copy
                final_path = os.path.join(staging, variants.model_path(entity, variant, state))
                with _variant_marker(unit) as marker:
                    _write_usdz(context, project, layer, [generated, *marker], final_path, textures)
                written += 1
                if written_paths is not None:
                    written_paths.append(final_path)
            finally:
                for slot, material in enumerate(bound):
                    generated.data.materials[slot] = material
                for copy in copies:
                    bpy.data.materials.remove(copy)
                bpy.data.images.remove(image)
    return written, problems


def _settle_uvs(objects, label):
    """SimpleBake as active and render UV map on the generated objects about
    to be written (see use_bake_uv); a file opened with the fix already has
    it, this also covers a flag switched by hand since."""
    switched = [obj.name for obj in objects if obj.get(TAG_GENERATED) and use_bake_uv(obj)]
    if switched:
        log.info(
            "Export",
            f"{label}: SimpleBake made active and render UV on {len(switched)} generated object(s): "
            + ", ".join(switched[:10]) + (f" and {len(switched) - 10} more" if len(switched) > 10 else ""),
        )


def _check_written(path):
    """A written USDZ counts as exported only when the Mac side reads it as
    meant (usd_check). The file stays written; the problems go to the log."""
    problems = usd_check.check_usd(path)
    if not problems:
        return
    name = os.path.basename(path)
    for problem in problems[:50]:
        log.error("Export", f"USD check {name}: {problem}")
    if len(problems) > 50:
        log.error("Export", f"USD check {name}: {len(problems) - 50} more problem(s)")
    raise PipelineExportError(
        f"written, but the USD check found {len(problems)} problem(s); first: {problems[0]}"
    )


def _write_usdz(context, project, layer, objects, final_path, textures):
    folder, temporary_path = _temporary_export_path(final_path)
    assembly = _make_assembly(context.scene, layer, objects)
    render_disabled = [obj for obj in objects if obj.hide_render]
    _settle_uvs(objects, os.path.basename(final_path))
    try:
        for obj in render_disabled:
            obj.hide_render = False
        with platform.authored_properties(objects), textures.scaled(project, objects), export_colours.explicit_colours(
            context, objects, os.path.basename(final_path)
        ):
            result = collection_export.export_usdz(assembly, temporary_path)
        if 'FINISHED' not in result or not os.path.exists(temporary_path):
            raise PipelineExportError(f"Blender did not produce {os.path.basename(final_path)}")
        _check_written(temporary_path)
        os.replace(temporary_path, final_path)
    finally:
        for obj in render_disabled:
            obj.hide_render = True
        _remove_assembly(context.scene, assembly)
        shutil.rmtree(folder, ignore_errors=True)


def write_variant_manifest(project):
    """Variants/materialVariants.json for every unit with variants whose Day
    file is in the staging folder (the Day files are the models), whichever
    layers this export wrote: a partial export (only the lamp layers, say)
    used to rewrite it empty. None when nothing to write."""
    staging = bpy.path.abspath(project.usdz_output_directory)
    index = _generated_beauty_index()
    entries = []
    for unit in project.bake_units:
        if not len(unit.variants):
            continue
        if variants.variant_problem(project, unit):
            continue
        generated = _unit_generated(project, unit, index)
        entry = generated and variants.manifest_entry(unit, variants.usd_name(generated.name), staging)
        if entry:
            entries.append(entry)
    if not entries and not os.path.isdir(os.path.join(staging, variants.FOLDER)):
        return None
    return variants.write_manifest(staging, entries), len(entries)


def export_semantic_layer(context, layer, format_name, textures=None):
    project = context.scene.pm_vr_project
    # Export binds the active state's materials to canonical generated objects;
    # put the viewport preview binding back afterwards.
    bindings = snapshot_generated_bindings()
    assembly = None
    folder = None
    own_textures = textures is None
    representations = ExitStack()
    if own_textures:
        textures = ExportTextures()
    try:
        objects = resolve_layer_objects(context, layer)
        if not objects:
            if format_name == 'USDZ':
                export_description.write(project, looks.active_id(project), excluded=(layer.layer_id,))
            return "SKIPPED", "no objects for the active state"
        if layer.layer_type == 'RUNTIME':
            for name, problem in platform.runtime_warnings(objects):
                log.warning('Export', f'{name}: {problem}')
            from .probe_export import representations as probe_representations
            objects = representations.enter_context(probe_representations(context, project, objects))
        _settle_uvs(objects, f"{layer.display_name} ({format_name})")
        final_path = _export_path(project, layer, looks.active_id(project), format_name)
        folder, temporary_path = _temporary_export_path(final_path)
        assembly = _make_assembly(context.scene, layer, objects)
        exporter = collection_export.export_usdz if format_name == 'USDZ' else collection_export.export_glb
        # The USD exporter evaluates for render and drops render-disabled
        # objects; the camera toggle is a working state, not a setup choice.
        render_disabled = [obj for obj in objects if obj.hide_render]
        try:
            for obj in render_disabled:
                obj.hide_render = False
            with platform.authored_properties(objects), textures.scaled(project, objects), export_colours.explicit_colours(
                context, objects, f"{layer.display_name} ({format_name})"
            ):
                result = exporter(assembly, temporary_path)
        finally:
            for obj in render_disabled:
                obj.hide_render = True
        if 'CANCELLED' in result:
            raise PipelineExportCancelled(f"{format_name} export was cancelled")
        if 'FINISHED' not in result or not os.path.exists(temporary_path):
            raise PipelineExportError(f"Blender did not produce {format_name}")
        if format_name == 'USDZ':
            _check_written(temporary_path)
        os.replace(temporary_path, final_path)
        log.info("Export", f"{layer.display_name} ({format_name}): {len(objects)} object(s) -> {final_path}")
        committed = [final_path]
        if format_name == 'USDZ' and layer.layer_type == 'UNLIT':
            written, problems = _export_variants(
                context, project, layer, looks.active_id(project), textures, written_paths=committed
            )
            if written:
                log.info("Export", f"{layer.display_name}: {written} variant file(s) in {variants.FOLDER}/")
            for problem in problems:
                log.warning("Export", f"Variant not exported: {problem}")
        if format_name == 'USDZ':
            export_description.write(project, looks.active_id(project), committed)
        return "SUCCESS", final_path
    finally:
        _remove_assembly(context.scene, assembly)
        representations.close()
        restore_generated_bindings(bindings)
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        if own_textures:
            textures.cleanup()


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
            activate_state(context, looks.active_id(project))
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
        # A test bake (or Setup raised after baking) is still a valid result;
        # say so instead of shipping lower-resolution textures unnoticed.
        state = looks.active_id(project)
        exported_layers = {layer.layer_id for layer, _format_name in jobs}
        below_setup = [
            unit.display_name for unit in project.bake_units
            if unit.render_layer_id in exported_layers
            and 0 < baked_resolution(unit, state) < int(unit.resolution)
        ]
        if below_setup:
            log.warning(
                "Export",
                f"{len(below_setup)} unit(s) baked below their Setup resolution: "
                + ", ".join(f'"{name}"' for name in below_setup[:10])
                + (f" and {len(below_setup) - 10} more" if len(below_setup) > 10 else ""),
            )
        succeeded = skipped = failed = 0
        first_error = ""
        cancelled = False
        log.info(
            "Export",
            f"Start: {len(jobs)} file(s), {looks.name(project, looks.active_id(project))}, "
            f"{bpy.data.filepath or 'unsaved file'}",
        )
        project.operation_running = True
        textures = ExportTextures()
        try:
            context.window_manager.progress_begin(0, len(jobs))
            for index, (layer, format_name) in enumerate(jobs):
                context.window_manager.progress_update(index)
                project.operation_progress = index / max(1, len(jobs))
                try:
                    status, message = export_semantic_layer(context, layer, format_name, textures)
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
            scaled = textures.summary()
            textures.cleanup()
        if scaled:
            log.info("Export", f"Atlases scaled to unit resolution: {scaled}")
        if 'USDZ' in formats:
            manifest = write_variant_manifest(project)
            if manifest:
                log.info("Export", f"Material variants: {manifest[1]} object(s) -> {manifest[0]}")
            export_description.write(project, looks.active_id(project))
        summary = f"Export: {succeeded} ready, {skipped} skipped, {failed} failed"
        if cancelled:
            summary += ", cancelled"
        if below_setup:
            summary += f"; {len(below_setup)} unit(s) baked below Setup resolution"
        project.last_operation_summary = summary
        log.info("Export", summary)
        self.report(
            {'WARNING'} if failed or cancelled or below_setup else {'INFO'},
            summary + (f"; {first_error}" if first_error else ""),
        )
        return {'FINISHED'} if (succeeded or skipped) and not cancelled else {'CANCELLED'}


CLASSES = (PMVR_OT_ExportSemanticLayers,)
