"""Setup, identity, queue, state, validation and preview operators."""

import math

import bpy

from ..scene_diagnostics import get_target_td, measure_texel_areas
from .constants import (
    GENERATED_COLLECTION,
    LAYER_COLOR_PALETTE,
    RESOLUTION_ITEMS,
    ROLE_ITEMS,
    SCHEMA_VERSION,
    BAKE_LAYER_TYPES,
    TAG_GENERATED,
    TAG_MODE,
    TAG_SOURCE_ID,
    TAG_UNIT_ID,
)
from .identity import (
    ensure_project_id,
    ensure_source_id,
    find_layer,
    find_unit,
    layer_members,
    new_id,
    safe_stem,
    unit_members,
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
    log.info(
        "Setup",
        f"Suggested {chosen}px for {len(objects)} object(s); "
        f"required {required_size:.0f}px at {target_td:.1f}px/cm",
    )
    return str(chosen)


def active_layer(project):
    if not project.render_layers:
        return None
    return project.render_layers[min(project.active_render_layer_index, len(project.render_layers) - 1)]


def active_unit(project):
    if not project.bake_units:
        return None
    layer = active_layer(project)
    index = min(project.active_bake_unit_index, len(project.bake_units) - 1)
    unit = project.bake_units[index]
    if layer and unit.render_layer_id == layer.layer_id:
        return unit
    if layer:
        for index, candidate in enumerate(project.bake_units):
            if candidate.render_layer_id == layer.layer_id:
                project.active_bake_unit_index = index
                return candidate
    return None


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
        self.report({'INFO'}, "PM VR pipeline project initialized")
        return {'FINISHED'}


class PMVR_OT_AddRenderLayer(bpy.types.Operator):
    bl_idname = "pmvr.add_render_layer"
    bl_label = "Add Render Layer"
    bl_options = {'REGISTER', 'UNDO'}

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
        return bool(context.scene.pm_vr_project.render_layers)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        if layer_members(layer.layer_id):
            self.report({'ERROR'}, "Unassign layer objects before removing this layer")
            return {'CANCELLED'}
        if any(unit.render_layer_id == layer.layer_id for unit in project.bake_units):
            self.report({'ERROR'}, "Remove this layer's bake units first")
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
    def poll(cls, context):
        return bool(context.selected_objects and context.scene.pm_vr_project.render_layers)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        role = 'EXPORT_ORIGINAL' if layer.layer_type not in BAKE_LAYER_TYPES else self.role
        assigned = 0
        for obj in context.selected_objects:
            if obj.get(TAG_GENERATED):
                continue
            metadata = obj.pm_vr_pipeline
            ensure_source_id(obj)
            metadata.render_layer_id = layer.layer_id
            metadata.processing_role = 'EXPORT_ORIGINAL' if obj.type == 'EMPTY' else role
            if metadata.processing_role != 'BAKE':
                metadata.bake_unit_id = ""
            assigned += 1
        self.report({'INFO'}, f"Assigned {assigned} source object(s) to {layer.display_name}")
        return {'FINISHED'}


class PMVR_OT_UnassignSelected(bpy.types.Operator):
    bl_idname = "pmvr.unassign_selected"
    bl_label = "Unassign Selected"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in context.selected_objects:
            if not hasattr(obj, "pm_vr_pipeline"):
                continue
            meta = obj.pm_vr_pipeline
            meta.render_layer_id = ""
            meta.bake_unit_id = ""
            meta.processing_role = 'UNASSIGNED'
            count += 1
        self.report({'INFO'}, f"Unassigned {count} object(s)")
        return {'FINISHED'}


class PMVR_OT_AddBakeUnit(bpy.types.Operator):
    bl_idname = "pmvr.add_bake_unit"
    bl_label = "New Unit from Selected"
    bl_description = "Create one unit per selected Mesh; hold Shift to create one shared unit"
    bl_options = {'REGISTER', 'UNDO'}

    merge_selected: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects and context.scene.pm_vr_project.render_layers)

    def invoke(self, context, event):
        self.merge_selected = event.shift
        return self.execute(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        layer = active_layer(project)
        if layer.layer_type not in BAKE_LAYER_TYPES:
            self.report({'ERROR'}, "Export Original layers cannot contain bake units")
            return {'CANCELLED'}
        members = []
        already_assigned = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj.get(TAG_GENERATED):
                continue
            meta = obj.pm_vr_pipeline
            ensure_source_id(obj)
            if meta.bake_unit_id and find_unit(project, meta.bake_unit_id):
                already_assigned += 1
                continue
            meta.render_layer_id = layer.layer_id
            meta.processing_role = 'BAKE'
            members.append(obj)
        if not members:
            message = "Selected Mesh objects already belong to bake units" if already_assigned else "Select one or more source Mesh objects"
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

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
        mode = "one shared unit" if self.merge_selected else f"{len(groups)} separate unit(s)"
        suffix = f"; skipped {already_assigned} already assigned" if already_assigned else ""
        self.report({'INFO'}, f"Created {mode}{suffix}")
        return {'FINISHED'}


class PMVR_OT_ScaleQueuedResolution(bpy.types.Operator):
    bl_idname = "pmvr.scale_queued_resolution"
    bl_label = "Scale Queued Resolution"
    bl_description = "Halve or double the resolution of every unit in the bake queue"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        items=(('HALF', "Half", ""), ('DOUBLE', "Double", "")),
        default='HALF',
    )

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit_ids = {entry.unit_id for entry in project.bake_queue}
        changed = 0
        selected_state = [unit.batch_selected for unit in project.bake_units]
        try:
            for unit in project.bake_units:
                unit.batch_selected = False
            for unit in project.bake_units:
                if unit.unit_id not in unit_ids:
                    continue
                current = int(unit.resolution)
                index = SUPPORTED_RESOLUTIONS.index(current)
                target_index = (
                    max(0, index - 1)
                    if self.direction == 'HALF'
                    else min(len(SUPPORTED_RESOLUTIONS) - 1, index + 1)
                )
                target = SUPPORTED_RESOLUTIONS[target_index]
                if target != current:
                    unit.resolution = str(target)
                    changed += 1
        finally:
            for unit, was_selected in zip(project.bake_units, selected_state):
                unit.batch_selected = was_selected
        symbol = "÷2" if self.direction == 'HALF' else "×2"
        log.info("Bake", f"Queue resolution {symbol}: {changed} unit(s) changed")
        self.report({'INFO'}, f"Changed {changed} queued unit(s)")
        return {'FINISHED'} if unit_ids else {'CANCELLED'}


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
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects and context.scene.pm_vr_project.bake_units)

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        count = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj.get(TAG_GENERATED):
                continue
            ensure_source_id(obj)
            meta = obj.pm_vr_pipeline
            meta.render_layer_id = unit.render_layer_id
            meta.processing_role = 'BAKE'
            meta.bake_unit_id = unit.unit_id
            count += 1
        self.report({'INFO'}, f"Added {count} object(s) to {unit.display_name}")
        return {'FINISHED'}


class PMVR_OT_RemoveBakeUnit(bpy.types.Operator):
    bl_idname = "pmvr.remove_bake_unit"
    bl_label = "Remove Bake Unit"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_unit(context.scene.pm_vr_project) is not None

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        generated_objects = [
            obj for obj in bpy.data.objects
            if obj.get(TAG_GENERATED) and obj.get(TAG_UNIT_ID) == unit.unit_id
        ]
        for generated in generated_objects:
            mesh = generated.data if generated.type == 'MESH' else None
            bpy.data.objects.remove(generated, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for material in list(bpy.data.materials):
            if (
                material.get(TAG_GENERATED)
                and material.get(TAG_UNIT_ID) == unit.unit_id
                and material.users == 0
            ):
                bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            if (
                image.get(TAG_GENERATED)
                and image.get(TAG_UNIT_ID) == unit.unit_id
                and image.users == 0
            ):
                bpy.data.images.remove(image)
        for obj in unit_members(unit.unit_id):
            obj.pm_vr_pipeline.bake_unit_id = ""
            obj.pm_vr_pipeline.processing_role = 'UNASSIGNED'
        for index in reversed(range(len(project.bake_queue))):
            if project.bake_queue[index].unit_id == unit.unit_id:
                project.bake_queue.remove(index)
        for index in reversed(range(len(project.build_records))):
            if project.build_records[index].unit_id == unit.unit_id:
                project.build_records.remove(index)
        index = project.active_bake_unit_index
        project.bake_units.remove(index)
        project.active_bake_unit_index = min(index, max(0, len(project.bake_units) - 1))
        return {'FINISHED'}


class PMVR_OT_RemoveSelectedFromUnit(bpy.types.Operator):
    bl_idname = "pmvr.remove_selected_from_unit"
    bl_label = "Remove Selected from Unit"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        if not unit:
            return {'CANCELLED'}
        count = 0
        for obj in context.selected_objects:
            meta = obj.pm_vr_pipeline
            if meta.bake_unit_id == unit.unit_id:
                meta.bake_unit_id = ""
                meta.processing_role = 'UNASSIGNED'
                count += 1
        self.report({'INFO'}, f"Removed {count} object(s) from {unit.display_name}")
        return {'FINISHED'} if count else {'CANCELLED'}


def selected_unit_ids(context):
    source_by_id = {
        obj.pm_vr_pipeline.source_id: obj
        for obj in bpy.data.objects
        if hasattr(obj, "pm_vr_pipeline") and obj.pm_vr_pipeline.source_id
    }
    unit_ids = []
    for selected in context.selected_objects:
        obj = selected
        if selected.get(TAG_GENERATED):
            obj = source_by_id.get(selected.get(TAG_SOURCE_ID, ""))
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


class PMVR_OT_SetLightingState(bpy.types.Operator):
    bl_idname = "pmvr.set_lighting_state"
    bl_label = "Set Lighting State"
    state: bpy.props.EnumProperty(items=(('DAY', "Day", ""), ('EVENING', "Evening", "")))

    def execute(self, context):
        try:
            activate_state(context, self.state)
        except PipelineStateError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
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
        for issue in issues:
            suffix = f' [{issue.object_name}]' if issue.object_name else ""
            print(f"[PM VR][Validation][{issue.severity}] {issue.message}{suffix}")
        self.report({'ERROR'} if errors else {'INFO'}, summary + ("; see console" if issues else ""))
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
                count += 1
        self.report({'INFO'}, f"Registered {count} selected object(s) as new sources")
        return {'FINISHED'}


class PMVR_OT_SelectPipelineItems(bpy.types.Operator):
    bl_idname = "pmvr.select_pipeline_items"
    bl_label = "Select Pipeline Items"
    target: bpy.props.EnumProperty(items=(
        ('LAYER_SOURCES', "Layer Sources", ""),
        ('UNIT_SOURCES', "Unit Sources", ""),
        ('UNIT_GENERATED', "Unit Generated", ""),
        ('UNASSIGNED', "Unassigned", ""),
    ))

    def execute(self, context):
        project = context.scene.pm_vr_project
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.selected_objects:
            obj.select_set(False)
        if self.target == 'LAYER_SOURCES':
            layer = active_layer(project)
            objects = layer_members(layer.layer_id) if layer else []
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
            objects = [obj for obj in bpy.data.objects if hasattr(obj, "pm_vr_pipeline") and obj.pm_vr_pipeline.processing_role == 'UNASSIGNED']
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


class PMVR_OT_TogglePreview(bpy.types.Operator):
    bl_idname = "pmvr.apply_preview_visibility"
    bl_label = "Apply Preview Visibility"
    bl_description = "Apply source/generated visibility switches without changing authored collection membership"

    def execute(self, context):
        from .bake import bind_generated_state

        project = context.scene.pm_vr_project
        for unit in project.bake_units:
            bind_generated_state(unit, project.active_lighting_state, project.bake_mode)
        for obj in bpy.data.objects:
            if obj.get(TAG_GENERATED):
                show_this_mode = obj.get(TAG_MODE) == project.bake_mode
                obj.hide_set(not (project.show_generated and show_this_mode))
            elif hasattr(obj, "pm_vr_pipeline") and obj.pm_vr_pipeline.is_registered_source:
                obj.hide_set(not project.show_sources)
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
        bake.prop(project, "default_unit_resolution")
        bake.prop(context.scene, "pm_vr_target_td", text="Target TD px/cm")
        bake.prop(project, "margin")
        bake.prop(project, "cycles_samples")
        bake.prop(project, "beauty_output_directory")
        bake.prop(project, "lightmap_output_directory")
        export = layout.box()
        export.label(text="Export", icon='EXPORT')
        export.prop(project, "usdz_output_directory")
        export.prop(project, "glb_output_directory")
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
    PMVR_OT_AddBakeUnit,
    PMVR_OT_ScaleQueuedResolution,
    PMVR_OT_SelectAllUnitsForResolution,
    PMVR_OT_AssignSelectedToUnit,
    PMVR_OT_RemoveBakeUnit,
    PMVR_OT_RemoveSelectedFromUnit,
    PMVR_OT_QueueSelectedUnits,
    PMVR_OT_QueueActiveUnit,
    PMVR_OT_RemoveQueueEntry,
    PMVR_OT_ClearBakeQueue,
    PMVR_OT_MoveQueueEntry,
    PMVR_OT_SetLightingState,
    PMVR_OT_ValidatePipeline,
    PMVR_OT_RegisterDuplicateAsNew,
    PMVR_OT_SelectPipelineItems,
    PMVR_OT_TogglePreview,
    PMVR_OT_ProjectSettings,
)
