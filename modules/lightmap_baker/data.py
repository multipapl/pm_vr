"""Persistent scene data for PM Lightmap Baker."""

import bpy

from .constants import RESOLUTION_ITEMS


class PM_LightmapBakeItem(bpy.types.PropertyGroup):
    source_object: bpy.props.PointerProperty(  # type: ignore[reportInvalidTypeForm]
        name="Object",
        type=bpy.types.Object,
    )
    source_name: bpy.props.StringProperty(  # type: ignore[reportInvalidTypeForm]
        name="Object Name",
        default="",
    )
    use_resolution_override: bpy.props.BoolProperty(  # type: ignore[reportInvalidTypeForm]
        name="Use Resolution Override",
        description="Use this row's resolution instead of the global resolution",
        default=False,
    )
    resolution: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Resolution",
        items=RESOLUTION_ITEMS,
        default="2048",
    )


class PM_LightmapSettings(bpy.types.PropertyGroup):
    objects: bpy.props.CollectionProperty(  # type: ignore[reportInvalidTypeForm]
        type=PM_LightmapBakeItem,
    )
    active_index: bpy.props.IntProperty(  # type: ignore[reportInvalidTypeForm]
        name="Active Lightmap Object",
        default=0,
        min=0,
    )
    resolution: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Resolution",
        items=RESOLUTION_ITEMS,
        default="2048",
    )
    margin: bpy.props.IntProperty(  # type: ignore[reportInvalidTypeForm]
        name="Margin",
        description="Padding around UV islands in pixels",
        default=16,
        min=0,
        soft_max=128,
    )
    export_to_disk: bpy.props.BoolProperty(  # type: ignore[reportInvalidTypeForm]
        name="Export to Disk",
        description="Save 32-bit scene-linear PIZ OpenEXR files",
        default=True,
    )
    output_directory: bpy.props.StringProperty(  # type: ignore[reportInvalidTypeForm]
        name="Output Directory",
        subtype='DIR_PATH',
        default="//Lightmaps/",
    )


CLASSES = (
    PM_LightmapBakeItem,
    PM_LightmapSettings,
)


def register_scene_properties():
    bpy.types.Scene.pm_lightmap_settings = bpy.props.PointerProperty(
        type=PM_LightmapSettings,
    )


def unregister_scene_properties():
    if hasattr(bpy.types.Scene, "pm_lightmap_settings"):
        del bpy.types.Scene.pm_lightmap_settings
