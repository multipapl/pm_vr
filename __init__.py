import bpy
import importlib

from .modules import lightmap_baker
from .modules import collection_export
from .modules import material_rebuild
from .modules import vr_project_tools


bl_info = {
    "name": "PM VR",
    "author": "multipapl",
    "version": (1, 3, 1),
    "blender": (5, 2, 0),
    "location": "View3D > N-Panel > PM VR",
    "description": "Internal tools for VR project production",
    "category": "Interface",
}


MODULES = (lightmap_baker, vr_project_tools, material_rebuild, collection_export)
CATEGORIES = (
    ("LIGHTMAP_BAKER", "LIGHTMAP BAKER", "show_lightmap_baker", "LIGHTPROBE_SPHERE"),
    ("VR_PROJECT", "VR PROJECT", "show_vr_project", "WORLD"),
    ("COLLECTION_EXPORT", "COLLECTION EXPORT", "show_collection_export", "EXPORT"),
)


class PMVR_UI_State(bpy.types.PropertyGroup):
    """Expanded/collapsed state for PM VR sections."""

    show_lightmap_baker: bpy.props.BoolProperty(name="Lightmap Baker", default=True)
    show_vr_project: bpy.props.BoolProperty(name="VR Project", default=True)
    show_collection_export: bpy.props.BoolProperty(name="Collection Export", default=True)


class PMVR_PT_MainPanel(bpy.types.Panel):
    bl_label = "PM VR"
    bl_idname = "PMVR_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PM VR'

    def draw(self, context):
        layout = self.layout
        ui_state = context.scene.pm_vr_ui_state

        for category, title, property_name, icon in CATEGORIES:
            category_modules = [
                module for module in MODULES
                if getattr(module, "UI_CATEGORY", None) == category
            ]
            if not category_modules:
                continue

            is_expanded = getattr(ui_state, property_name)
            box = layout.box()
            header = box.row(align=True)
            header.prop(
                ui_state,
                property_name,
                text="",
                icon='TRIA_DOWN' if is_expanded else 'TRIA_RIGHT',
                emboss=False,
            )
            header.label(text=title, icon=icon)

            if is_expanded:
                for module in category_modules:
                    module.draw_ui(box, context)


CLASSES = (PMVR_UI_State, PMVR_PT_MainPanel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pm_vr_ui_state = bpy.props.PointerProperty(type=PMVR_UI_State)

    for module in MODULES:
        importlib.reload(module)
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()

    if hasattr(bpy.types.Scene, "pm_vr_ui_state"):
        del bpy.types.Scene.pm_vr_ui_state
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
