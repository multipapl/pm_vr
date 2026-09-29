"""Setup, identity, queue, state, validation and preview operators."""

import math
import os

import bpy

from ..scene_diagnostics import get_target_td, measure_texel_areas
from .constants import (
    LAYER_COLOR_PALETTE,
    MAX_RESOLUTION_VALUE,
    REMOVED_RESOLUTION_VALUE,
    RESOLUTION_ITEMS,
    ROLE_ITEMS,
    SCHEMA_VERSION,
    BAKE_LAYER_TYPES,
    TAG_GENERATED,
    TAG_MODE,
    TAG_SOURCE_ID,
    TAG_UNIT_ID,
)
from .generated import (
    bind_generated_state,
    release_generated_material,
    remove_generated_object,
)
from .identity import (
    duplicate_source_ids,
    ensure_project_id,
    ensure_source_id,
    extra_export_members,
    find_layer,
    find_unit,
    layer_members,
    new_id,
    safe_stem,
    sources_by_id,
    unit_members,
    units_with_members,
)
from .state import PipelineStateError, activate_state
from .validation import validate_all
from . import log


SUPPORTED_RESOLUTIONS = tuple(int(item[0]) for item in RESOLUTION_ITEMS)
AUTO_RESOLUTIONS = tuple(value for value in SUPPORTED_RESOLUTIONS if value >= 1024)


def suggested_unit_resolution(context, objects):
    project = context.scene.pm_vr_project
    fallback = project.default_unit_resolution
    target_td = get_target_td(context)
    required = []
    for obj in objects:
        mesh_area_cm2, uv_area, error = measure_texel_areas(context, obj)
        if error or mesh_area_cm2 <= 0.0 or uv_area <= 0.0:
            log.warning(
                "Setup",
                f'{obj.name}: resolution fallback {fallback}; '
                f'{error or "invalid mesh/UV area"}',
            )
            return fallback
        required.append(target_td * math.sqrt(mesh_area_cm2 / uv_area))
    required_size = max(required, default=float(fallback))
    chosen = next(
        (value for value in AUTO_RESOLUTIONS if value >= required_size),
        AUTO_RESOLUTIONS[-1],
    )
    names = f'"{objects[0].name}"' + (f" and {len(objects) - 1} more" if len(objects) > 1 else "")
    capped = (
        f"; above {AUTO_RESOLUTIONS[-1]}px, texel density stays below target"
        if required_size > AUTO_RESOLUTIONS[-1] else ""
    )
    log.info(
        "Setup",
        f"Suggested {chosen}px for {names}; "
        f"required {required_size:.0f}px at {target_td:.1f}px/cm{capped}",
    )
    return str(chosen)


def active_layer(project):
    if not project.render_layers:
        return None
    return project.render_layers[min(project.active_render_layer_index, len(project.render_layers) - 1)]


def active_unit(project):
    """The highlighted unit, when it belongs to the active layer.

    Read-only: panels and poll() call it, and Blender forbids writing scene
    data there. An index left on another layer's unit means no active unit
    until one is clicked, never a unit that is not highlighted."""
    layer = active_layer(project)
    index = project.active_bake_unit_index
    if not layer or not 0 <= index < len(project.bake_units):
        return None
    unit = project.bake_units[index]
    return unit if unit.render_layer_id == layer.layer_id else None


def select_layer_unit(project, layer_id, near_index=0):
    """Make the unit of layer_id nearest to near_index active (operators only)."""
    indices = [
        index for index, unit in enumerate(project.bake_units)
        if unit.render_layer_id == layer_id
    ]
    if not indices:
        project.active_bake_unit_index = 0
        return
    project.active_bake_unit_index = min(indices, key=lambda index: (abs(index - near_index), -index))


def release_objects(objects):
    """Take objects out of their layer and unit completely, as before they were
    added: no layer, no unit, no role, no additional exports. Returns
    (cleared additional exports, IDs of the units they left)."""
    cleared_exports = 0
    left_units = set()
    for obj in objects:
        meta = obj.pm_vr_pipeline
        if meta.bake_unit_id:
            left_units.add(meta.bake_unit_id)
        meta.render_layer_id = ""
        meta.bake_unit_id = ""
        meta.processing_role = 'UNASSIGNED'
        cleared_exports += len(meta.extra_export_layers)
        meta.extra_export_layers.clear()
    return cleared_exports, left_units


def cap_removed_resolutions():
    """Files saved while 8192 existed store it as enum value 5, which no
    longer reads as a resolution; make those 4096 (pipeline units, the
    default unit resolution and the legacy Lightmap Baker)."""
    owners = []
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project:
            owners += [(unit, "resolution", unit.display_name) for unit in project.bake_units]
            owners.append((project, "default_unit_resolution", "Default Unit Resolution"))
        legacy = getattr(scene, "pm_lightmap_settings", None)
        if legacy:
            owners.append((legacy, "resolution", "Lightmap Baker"))
            owners += [(item, "resolution", item.source_name or "Lightmap row") for item in legacy.objects]
    capped = []
    for owner, prop, label in owners:
        if owner.get(prop) == REMOVED_RESOLUTION_VALUE:
            owner[prop] = MAX_RESOLUTION_VALUE
            capped.append(label)
    if capped:
        names = ", ".join(f'"{name}"' for name in capped[:10])
        more = f" and {len(capped) - 10} more" if len(capped) > 10 else ""
        log.info("Setup", f"8192 is no longer used; set {len(capped)} resolution(s) to 4096: {names}{more}")
    return len(capped)


def release_roleless_members():
    """Objects left in a layer without a role (removing a unit used to do
    that). They were invisible in Setup but failed validation and export."""
    stale = [
        obj for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline")
        and obj.pm_vr_pipeline.processing_role == 'UNASSIGNED'
        and (obj.pm_vr_pipeline.render_layer_id or obj.pm_vr_pipeline.extra_export_layers)
    ]
    release_objects(stale)
    if stale:
        names = ", ".join(f'"{obj.name}"' for obj in stale[:10])
        more = f" and {len(stale) - 10} more" if len(stale) > 10 else ""
        log.info("Setup", f"Released {len(stale)} object(s) left in a layer without a role: {names}{more}")
    return len(stale)


def remove_unit(project, unit):
    """Remove a unit with its generated objects, materials, images, queue entries
    and records; its members leave the layer. Returns the generated count."""
    unit_id = unit.unit_id
    generated_objects = [
        obj for obj in bpy.data.objects
        if obj.get(TAG_GENERATED) and obj.get(TAG_UNIT_ID) == unit_id
    ]
    for generated in generated_objects:
        remove_generated_object(generated)
    for material in list(bpy.data.materials):
        if material.get(TAG_GENERATED) and material.get(TAG_UNIT_ID) == unit_id:
            release_generated_material(material)
    for image in list(bpy.data.images):
        if image.get(TAG_GENERATED) and image.get(TAG_UNIT_ID) == unit_id and image.users == 0:
            bpy.data.images.remove(image)
    release_objects(unit_members(unit_id))
    for index in reversed(range(len(project.bake_queue))):
        if project.bake_queue[index].unit_id == unit_id:
            project.bake_queue.remove(index)
    for index in reversed(range(len(project.build_records))):
        if project.build_records[index].unit_id == unit_id:
            project.build_records.remove(index)
    active_id = (
        project.bake_units[project.active_bake_unit_index].unit_id
        if 0 <= project.active_bake_unit_index < len(project.bake_units)
        else ""
    )
    layer_id = unit.render_layer_id
    index = next(i for i, item in enumerate(project.bake_units) if item.unit_id == unit_id)
    project.bake_units.remove(index)
    # Items after the removed one move up; keep the highlight on the same
    # unit, or on its nearest neighbour of the same layer.
    if active_id and active_id != unit_id:
        project.active_bake_unit_index = next(
            i for i, item in enumerate(project.bake_units) if item.unit_id == active_id
        )
    else:
        select_layer_unit(project, layer_id, index)
    return len(generated_objects)


def remove_emptied_units(project, unit_ids):
    """Remove units among unit_ids that no longer have members. A unit is its
    members: left empty in the list, it showed up again as a duplicate when
    the same objects got a new unit. Kept while a bake runs."""
    if project.operation_running:
        return []
    occupied = units_with_members()
    removed = []
    for unit_id in unit_ids:
        unit = find_unit(project, unit_id)
        if unit and unit_id not in occupied:
            removed.append(unit.display_name)
            remove_unit(project, unit)
    if removed:
        log.info("Setup", "Removed empty unit(s): " + ", ".join(f'"{name}"' for name in removed))
    return removed


def _removed_suffix(removed):
    return f"; removed {len(removed)} empty unit(s)" if removed else ""


def _drop_incompatible_extra_exports(project, metadata, layer):
    """Additional exports must match the primary layer's type; moving an
    object to another type of layer drops the ones that no longer match."""
    for index in reversed(range(len(metadata.extra_export_layers))):
        target = find_layer(project, metadata.extra_export_layers[index].layer_id)
        if not target or target.layer_type != layer.layer_type:
            metadata.extra_export_layers.remove(index)


def _remove_extra_export_layer(metadata, layer_id):
    removed = 0
    for index in reversed(range(len(metadata.extra_export_layers))):
        if metadata.extra_export_layers[index].layer_id == layer_id:
            metadata.extra_export_layers.remove(index)
            removed += 1
    return removed


def selected_source_objects(context):
    """Accept source or generated selections for export-only membership edits."""
    source_index = sources_by_id()
    sources = []
    seen = set()
    for selected in context.selected_objects:
        source = (
            source_index.get(selected.get(TAG_SOURCE_ID, ""))
            if selected.get(TAG_GENERATED)
            else selected
        )
        if (
            source
            and hasattr(source, "pm_vr_pipeline")
            and source.pm_vr_pipeline.is_registered_source
            and source.as_pointer() not in seen
        ):
            sources.append(source)
            seen.add(source.as_pointer())
    return sources


def _report_change(operator, message):
    """Report a Setup change and keep a trail of it in the pipeline log."""
    operator.report({'INFO'}, message)
    log.info("Setup", message)


def structure_editable(context):
    """Block collection reallocation while a modal bake holds unit/layer items."""
    project = getattr(context.scene, "pm_vr_project", None) if context.scene else None
    return bool(project and not project.operation_running)


def add_layer(project, name="Unlit", layer_type='UNLIT'):
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = name
    layer.output_base_name = safe_stem(name)
    layer.layer_type = layer_type
    layer.viewport_color = LAYER_COLOR_PALETTE[
        (len(project.render_layers) - 1) % len(LAYER_COLOR_PALETTE)
    ]
    layer.viewport_color_initialized = True
    project.active_render_layer_index = len(project.render_layers) - 1
    return layer


class PMVR_OT_InitializeProject(bpy.types.Operator):
    bl_idname = "pmvr.initialize_project"
    bl_label = "Initialize Pipeline Project"
    bl_description = "Create stable project identity and an initial semantic layer set"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return structure_editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        ensure_project_id(project)
        project.schema_version = SCHEMA_VERSION
        if not project.render_layers:
            for name, layer_type in (
                ("Unlit", 'UNLIT'),
                ("PBR", 'PBR'),
                ("Alpha", 'ALPHA'),
                ("Translucent", 'TRANSLUCENT'),
                ("Glass", 'GLASS'),
                ("Emissive", 'EMISSIVE'),
                ("Runtime", 'RUNTIME'),
            ):
                add_layer(project, name, layer_type)
            project.active_render_layer_index = 0
        _report_change(self, "PM VR pipeline project initialized")
        return {'FINISHED'}


class PMVR_OT_AddRenderLayer(bpy.types.Operator):
    bl_idname = "pmvr.add_render_layer"
    bl_label = "Add Render Layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return structure_editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        ensure_project_id(project)
        add_layer(project)
        return {'FINISHED'}


class PMVR_OT_RemoveRenderLayer(bpy.types.Operator):
    bl_idname = "pmvr.remove_render_layer"
    bl_label = "Remove Render Layer"
    bl_description = "Remove only an empty semantic layer definition"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.pm_vr_project.render_layers) and structure_editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        if layer_members(layer.layer_id):
            self.report({'ERROR'}, "Unassign layer objects before removing this layer")
            return {'CANCELLED'}
        if any(unit.render_layer_id == layer.layer_id for unit in project.bake_units):
            self.report({'ERROR'}, "Remove this layer's bake units first")
            return {'CANCELLED'}
        if extra_export_members(layer.layer_id):
            self.report({'ERROR'}, "Remove additional export assignments before removing this layer")
            return {'CANCELLED'}
        index = project.active_render_layer_index
        project.render_layers.remove(index)
        project.active_render_layer_index = min(index, max(0, len(project.render_layers) - 1))
        return {'FINISHED'}


class PMVR_OT_AssignSelectedToLayer(bpy.types.Operator):
    bl_idname = "pmvr.assign_selected_to_layer"
    bl_label = "Assign Selected"
    bl_description = "Register selected source objects and assign them to the active semantic layer"
    bl_options = {'REGISTER', 'UNDO'}

    role: bpy.props.EnumProperty(name="Role", items=ROLE_ITEMS, default='BAKE')

    @classmethod
    def description(cls, _context, properties):
        if properties.role == 'EXPORT_ORIGINAL':
            return "Add the selected objects to the active layer; they export as they are, without baking"
        return cls.bl_description

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects and context.scene.pm_vr_project.render_layers)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        role = 'EXPORT_ORIGINAL' if layer.layer_type not in BAKE_LAYER_TYPES else self.role
        assigned = 0
        left_units = set()
        for obj in context.selected_objects:
            if obj.get(TAG_GENERATED):
                continue
            metadata = obj.pm_vr_pipeline
            ensure_source_id(obj)
            metadata.render_layer_id = layer.layer_id
            _remove_extra_export_layer(metadata, layer.layer_id)
            _drop_incompatible_extra_exports(project, metadata, layer)
            metadata.processing_role = 'EXPORT_ORIGINAL' if obj.type == 'EMPTY' else role
            unit = find_unit(project, metadata.bake_unit_id)
            if metadata.processing_role != 'BAKE' or (unit and unit.render_layer_id != layer.layer_id):
                if metadata.bake_unit_id:
                    left_units.add(metadata.bake_unit_id)
                metadata.bake_unit_id = ""
            assigned += 1
        removed = remove_emptied_units(project, left_units)
        _report_change(
            self,
            f"Assigned {assigned} source object(s) to {layer.display_name}{_removed_suffix(removed)}",
        )
        return {'FINISHED'}


class PMVR_OT_UnassignSelected(bpy.types.Operator):
    bl_idname = "pmvr.unassign_selected"
    bl_label = "Unassign Selected"
    bl_options = {'REGISTER', 'UNDO'}

    # The - of an Objects list: only the unbaked objects of the active layer.
    layer_originals_only: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, properties):
        if properties.layer_originals_only:
            return "Remove the selected objects from this layer"
        return "Take the selected objects out of the pipeline: no layer, unit or additional exports"

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project) if self.layer_originals_only else None
        objects = [
            obj for obj in context.selected_objects
            if hasattr(obj, "pm_vr_pipeline")
            and not obj.get(TAG_GENERATED)
            and (
                not layer
                or (
                    obj.pm_vr_pipeline.render_layer_id == layer.layer_id
                    and obj.pm_vr_pipeline.processing_role == 'EXPORT_ORIGINAL'
                )
            )
        ]
        cleared_exports, left_units = release_objects(objects)
        removed = remove_emptied_units(project, left_units)
        suffix = f"; cleared {cleared_exports} additional export assignment(s)" if cleared_exports else ""
        where = f" from {layer.display_name}" if layer else ""
        _report_change(self, f"Removed {len(objects)} object(s){where}{suffix}{_removed_suffix(removed)}")
        return {'FINISHED'} if objects else {'CANCELLED'}


class PMVR_OT_EditExtraExports(bpy.types.Operator):
    bl_idname = "pmvr.edit_extra_exports"
    bl_label = "Edit Additional Export Layers"
    bl_description = "Include or remove selected sources in the active layer's export file without changing their bake layer"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(
        items=(('ADD', "Include", ""), ('REMOVE', "Remove", "")),
        default='ADD',
    )
    source_id: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        project = getattr(context.scene, "pm_vr_project", None) if context.scene else None
        return bool(project and project.render_layers)

    def execute(self, context):
        project = context.scene.pm_vr_project
        target = active_layer(project)
        sources = (
            [
                obj for obj in bpy.data.objects
                if obj.pm_vr_pipeline.is_registered_source
                and obj.pm_vr_pipeline.source_id == self.source_id
            ]
            if self.source_id
            else selected_source_objects(context)
        )
        if not target or not sources:
            self.report({'WARNING'}, "Select one or more registered source objects")
            return {'CANCELLED'}
        ambiguous_ids = set(duplicate_source_ids())
        if any(source.pm_vr_pipeline.source_id in ambiguous_ids for source in sources):
            self.report({'ERROR'}, "Selected source has a duplicate ID; repair it before editing exports")
            return {'CANCELLED'}
        changed = 0
        skipped = {}
        for source in sources:
            metadata = source.pm_vr_pipeline
            if self.action == 'REMOVE':
                if _remove_extra_export_layer(metadata, target.layer_id):
                    changed += 1
                else:
                    skipped["not assigned"] = skipped.get("not assigned", 0) + 1
                continue
            owner = find_layer(project, metadata.render_layer_id)
            if not metadata.source_id:
                reason = "missing source ID"
            elif not owner or metadata.processing_role == 'UNASSIGNED':
                reason = "no valid primary layer"
            elif owner.layer_id == target.layer_id:
                reason = "already primary"
            elif owner.layer_type != target.layer_type:
                reason = "different layer type"
            elif any(entry.layer_id == target.layer_id for entry in metadata.extra_export_layers):
                reason = "already included"
            else:
                reason = ""
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            metadata.extra_export_layers.add().layer_id = target.layer_id
            changed += 1
        verb = "Included" if self.action == 'ADD' else "Removed"
        suffix = "; " + ", ".join(f"{count} {reason}" for reason, count in skipped.items()) if skipped else ""
        preposition = "in" if self.action == 'ADD' else "from"
        _report_change(self, f"{verb} {changed} source(s) {preposition} {target.display_name}{suffix}")
        return {'FINISHED'}


class PMVR_OT_AddBakeUnit(bpy.types.Operator):
    bl_idname = "pmvr.add_bake_unit"
    bl_label = "New Unit from Selected"
    bl_description = (
        "Put the selected meshes into the active layer, one unit each; objects "
        "from other layers move here. Shift: one shared unit from the whole "
        "selection, merging units they had"
    )
    bl_options = {'REGISTER', 'UNDO'}

    merge_selected: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects and context.scene.pm_vr_project.render_layers) and structure_editable(context)

    def invoke(self, context, event):
        self.merge_selected = event.shift
        return self.execute(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        if layer.layer_type not in BAKE_LAYER_TYPES:
            self.report(
                {'ERROR'},
                f'"{layer.display_name}" is not baked and has no units; '
                "use + in its Objects list",
            )
            return {'CANCELLED'}
        members = []
        kept = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj.get(TAG_GENERATED):
                continue
            ensure_source_id(obj)
            current = find_unit(project, obj.pm_vr_pipeline.bake_unit_id)
            if current and current.render_layer_id == layer.layer_id and not self.merge_selected:
                # Already has its unit here: + never splits or drops a result.
                kept += 1
                continue
            members.append(obj)
        if not members:
            if kept:
                self.report({'INFO'}, f"The selected meshes already have units in {layer.display_name}")
            else:
                self.report({'ERROR'}, "Select one or more source Mesh objects")
            return {'CANCELLED'}
        current_ids = {obj.pm_vr_pipeline.bake_unit_id for obj in members}
        if self.merge_selected and len(current_ids) == 1:
            current = find_unit(project, next(iter(current_ids)))
            if (
                current
                and current.render_layer_id == layer.layer_id
                and set(unit_members(current.unit_id)) == set(members)
            ):
                self.report({'INFO'}, f'The selection already is unit "{current.display_name}"')
                return {'CANCELLED'}

        moved = 0
        left_units = set()
        for obj in members:
            meta = obj.pm_vr_pipeline
            moved += bool(meta.render_layer_id or meta.bake_unit_id)
            if meta.bake_unit_id:
                left_units.add(meta.bake_unit_id)
            meta.render_layer_id = layer.layer_id
            _remove_extra_export_layer(meta, layer.layer_id)
            _drop_incompatible_extra_exports(project, meta, layer)
            meta.processing_role = 'BAKE'
            meta.bake_unit_id = ""

        groups = [members] if self.merge_selected else [[obj] for obj in members]
        for group in groups:
            unit = project.bake_units.add()
            unit.unit_id = new_id()
            unit.artifact_key = unit.unit_id
            unit.render_layer_id = layer.layer_id
            unit.display_name = (
                group[0].name
                if len(group) == 1
                else f"{layer.display_name} Unit {len(project.bake_units)}"
            )
            unit.resolution = suggested_unit_resolution(context, group)
            for obj in group:
                obj.pm_vr_pipeline.bake_unit_id = unit.unit_id
            project.active_bake_unit_index = len(project.bake_units) - 1
        removed = remove_emptied_units(project, left_units)
        mode = "one shared unit" if self.merge_selected else f"{len(groups)} separate unit(s)"
        details = []
        if moved:
            details.append(f"moved {moved} object(s) from other layers or units")
        if kept:
            details.append(f"{kept} already had a unit here")
        suffix = "".join(f"; {item}" for item in details)
        _report_change(self, f"Created {mode}{suffix}{_removed_suffix(removed)}")
        return {'FINISHED'}


def _test_share(project, size):
    return max(64, int(size) * int(project.test_resolution) // 100)


def bake_size(project, unit):
    """Size the queue bakes a unit's Beauty at, and the size of the file it
    keeps: the Bake Resolution (the unit's own when larger), scaled down
    while a test resolution is chosen. Export scales it to the unit's
    resolution, so that can change later without a rebake."""
    return _test_share(project, max(int(project.bake_resolution), int(unit.resolution)))


def bake_margin(project, size, objects=1):
    """Bake margin in pixels for an image of size: the island padding the UVs
    were packed with. Objects of a unit bake one after another into one
    image, and each object's margin paints every pixel it reaches that is
    not its own, a neighbour's island too; with several objects each fills
    half the gap, so neighbours meet in the middle."""
    gap = project.uv_padding * size
    if gap <= 0.0:
        return 0
    return max(1, int(gap / 2 if objects > 1 else gap))


def queue_bake_size(project):
    """What the queue bakes units at (larger units bake at their own)."""
    return _test_share(project, project.bake_resolution)


def lightmap_resolution(project, unit):
    """Lightmaps bake at the unit's resolution (scaled by the test share)."""
    return _test_share(project, unit.resolution)


def test_resolution_label(project):
    return "" if project.test_resolution == '100' else f"{project.test_resolution}%"


def baked_resolution(unit, state):
    return unit.day_baked_resolution if state == 'DAY' else unit.evening_baked_resolution


def reset_test_resolution():
    """A reopened file always bakes at its Setup resolutions."""
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project and project.test_resolution != '100':
            project.test_resolution = '100'


class PMVR_OT_SelectAllUnitsForResolution(bpy.types.Operator):
    bl_idname = "pmvr.select_all_units_for_resolution"
    bl_label = "Select All for Resolution"
    bl_description = (
        "Select every bake unit in the active render layer for batch "
        "resolution changes"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        return bool(
            layer
            and any(
                unit.render_layer_id == layer.layer_id
                for unit in project.bake_units
            )
        )

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        selected = 0
        for unit in project.bake_units:
            if unit.render_layer_id == layer.layer_id:
                unit.batch_selected = True
                selected += 1
        self.report(
            {'INFO'},
            f"Selected {selected} unit(s) for batch resolution",
        )
        return {'FINISHED'}


class PMVR_OT_AssignSelectedToUnit(bpy.types.Operator):
    bl_idname = "pmvr.assign_selected_to_unit"
    bl_label = "Add Selected to Unit"
    bl_description = "Add the selected meshes to the highlighted unit; they share its atlas"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(
            context.selected_objects
            and active_unit(project) is not None
            and structure_editable(context)
        )

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        if not unit:
            self.report({'ERROR'}, "The active render layer has no bake unit")
            return {'CANCELLED'}
        # Unit items move when another unit is removed; keep the ID and name.
        unit_id, unit_name, layer_id = unit.unit_id, unit.display_name, unit.render_layer_id
        count = 0
        left_units = set()
        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj.get(TAG_GENERATED):
                continue
            ensure_source_id(obj)
            meta = obj.pm_vr_pipeline
            if meta.bake_unit_id and meta.bake_unit_id != unit_id:
                left_units.add(meta.bake_unit_id)
            meta.render_layer_id = layer_id
            _remove_extra_export_layer(meta, layer_id)
            meta.processing_role = 'BAKE'
            meta.bake_unit_id = unit_id
            count += 1
        removed = remove_emptied_units(project, left_units)
        _report_change(self, f"Added {count} object(s) to {unit_name}{_removed_suffix(removed)}")
        return {'FINISHED'}


class PMVR_OT_RemoveBakeUnit(bpy.types.Operator):
    bl_idname = "pmvr.remove_bake_unit"
    bl_label = "Remove Bake Unit"
    bl_description = "Remove the highlighted unit and its baked result; its objects become Unassigned"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_unit(context.scene.pm_vr_project) is not None and structure_editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        name = unit.display_name
        generated = remove_unit(project, unit)
        _report_change(self, f'Removed unit "{name}" and {generated} generated object(s)')
        return {'FINISHED'}


class PMVR_OT_RemoveEmptyUnits(bpy.types.Operator):
    bl_idname = "pmvr.remove_empty_units"
    bl_label = "Remove Empty Units"
    bl_description = (
        "Remove every unit (in all layers) that has no objects left, for "
        "example after its objects were deleted"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return structure_editable(context) and bool(context.scene.pm_vr_project.bake_units)

    def execute(self, context):
        project = context.scene.pm_vr_project
        removed = remove_emptied_units(project, [unit.unit_id for unit in project.bake_units])
        if not removed:
            self.report({'INFO'}, "No empty units")
            return {'CANCELLED'}
        _report_change(self, f"Removed {len(removed)} empty unit(s)")
        return {'FINISHED'}


class PMVR_OT_RemoveSelectedFromUnit(bpy.types.Operator):
    bl_idname = "pmvr.remove_selected_from_unit"
    bl_label = "Remove Selected from Unit"
    bl_description = (
        "Take the selected objects out of the highlighted unit and its layer; "
        "a unit left without objects is removed"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(
            context.selected_objects
            and active_unit(project) is not None
            and structure_editable(context)
        )

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        unit_id, unit_name = unit.unit_id, unit.display_name
        objects = [
            obj for obj in context.selected_objects
            if hasattr(obj, "pm_vr_pipeline") and obj.pm_vr_pipeline.bake_unit_id == unit_id
        ]
        release_objects(objects)
        removed = remove_emptied_units(project, [unit_id])
        _report_change(self, f"Removed {len(objects)} object(s) from {unit_name}{_removed_suffix(removed)}")
        return {'FINISHED'} if objects else {'CANCELLED'}


def selected_unit_ids(context):
    source_index = sources_by_id()
    unit_ids = []
    for selected in context.selected_objects:
        obj = selected
        if selected.get(TAG_GENERATED):
            obj = source_index.get(selected.get(TAG_SOURCE_ID, ""))
        if not obj or not hasattr(obj, "pm_vr_pipeline"):
            continue
        unit_id = obj.pm_vr_pipeline.bake_unit_id
        if unit_id and unit_id not in unit_ids:
            unit_ids.append(unit_id)
    return unit_ids


class PMVR_OT_QueueSelectedUnits(bpy.types.Operator):
    bl_idname = "pmvr.queue_selected_units"
    bl_label = "Add Selected Units"
    bl_description = "Add complete bake units represented by selected source or generated objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        project = context.scene.pm_vr_project
        existing = {entry.unit_id for entry in project.bake_queue}
        added = 0
        for unit_id in selected_unit_ids(context):
            if unit_id in existing or not find_unit(project, unit_id):
                continue
            project.bake_queue.add().unit_id = unit_id
            existing.add(unit_id)
            added += 1
        if added:
            project.active_bake_queue_index = len(project.bake_queue) - 1
        self.report({'INFO'}, f"Added {added} complete unit(s) to the queue")
        return {'FINISHED'} if added else {'CANCELLED'}


class PMVR_OT_QueueLayerUnits(bpy.types.Operator):
    bl_idname = "pmvr.queue_layer_units"
    bl_label = "Queue Layer Units"
    bl_description = (
        "Add every unit of the active render layer to the bake queue, in list "
        "order; units already queued and units without objects are skipped"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        layer = active_layer(context.scene.pm_vr_project)
        return bool(layer and layer.layer_type in BAKE_LAYER_TYPES)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        queued = {entry.unit_id for entry in project.bake_queue}
        occupied = units_with_members()
        added = already = empty = 0
        for unit in project.bake_units:
            if unit.render_layer_id != layer.layer_id:
                continue
            if unit.unit_id in queued:
                already += 1
            elif unit.unit_id not in occupied:
                empty += 1
            else:
                project.bake_queue.add().unit_id = unit.unit_id
                queued.add(unit.unit_id)
                added += 1
        if added:
            project.active_bake_queue_index = len(project.bake_queue) - 1
        message = f'Queued {added} unit(s) of "{layer.display_name}"'
        if already:
            message += f", {already} already queued"
        if empty:
            message += f", {empty} without objects skipped"
        self.report({'INFO'} if added else {'WARNING'}, message)
        return {'FINISHED'} if added else {'CANCELLED'}


class PMVR_OT_QueueActiveUnit(bpy.types.Operator):
    bl_idname = "pmvr.queue_active_unit"
    bl_label = "Add Active Unit"

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        if not unit:
            return {'CANCELLED'}
        if any(entry.unit_id == unit.unit_id for entry in project.bake_queue):
            self.report({'INFO'}, "Unit is already queued")
            return {'CANCELLED'}
        project.bake_queue.add().unit_id = unit.unit_id
        project.active_bake_queue_index = len(project.bake_queue) - 1
        return {'FINISHED'}


class PMVR_OT_RemoveQueueEntry(bpy.types.Operator):
    bl_idname = "pmvr.remove_queue_entry"
    bl_label = "Remove Queue Entry"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        project = context.scene.pm_vr_project
        if not project.bake_queue:
            return {'CANCELLED'}
        index = min(project.active_bake_queue_index, len(project.bake_queue) - 1)
        project.bake_queue.remove(index)
        project.active_bake_queue_index = min(index, max(0, len(project.bake_queue) - 1))
        return {'FINISHED'}


class PMVR_OT_ClearBakeQueue(bpy.types.Operator):
    bl_idname = "pmvr.clear_bake_queue"
    bl_label = "Clear Queue"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.pm_vr_project.bake_queue.clear()
        return {'FINISHED'}


class PMVR_OT_MoveQueueEntry(bpy.types.Operator):
    bl_idname = "pmvr.move_queue_entry"
    bl_label = "Move Queue Entry"
    direction: bpy.props.EnumProperty(items=(('UP', "Up", ""), ('DOWN', "Down", "")))

    def execute(self, context):
        project = context.scene.pm_vr_project
        if len(project.bake_queue) < 2:
            return {'CANCELLED'}
        index = min(project.active_bake_queue_index, len(project.bake_queue) - 1)
        target = index - 1 if self.direction == 'UP' else index + 1
        if target < 0 or target >= len(project.bake_queue):
            return {'CANCELLED'}
        project.bake_queue.move(index, target)
        project.active_bake_queue_index = target
        return {'FINISHED'}


class PMVR_OT_MoveRenderLayer(bpy.types.Operator):
    bl_idname = "pmvr.move_render_layer"
    bl_label = "Move Render Layer"
    bl_description = "Move the highlighted render layer up or down the list"
    bl_options = {'REGISTER', 'UNDO'}
    direction: bpy.props.EnumProperty(items=(('UP', "Up", ""), ('DOWN', "Down", "")))

    @classmethod
    def poll(cls, context):
        return len(context.scene.pm_vr_project.render_layers) > 1 and structure_editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        index = min(project.active_render_layer_index, len(project.render_layers) - 1)
        target = index - 1 if self.direction == 'UP' else index + 1
        if target < 0 or target >= len(project.render_layers):
            return {'CANCELLED'}
        project.render_layers.move(index, target)
        project.active_render_layer_index = target
        return {'FINISHED'}


class PMVR_OT_SetLightingState(bpy.types.Operator):
    bl_idname = "pmvr.set_lighting_state"
    bl_label = "Set Lighting State"
    bl_description = "Show this lighting state: its lights, world and baked results"
    state: bpy.props.EnumProperty(items=(('DAY', "Day", ""), ('EVENING', "Evening", "")))

    @classmethod
    def poll(cls, context):
        return structure_editable(context)

    def execute(self, context):
        try:
            activate_state(context, self.state)
        except PipelineStateError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        # The queue switches states through activate_state() alone; only this
        # button also changes what the baked results show.
        preview_state(context.scene.pm_vr_project)
        self.report({'INFO'}, f"{self.state.title()} lighting is active")
        return {'FINISHED'}


class PMVR_OT_ValidatePipeline(bpy.types.Operator):
    bl_idname = "pmvr.validate_pipeline"
    bl_label = "Validate Pipeline"

    def execute(self, context):
        issues = validate_all(context)
        errors = sum(issue.severity == 'ERROR' for issue in issues)
        warnings = sum(issue.severity == 'WARNING' for issue in issues)
        summary = "Pipeline valid" if not issues else f"{errors} error(s), {warnings} warning(s), {len(issues) - errors - warnings} info"
        context.scene.pm_vr_project.last_validation_summary = summary
        log.info("Validation", summary)
        for issue in issues:
            suffix = f' [{issue.object_name}]' if issue.object_name else ""
            log.write("Validation", f"{issue.message}{suffix}", issue.severity)
        self.report({'ERROR'} if errors else {'INFO'}, summary + ("; see the PMVR Pipeline Log" if issues else ""))
        return {'CANCELLED'} if errors else {'FINISHED'}


class PMVR_OT_RegisterDuplicateAsNew(bpy.types.Operator):
    bl_idname = "pmvr.register_duplicate_as_new"
    bl_label = "Register Selected Duplicates as New Sources"
    bl_description = "Give every selected registered source a new stable identity"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in context.selected_objects:
            if obj.get(TAG_GENERATED):
                continue
            meta = obj.pm_vr_pipeline
            if meta.is_registered_source:
                meta.source_id = new_id()
                meta.bake_unit_id = ""
                meta.extra_export_layers.clear()
                count += 1
        _report_change(self, f"Registered {count} selected object(s) as new sources")
        return {'FINISHED'}


def unassigned_visible_objects(context):
    """Objects inside Source Root that are visible now and belong to no layer.
    Lights are left out: they are lighting, never exported."""
    project = context.scene.pm_vr_project
    root = project.source_root_collection
    if not root:
        return []
    layer_ids = {layer.layer_id for layer in project.render_layers}
    objects = []
    for obj in root.all_objects:
        if obj.type in {'LIGHT', 'LIGHT_PROBE'} or obj.get(TAG_GENERATED):
            continue
        meta = obj.pm_vr_pipeline
        if (
            meta.is_registered_source
            and meta.render_layer_id in layer_ids
            and meta.processing_role != 'UNASSIGNED'
        ):
            continue
        if obj.visible_get(view_layer=context.view_layer):
            objects.append(obj)
    return objects


class PMVR_OT_SelectPipelineItems(bpy.types.Operator):
    bl_idname = "pmvr.select_pipeline_items"
    bl_label = "Select Pipeline Items"
    target: bpy.props.EnumProperty(items=(
        ('LAYER_SOURCES', "Layer Sources", "Select the objects of the active layer"),
        ('LAYER_EXPORT_GUESTS', "Additional Export Objects", "Select the additional objects of this export"),
        ('UNIT_SOURCES', "Unit Sources", "Select the objects of the highlighted unit"),
        ('UNIT_GENERATED', "Unit Generated", "Select the baked result of the highlighted unit"),
        ('UNASSIGNED', "Unassigned",
         "Select the objects visible now inside Source Root that belong to no layer (lights excluded)"),
    ))

    @classmethod
    def description(cls, _context, properties):
        return cls.bl_rna.properties["target"].enum_items[properties.target].description

    def execute(self, context):
        project = context.scene.pm_vr_project
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.selected_objects:
            obj.select_set(False)
        if self.target == 'LAYER_SOURCES':
            layer = active_layer(project)
            objects = layer_members(layer.layer_id) if layer else []
        elif self.target == 'LAYER_EXPORT_GUESTS':
            layer = active_layer(project)
            objects = extra_export_members(layer.layer_id) if layer else []
        elif self.target == 'UNIT_SOURCES':
            unit = active_unit(project)
            objects = unit_members(unit.unit_id) if unit else []
        elif self.target == 'UNIT_GENERATED':
            unit = active_unit(project)
            objects = [
                obj for obj in bpy.data.objects
                if unit
                and obj.get(TAG_GENERATED)
                and obj.get(TAG_UNIT_ID) == unit.unit_id
                and obj.get(TAG_MODE) == project.bake_mode
            ]
        else:
            objects = unassigned_visible_objects(context)
        selected = 0
        for obj in objects:
            try:
                obj.select_set(True)
                context.view_layer.objects.active = obj
                selected += 1
            except RuntimeError:
                pass
        self.report({'INFO'}, f"Selected {selected} object(s)")
        return {'FINISHED'}


def _set_hidden(obj, hidden):
    try:
        obj.hide_set(hidden)
    except RuntimeError:
        # Not in the View Layer, for example inside a disabled collection.
        pass


def preview_sources(project):
    """Show or hide registered sources (Show Sources)."""
    for obj in bpy.data.objects:
        if (
            not obj.get(TAG_GENERATED)
            and hasattr(obj, "pm_vr_pipeline")
            and obj.pm_vr_pipeline.is_registered_source
        ):
            _set_hidden(obj, not project.show_sources)


def preview_generated(project):
    """Show or hide baked results of the active bake mode (Show Generated)."""
    for obj in bpy.data.objects:
        if obj.get(TAG_GENERATED):
            _set_hidden(obj, not (project.show_generated and obj.get(TAG_MODE) == project.bake_mode))


def preview_state(project):
    """Let baked results show the materials of the active lighting state."""
    for unit in project.bake_units:
        bind_generated_state(unit, project.active_lighting_state, project.bake_mode)


class PMVR_OT_TogglePreview(bpy.types.Operator):
    bl_idname = "pmvr.apply_preview_visibility"
    bl_label = "Apply Preview Visibility"
    bl_description = "Apply the preview switches and show the active lighting state on baked results"

    def execute(self, context):
        project = context.scene.pm_vr_project
        preview_state(project)
        preview_generated(project)
        preview_sources(project)
        return {'FINISHED'}


class PMVR_OT_OpenLogFolder(bpy.types.Operator):
    bl_idname = "pmvr.open_log_folder"
    bl_label = "Open Log Folder"
    bl_description = "Open the folder with the PM VR log files for this .blend"

    def execute(self, _context):
        folder = os.path.dirname(log.log_file_path())
        os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class PMVR_OT_ProjectSettings(bpy.types.Operator):
    bl_idname = "pmvr.project_settings"
    bl_label = "PM VR Project Settings"

    def draw(self, context):
        project = context.scene.pm_vr_project
        layout = self.layout
        layout.prop(project, "source_root_collection")
        day = layout.box()
        day.label(text="Day", icon='LIGHT_SUN')
        day.prop(project, "day_lighting_collection")
        day.prop(project, "day_world")
        evening = layout.box()
        evening.label(text="Evening", icon='LIGHT')
        evening.prop(project, "evening_lighting_collection")
        evening.prop(project, "evening_world")
        bake = layout.box()
        bake.label(text="Bake Defaults", icon='RENDER_STILL')
        bake.prop(project, "bake_resolution")
        bake.prop(project, "default_unit_resolution")
        bake.prop(context.scene, "pm_vr_target_td", text="Target TD px/cm")
        bake.prop(project, "uv_padding")
        bake.prop(project, "cycles_samples")
        bake.prop(project, "beauty_output_directory")
        bake.prop(project, "lightmap_output_directory")
        export = layout.box()
        export.label(text="Export", icon='EXPORT')
        export.prop(project, "usdz_output_directory")
        export.prop(project, "glb_output_directory")
        probes = layout.box()
        probes.label(text="Probes", icon='WORLD')
        probes.prop(project, "probe_output_directory")
        probes.prop(project, "probe_width")
        log_box = layout.box()
        log_box.label(text="Log", icon='TEXT')
        log_box.label(text=log.log_file_path())
        log_box.operator("pmvr.open_log_folder", icon='FILEBROWSER')
        layout.label(text=f"Schema {project.schema_version}  •  Project {project.project_id[:8] or 'not initialized'}")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, _context):
        return {'FINISHED'}


CLASSES = (
    PMVR_OT_InitializeProject,
    PMVR_OT_AddRenderLayer,
    PMVR_OT_RemoveRenderLayer,
    PMVR_OT_AssignSelectedToLayer,
    PMVR_OT_UnassignSelected,
    PMVR_OT_EditExtraExports,
    PMVR_OT_AddBakeUnit,
    PMVR_OT_SelectAllUnitsForResolution,
    PMVR_OT_AssignSelectedToUnit,
    PMVR_OT_RemoveBakeUnit,
    PMVR_OT_RemoveEmptyUnits,
    PMVR_OT_RemoveSelectedFromUnit,
    PMVR_OT_QueueSelectedUnits,
    PMVR_OT_QueueLayerUnits,
    PMVR_OT_QueueActiveUnit,
    PMVR_OT_RemoveQueueEntry,
    PMVR_OT_ClearBakeQueue,
    PMVR_OT_MoveQueueEntry,
    PMVR_OT_MoveRenderLayer,
    PMVR_OT_SetLightingState,
    PMVR_OT_ValidatePipeline,
    PMVR_OT_RegisterDuplicateAsNew,
    PMVR_OT_SelectPipelineItems,
    PMVR_OT_TogglePreview,
    PMVR_OT_OpenLogFolder,
    PMVR_OT_ProjectSettings,
)
