"""Persistent collection-based batch export to USDZ and GLB."""

from datetime import datetime
import os
import re

import bpy


UI_CATEGORY = "COLLECTION_EXPORT"
DEFAULT_USDZ_DIRECTORY = "//USDZ/"
DEFAULT_GLB_DIRECTORY = "//GLB/"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_export_name(value):
    """Return a cross-platform-safe filename stem."""
    name = (value or "").strip()
    for extension in (".usdz", ".glb"):
        if name.lower().endswith(extension):
            name = name[:-len(extension)]
            break
    name = INVALID_FILENAME_CHARS.sub("_", name).rstrip(" .")
    if name.upper() in WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    return name


def selected_outliner_collections(context):
    selected_ids = getattr(context, "selected_ids", ()) or ()
    return [item for item in selected_ids if isinstance(item, bpy.types.Collection)]


def add_collection_to_export_list(items, collection):
    """Add one collection, rolling back cleanly when Blender rejects its pointer."""
    if not collection:
        return False, "missing collection"
    if getattr(collection, "is_embedded_data", False):
        return False, "Scene Collection cannot be stored as an export item"

    item = items.add()
    try:
        item.collection = collection
        item.export_name = collection.name
    except (RuntimeError, TypeError) as exc:
        items.remove(len(items) - 1)
        return False, str(exc)
    return True, ""


def export_filepath(directory, export_name, extension):
    return os.path.join(directory, f"{sanitize_export_name(export_name)}{extension}")


def temporary_filepath(filepath):
    stem, extension = os.path.splitext(filepath)
    return f"{stem}.pmvr_tmp{extension}"


def export_usdz(collection, filepath):
    return bpy.ops.wm.usd_export(
        filepath=filepath,
        check_existing=False,
        selected_objects_only=False,
        collection=collection.name,
        export_animation=False,
        export_custom_properties=True,
        custom_properties_namespace="userProperties",
        author_blender_name=True,
        allow_unicode=True,
        relative_paths=True,
        convert_orientation=False,
        convert_scene_units='METERS',
        xform_op_mode='TRS',
        evaluation_mode='RENDER',
        export_meshes=True,
        export_lights=False,
        export_cameras=False,
        export_curves=False,
        export_points=False,
        export_volumes=False,
        export_hair=False,
        export_uvmaps=True,
        rename_uvmaps=True,
        export_normals=True,
        merge_parent_xform=False,
        triangulate_meshes=False,
        export_subdivision='BEST_MATCH',
        export_materials=True,
        generate_preview_surface=True,
        generate_materialx_network=False,
        export_textures_mode='NEW',
        overwrite_textures=False,
        usdz_downscale_size='KEEP',
    )


def export_glb(collection, filepath):
    return bpy.ops.export_scene.gltf(
        filepath=filepath,
        check_existing=False,
        export_format='GLB',
        use_selection=False,
        use_visible=False,
        use_renderable=False,
        use_active_collection=False,
        use_active_scene=False,
        collection=collection.name,
        export_extras=False,
        export_cameras=False,
        export_lights=False,
        export_yup=True,
        export_apply=False,
        export_texcoords=True,
        export_normals=True,
        export_tangents=False,
        export_attributes=False,
        use_mesh_edges=False,
        use_mesh_vertices=False,
        export_shared_accessors=False,
        export_materials='EXPORT',
        export_image_format='WEBP',
        export_image_quality=75,
        export_image_add_webp=False,
        export_image_webp_fallback=False,
        export_morph=False,
        export_skins=False,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
        export_draco_color_quantization=10,
        export_draco_generic_quantization=12,
        export_meshopt_compression_enable=False,
        export_animations=False,
    )


class ExportValidationError(RuntimeError):
    pass


def build_export_jobs(scene, format_names):
    jobs = []
    for format_name in format_names:
        property_name = "export_usdz" if format_name == 'USDZ' else "export_glb"
        directory_property = (
            "pm_vr_usdz_export_directory"
            if format_name == 'USDZ'
            else "pm_vr_glb_export_directory"
        )
        directory_value = getattr(scene, directory_property)
        items = [
            item for item in scene.pm_vr_export_collections
            if getattr(item, property_name)
        ]
        if not items:
            continue
        if directory_value.startswith("//") and not bpy.data.filepath:
            raise ExportValidationError(
                f"Save the Blender file before using the relative {format_name} path"
            )
        directory = bpy.path.abspath(directory_value)
        if not directory:
            raise ExportValidationError(f"Choose a {format_name} export directory")
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise ExportValidationError(
                f"Cannot create the {format_name} directory: {exc}"
            ) from exc

        invalid = [
            item for item in items
            if not item.collection or not sanitize_export_name(item.export_name)
        ]
        if invalid:
            raise ExportValidationError(
                f"{format_name} rows contain a missing collection or empty name"
            )
        names = [sanitize_export_name(item.export_name).casefold() for item in items]
        if len(names) != len(set(names)):
            raise ExportValidationError(
                f"{format_name} rows contain duplicate export names"
            )
        jobs.extend((format_name, directory, item) for item in items)

    if not jobs:
        raise ExportValidationError("No collections are checked for the requested format")
    return jobs


def perform_export_job(format_name, directory, item):
    extension = ".usdz" if format_name == 'USDZ' else ".glb"
    filepath = export_filepath(directory, item.export_name, extension)
    temp_path = temporary_filepath(filepath)
    status_property = f"last_{format_name.lower()}_status"
    path_property = f"last_{format_name.lower()}_path"
    time_property = f"last_{format_name.lower()}_time"
    setattr(item, status_property, "Exporting")

    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        exporter = export_usdz if format_name == 'USDZ' else export_glb
        result = exporter(item.collection, temp_path)
        if 'FINISHED' not in result or not os.path.exists(temp_path):
            raise RuntimeError(f"Blender did not produce a {format_name} file")
        os.replace(temp_path, filepath)
        setattr(item, status_property, "Exported")
        setattr(item, path_property, filepath)
        setattr(item, time_property, datetime.now().isoformat(timespec="seconds"))
        print(f'[PM VR][{format_name}] Exported "{item.collection.name}" -> "{filepath}"')
        return True
    except Exception as exc:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        setattr(item, status_property, f"Failed: {exc}")
        print(f'[PM VR][{format_name}] Failed "{item.collection.name}": {exc}')
        return False


def tag_redraw_all(window_manager):
    for window in window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class PMVR_ExportCollectionItem(bpy.types.PropertyGroup):
    collection: bpy.props.PointerProperty(
        name="Collection",
        description="Blender collection exported by this row",
        type=bpy.types.Collection,
    )
    export_usdz: bpy.props.BoolProperty(
        name="USDZ",
        description="Include this collection when exporting checked USDZ files",
        default=True,
    )
    export_glb: bpy.props.BoolProperty(
        name="GLB",
        description="Include this collection when exporting checked GLB files",
        default=True,
    )
    export_name: bpy.props.StringProperty(
        name="Export Name",
        description="Persistent filename shared by USDZ and GLB; extension is added automatically",
    )
    last_usdz_status: bpy.props.StringProperty(name="Last USDZ Status")
    last_glb_status: bpy.props.StringProperty(name="Last GLB Status")
    last_usdz_path: bpy.props.StringProperty(name="Last USDZ Path", subtype='FILE_PATH')
    last_glb_path: bpy.props.StringProperty(name="Last GLB Path", subtype='FILE_PATH')
    last_usdz_time: bpy.props.StringProperty(name="Last USDZ Time")
    last_glb_time: bpy.props.StringProperty(name="Last GLB Time")


class PMVR_UL_ExportCollections(bpy.types.UIList):
    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index,
    ):
        row = layout.row(align=True)
        row.prop(item, "export_usdz", text="USDZ", toggle=True)
        row.prop(item, "export_glb", text="GLB", toggle=True)
        if item.collection:
            row.label(text=item.collection.name, icon='OUTLINER_COLLECTION')
        else:
            row.label(text="Missing collection", icon='ERROR')
        row.prop(item, "export_name", text="")


class PMVR_OT_AddExportCollections(bpy.types.Operator):
    bl_idname = "pm_vr.add_export_collections"
    bl_label = "Add to PM VR Export List"
    bl_description = "Add selected Outliner collections to the persistent export list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(selected_outliner_collections(context))

    def execute(self, context):
        items = context.scene.pm_vr_export_collections
        existing = {item.collection.as_pointer() for item in items if item.collection}
        added = 0
        skipped = []

        for collection in selected_outliner_collections(context):
            if collection.as_pointer() in existing:
                skipped.append((collection.name, "already in the list"))
                continue
            was_added, reason = add_collection_to_export_list(items, collection)
            if not was_added:
                skipped.append((collection.name, reason))
                continue
            existing.add(collection.as_pointer())
            added += 1

        if added:
            context.scene.pm_vr_export_collection_index = len(items) - 1
        for name, reason in skipped:
            print(f'[PM VR][Export List] Skipped "{name}": {reason}')
        if skipped:
            self.report(
                {'WARNING'},
                f"Added {added} collection(s), skipped {len(skipped)}; see the console",
            )
        else:
            self.report({'INFO'}, f"Added {added} collection(s) to the export list")
        return {'FINISHED'} if added else {'CANCELLED'}


class PMVR_OT_AddActiveCollection(bpy.types.Operator):
    bl_idname = "pm_vr.add_active_export_collection"
    bl_label = "Add Active Collection"
    bl_description = "Add the active View Layer collection to the export list"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        layer_collection = context.view_layer.active_layer_collection
        collection = layer_collection.collection if layer_collection else None
        if not collection:
            self.report({'WARNING'}, "No active collection")
            return {'CANCELLED'}

        items = context.scene.pm_vr_export_collections
        if any(item.collection == collection for item in items):
            self.report({'INFO'}, f'Collection "{collection.name}" is already in the list')
            return {'CANCELLED'}

        was_added, reason = add_collection_to_export_list(items, collection)
        if not was_added:
            self.report({'WARNING'}, f'Could not add "{collection.name}": {reason}')
            return {'CANCELLED'}
        context.scene.pm_vr_export_collection_index = len(items) - 1
        self.report({'INFO'}, f'Added "{collection.name}"')
        return {'FINISHED'}


class PMVR_OT_RemoveExportCollection(bpy.types.Operator):
    bl_idname = "pm_vr.remove_export_collection"
    bl_label = "Remove Export Collection"
    bl_description = "Remove only the active collection from the export list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.pm_vr_export_collections)

    def execute(self, context):
        scene = context.scene
        items = scene.pm_vr_export_collections
        index = min(scene.pm_vr_export_collection_index, len(items) - 1)
        items.remove(index)
        scene.pm_vr_export_collection_index = min(index, max(0, len(items) - 1))
        return {'FINISHED'}


class PMVR_OT_SetAllExportCollections(bpy.types.Operator):
    bl_idname = "pm_vr.set_all_export_collections"
    bl_label = "Set Export Checkboxes"
    bl_description = "Enable or disable one export format for every collection in the list"

    export_format: bpy.props.EnumProperty(
        name="Format",
        description="Export format whose checkboxes will be changed",
        items=(('USDZ', "USDZ", ""), ('GLB', "GLB", "")),
    )
    enabled: bpy.props.BoolProperty(
        name="Enabled",
        description="Enable when checked; disable when unchecked",
        default=True,
    )

    @classmethod
    def description(cls, _context, properties):
        action = "Enable" if properties.enabled else "Disable"
        return f"{action} {properties.export_format} export for every collection in the list"

    def execute(self, context):
        property_name = "export_usdz" if self.export_format == 'USDZ' else "export_glb"
        for item in context.scene.pm_vr_export_collections:
            setattr(item, property_name, self.enabled)
        return {'FINISHED'}


class PMVR_OT_ExportCollections(bpy.types.Operator):
    bl_idname = "pm_vr.export_collections"
    bl_label = "Export Collections"
    bl_description = "Export checked collections as separate files"

    export_format: bpy.props.EnumProperty(
        items=(
            ('USDZ', "USDZ", "Export checked USDZ rows"),
            ('GLB', "GLB", "Export checked GLB rows"),
            ('BOTH', "Both", "Export both checked formats"),
        ),
        default='BOTH',
    )

    @classmethod
    def description(cls, _context, properties):
        if properties.export_format == 'BOTH':
            return "Export all collections checked for USDZ and GLB"
        return f"Export every collection checked for {properties.export_format}"

    @classmethod
    def poll(cls, context):
        return not context.scene.pm_vr_export_running and any(
            item.export_usdz or item.export_glb
            for item in context.scene.pm_vr_export_collections
        )

    def _formats(self):
        if self.export_format == 'BOTH':
            return ('USDZ', 'GLB')
        return (self.export_format,)

    def _prepare_jobs(self, context):
        try:
            return build_export_jobs(context.scene, self._formats())
        except ExportValidationError as exc:
            self.report({'ERROR'}, str(exc))
            return None

    def _set_progress(self, context, completed, label):
        total = max(1, len(self._jobs))
        context.scene.pm_vr_export_progress = completed / total
        context.scene.pm_vr_export_progress_label = label
        context.window_manager.progress_update(completed)
        if context.workspace:
            context.workspace.status_text_set(label)
        tag_redraw_all(context.window_manager)

    def _finish(self, context, cancelled=False):
        window_manager = context.window_manager
        if getattr(self, "_timer", None) is not None:
            window_manager.event_timer_remove(self._timer)
            self._timer = None
        window_manager.progress_end()
        if context.workspace:
            context.workspace.status_text_set(None)

        scene = context.scene
        scene.pm_vr_export_running = False
        completed = self._succeeded + self._failed
        scene.pm_vr_export_progress = completed / max(1, len(self._jobs))
        if cancelled:
            summary = f"Export cancelled: {self._succeeded} completed, {self._failed} failed"
            self.report({'WARNING'}, summary)
            result = {'CANCELLED'}
        elif self._failed:
            summary = f"Export finished: {self._succeeded} completed, {self._failed} failed"
            self.report({'WARNING'}, f"{summary}; see the console")
            result = {'FINISHED'} if self._succeeded else {'CANCELLED'}
        else:
            summary = f"Export finished: {self._succeeded} file(s) completed"
            self.report({'INFO'}, summary)
            result = {'FINISHED'}
        scene.pm_vr_export_progress_label = summary
        scene.pm_vr_export_summary = summary
        tag_redraw_all(window_manager)
        return result

    def execute(self, context):
        """Synchronous path for scripts and automated tests."""
        jobs = self._prepare_jobs(context)
        if jobs is None:
            return {'CANCELLED'}
        self._jobs = jobs
        self._succeeded = 0
        self._failed = 0
        self._timer = None
        context.scene.pm_vr_export_running = True
        context.scene.pm_vr_export_summary = ""
        context.window_manager.progress_begin(0, len(jobs))

        for index, (format_name, directory, item) in enumerate(jobs):
            label = f"Exporting {format_name}: {item.export_name} ({index + 1}/{len(jobs)})"
            self._set_progress(context, index, label)
            if perform_export_job(format_name, directory, item):
                self._succeeded += 1
            else:
                self._failed += 1
        return self._finish(context)

    def invoke(self, context, _event):
        jobs = self._prepare_jobs(context)
        if jobs is None:
            return {'CANCELLED'}

        self._jobs = jobs
        self._job_index = 0
        self._succeeded = 0
        self._failed = 0
        self._phase = 'ANNOUNCE'
        scene = context.scene
        scene.pm_vr_export_running = True
        scene.pm_vr_export_progress = 0.0
        scene.pm_vr_export_progress_label = f"Preparing {len(jobs)} export job(s)..."
        scene.pm_vr_export_summary = ""

        window_manager = context.window_manager
        window_manager.progress_begin(0, len(jobs))
        self._timer = window_manager.event_timer_add(0.1, window=context.window)
        window_manager.modal_handler_add(self)
        tag_redraw_all(window_manager)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return self._finish(context, cancelled=True)
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}

        if self._job_index >= len(self._jobs):
            return self._finish(context)

        format_name, directory, item = self._jobs[self._job_index]
        if self._phase == 'ANNOUNCE':
            label = (
                f"Exporting {format_name}: {item.export_name} "
                f"({self._job_index + 1}/{len(self._jobs)})"
            )
            setattr(item, f"last_{format_name.lower()}_status", "Exporting")
            self._set_progress(context, self._job_index, label)
            self._phase = 'EXPORT'
            return {'RUNNING_MODAL'}

        if perform_export_job(format_name, directory, item):
            self._succeeded += 1
        else:
            self._failed += 1
        self._job_index += 1
        completed_label = (
            f"Completed {self._job_index}/{len(self._jobs)} — "
            f"{self._succeeded} successful, {self._failed} failed"
        )
        self._set_progress(context, self._job_index, completed_label)
        self._phase = 'ANNOUNCE'
        return {'RUNNING_MODAL'}


def draw_outliner_collection_menu(self, _context):
    self.layout.separator()
    self.layout.operator(PMVR_OT_AddExportCollections.bl_idname, icon='EXPORT')


def draw_ui(layout, context):
    scene = context.scene
    box = layout.box()
    box.label(text="Collection Batch Export", icon='EXPORT')
    content = box.column()
    content.enabled = not scene.pm_vr_export_running
    content.prop(scene, "pm_vr_usdz_export_directory", text="USDZ Directory")
    content.prop(scene, "pm_vr_glb_export_directory", text="GLB Directory")

    content.template_list(
        PMVR_UL_ExportCollections.__name__,
        "",
        scene,
        "pm_vr_export_collections",
        scene,
        "pm_vr_export_collection_index",
        rows=5,
    )

    controls = content.row(align=True)
    controls.operator(PMVR_OT_AddActiveCollection.bl_idname, text="Add Active", icon='ADD')
    controls.operator(PMVR_OT_RemoveExportCollection.bl_idname, text="", icon='REMOVE')

    content.label(text="Outliner: right-click selected collections to add", icon='INFO')

    format_columns = content.row(align=True)
    for format_name in ('USDZ', 'GLB'):
        column = format_columns.column(align=True)
        toggles = column.row(align=True)
        op = toggles.operator(PMVR_OT_SetAllExportCollections.bl_idname, text="All")
        op.export_format = format_name
        op.enabled = True
        op = toggles.operator(PMVR_OT_SetAllExportCollections.bl_idname, text="None")
        op.export_format = format_name
        op.enabled = False
        column.operator_context = 'INVOKE_DEFAULT'
        op = column.operator(PMVR_OT_ExportCollections.bl_idname, text=format_name, icon='EXPORT')
        op.export_format = format_name

    if scene.pm_vr_export_running:
        progress = box.column(align=True)
        progress.label(text=scene.pm_vr_export_progress_label, icon='TIME')
        progress_bar = progress.row()
        progress_bar.enabled = False
        progress_bar.prop(scene, "pm_vr_export_progress", text="", slider=True)
        progress.label(text="Press Esc between files to cancel", icon='INFO')
    elif scene.pm_vr_export_summary:
        icon = 'ERROR' if "failed" in scene.pm_vr_export_summary else 'CHECKMARK'
        box.label(text=scene.pm_vr_export_summary, icon=icon)


CLASSES = (
    PMVR_ExportCollectionItem,
    PMVR_UL_ExportCollections,
    PMVR_OT_AddExportCollections,
    PMVR_OT_AddActiveCollection,
    PMVR_OT_RemoveExportCollection,
    PMVR_OT_SetAllExportCollections,
    PMVR_OT_ExportCollections,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pm_vr_export_collections = bpy.props.CollectionProperty(
        type=PMVR_ExportCollectionItem,
    )
    bpy.types.Scene.pm_vr_export_collection_index = bpy.props.IntProperty(default=0, min=0)
    bpy.types.Scene.pm_vr_usdz_export_directory = bpy.props.StringProperty(
        name="USDZ Export Directory",
        description="Directory for separate USDZ files; // is relative to the Blender file",
        subtype='DIR_PATH',
        default=DEFAULT_USDZ_DIRECTORY,
    )
    bpy.types.Scene.pm_vr_glb_export_directory = bpy.props.StringProperty(
        name="GLB Export Directory",
        description="Directory for separate GLB files; // is relative to the Blender file",
        subtype='DIR_PATH',
        default=DEFAULT_GLB_DIRECTORY,
    )
    bpy.types.Scene.pm_vr_export_running = bpy.props.BoolProperty(
        name="Export Running",
        description="Whether a collection batch export is currently running",
        default=False,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_export_progress = bpy.props.FloatProperty(
        name="Export Progress",
        description="Completed portion of the current collection export batch",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_export_progress_label = bpy.props.StringProperty(
        name="Export Progress Status",
        description="Current collection and format being exported",
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_export_summary = bpy.props.StringProperty(
        name="Last Export Summary",
        description="Result of the most recent collection export batch",
        options={'SKIP_SAVE'},
    )
    bpy.types.OUTLINER_MT_collection.append(draw_outliner_collection_menu)


def unregister():
    bpy.types.OUTLINER_MT_collection.remove(draw_outliner_collection_menu)
    for property_name in (
        "pm_vr_export_summary",
        "pm_vr_export_progress_label",
        "pm_vr_export_progress",
        "pm_vr_export_running",
        "pm_vr_glb_export_directory",
        "pm_vr_usdz_export_directory",
        "pm_vr_export_collection_index",
        "pm_vr_export_collections",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
