if "bpy" in locals():
    # Blender re-executes this file when scripts are reloaded. Drop every
    # submodule so nested packages (pipeline, lightmap_baker) are imported
    # fresh instead of keeping their previous code.
    import sys

    for _module_name in [name for name in sys.modules if name.startswith(f"{__name__}.")]:
        del sys.modules[_module_name]

import bpy

from .modules import lightmap_baker
from .modules import checker_preview
from .modules import collection_export
from .modules import material_rebuild
from .modules import pipeline
from .modules import vr_project_tools


bl_info = {
    "name": "PM VR",
    "author": "multipapl",
    "version": (2, 0, 0),
    "blender": (5, 2, 0),
    "location": "View3D > N-Panel > PM VR",
    "description": "Internal tools for VR project production",
    "category": "Interface",
}


MODULES = (lightmap_baker, checker_preview, vr_project_tools, material_rebuild, collection_export, pipeline)


def _ui_stage_changed(ui_state, _context):
    if ui_state.stage == 'SETUP':
        pipeline.request_selection_sync()


class PMVR_UI_State(bpy.types.PropertyGroup):
    """Active production stage for the PM VR panel."""

    stage: bpy.props.EnumProperty(
        name="Stage",
        items=(
            ('OPTIMIZATION', "Optimize", "Manual source-scene optimization tools", 'MODIFIER', 0),
            ('SETUP', "Setup", "Semantic render layers and bake units", 'PREFERENCES', 1),
            ('BAKE', "Bake", "Persistent unit queue and Beauty/Lightmap bake", 'RENDER_STILL', 2),
            ('EXPORT', "Export", "Semantic layer export", 'EXPORT', 3),
        ),
        default='OPTIMIZATION',
        update=_ui_stage_changed,
    )


class PMVR_PT_MainPanel(bpy.types.Panel):
    bl_label = "PM VR"
    bl_idname = "PMVR_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'PM VR'

    def draw(self, context):
        layout = self.layout
        ui_state = context.scene.pm_vr_ui_state
        header = layout.row(align=True)
        header.prop(ui_state, "stage", expand=True)
        header.operator("pmvr.show_help", text="", icon='QUESTION')
        header.operator("pmvr.project_settings", text="", icon='PREFERENCES')

        if ui_state.stage == 'OPTIMIZATION':
            pipeline.draw_scene_debug(layout, context)
            vr_project_tools.draw_ui(layout, context)
        else:
            pipeline.draw_stage(layout, context, ui_state.stage)


CLASSES = (PMVR_UI_State, PMVR_PT_MainPanel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pm_vr_ui_state = bpy.props.PointerProperty(type=PMVR_UI_State)

    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()

    if hasattr(bpy.types.Scene, "pm_vr_ui_state"):
        del bpy.types.Scene.pm_vr_ui_state
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
