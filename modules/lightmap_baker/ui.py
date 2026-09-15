"""Bake-list operators and panel drawing."""

import bpy

from .constants import TAG_GENERATED


def _is_generated(obj):
    return bool(obj and obj.get(TAG_GENERATED, False))


class PM_UL_LightmapBakeObjects(bpy.types.UIList):
    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index,
    ):
        obj = item.source_object
        row = layout.row(align=True)
        row.label(
            text=obj.name if obj else (item.source_name or "<Missing Object>"),
            icon='MESH_DATA' if obj else 'ERROR',
        )
        row.prop(item, "use_resolution_override", text="")
        resolution_row = row.row(align=True)
        resolution_row.enabled = item.use_resolution_override
        resolution_row.prop(item, "resolution", text="")


class PM_OT_LightmapAddSelected(bpy.types.Operator):
    bl_idname = "pm.lightmap_add_selected"
    bl_label = "Add Selected"
    bl_description = "Add selected mesh objects to the lightmap bake list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        settings = context.scene.pm_lightmap_settings
        existing = {
            item.source_object.as_pointer()
            for item in settings.objects
            if item.source_object
        }
        added = 0
        ignored = 0

        for obj in context.selected_objects:
            if (
                obj.type != 'MESH'
                or _is_generated(obj)
                or obj.as_pointer() in existing
            ):
                ignored += 1
                continue

            item = settings.objects.add()
            item.source_object = obj
            item.source_name = obj.name
            item.name = obj.name
            existing.add(obj.as_pointer())
            added += 1

        if added:
            self.report({'INFO'}, f"Added {added} object(s)")
            return {'FINISHED'}

        self.report({'WARNING'}, f"No objects added ({ignored} ignored)")
        return {'CANCELLED'}


class PM_OT_LightmapRemove(bpy.types.Operator):
    bl_idname = "pm.lightmap_remove"
    bl_label = "Remove"
    bl_description = "Remove the active row from the lightmap bake list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = context.scene.pm_lightmap_settings
        return bool(settings.objects)

    def execute(self, context):
        settings = context.scene.pm_lightmap_settings
        index = min(settings.active_index, len(settings.objects) - 1)
        settings.objects.remove(index)
        settings.active_index = min(index, max(0, len(settings.objects) - 1))
        return {'FINISHED'}


class PM_OT_LightmapClear(bpy.types.Operator):
    bl_idname = "pm.lightmap_clear"
    bl_label = "Clear"
    bl_description = "Clear the lightmap bake list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.pm_lightmap_settings.objects)

    def execute(self, context):
        settings = context.scene.pm_lightmap_settings
        settings.objects.clear()
        settings.active_index = 0
        return {'FINISHED'}


class PM_OT_LightmapRefresh(bpy.types.Operator):
    bl_idname = "pm.lightmap_refresh"
    bl_label = "Refresh"
    bl_description = "Remove missing, invalid, generated, and duplicate list entries"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.pm_lightmap_settings
        seen = set()
        removed = 0

        for index in reversed(range(len(settings.objects))):
            item = settings.objects[index]
            obj = item.source_object
            pointer = obj.as_pointer() if obj else None
            invalid = (
                obj is None
                or obj.type != 'MESH'
                or _is_generated(obj)
                or pointer in seen
            )
            if invalid:
                settings.objects.remove(index)
                removed += 1
                continue

            seen.add(pointer)
            item.source_name = obj.name
            item.name = obj.name

        settings.active_index = min(
            settings.active_index,
            max(0, len(settings.objects) - 1),
        )
        self.report({'INFO'}, f"Refreshed list; removed {removed} row(s)")
        return {'FINISHED'}


def draw_ui(layout, context):
    settings = context.scene.pm_lightmap_settings
    box = layout.box()
    box.label(text="Lightmap Bake Queue", icon='LIGHTPROBE_SPHERE')

    box.template_list(
        PM_UL_LightmapBakeObjects.__name__,
        "",
        settings,
        "objects",
        settings,
        "active_index",
        rows=4,
    )

    controls = box.row(align=True)
    controls.operator(PM_OT_LightmapAddSelected.bl_idname, text="Add Selected", icon='ADD')
    controls.operator(PM_OT_LightmapRemove.bl_idname, text="", icon='REMOVE')
    controls.operator(PM_OT_LightmapClear.bl_idname, text="", icon='TRASH')
    controls.operator(PM_OT_LightmapRefresh.bl_idname, text="", icon='FILE_REFRESH')

    box.separator()
    box.prop(settings, "resolution")
    box.prop(settings, "margin")
    box.prop(settings, "export_to_disk")
    output_row = box.row()
    output_row.enabled = settings.export_to_disk
    output_row.prop(settings, "output_directory")

    bake_row = box.row()
    bake_row.scale_y = 1.35
    bake_row.operator(
        "pm.lightmap_bake",
        text="Bake Lightmaps",
        icon='RENDER_STILL',
    )


CLASSES = (
    PM_UL_LightmapBakeObjects,
    PM_OT_LightmapAddSelected,
    PM_OT_LightmapRemove,
    PM_OT_LightmapClear,
    PM_OT_LightmapRefresh,
)
