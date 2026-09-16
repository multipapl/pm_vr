"""Persistent PropertyGroups for the semantic production pipeline."""

import bpy

from .constants import MODE_ITEMS, PROFILE_ITEMS, RESOLUTION_ITEMS, ROLE_ITEMS, STATE_ITEMS


_RESOLUTION_UPDATE_RUNNING = False


def _active_layer_changed(project, _context):
    if not project.render_layers or not project.bake_units:
        project.active_bake_unit_index = 0
        return
    layer_index = min(project.active_render_layer_index, len(project.render_layers) - 1)
    layer_id = project.render_layers[layer_index].layer_id
    current = min(project.active_bake_unit_index, len(project.bake_units) - 1)
    if project.bake_units[current].render_layer_id == layer_id:
        return
    for index, unit in enumerate(project.bake_units):
        if unit.render_layer_id == layer_id:
            project.active_bake_unit_index = index
            return
    project.active_bake_unit_index = 0


def _batch_resolution_changed(unit, context):
    global _RESOLUTION_UPDATE_RUNNING
    if _RESOLUTION_UPDATE_RUNNING or not unit.batch_selected or not context.scene:
        return
    project = context.scene.pm_vr_project
    _RESOLUTION_UPDATE_RUNNING = True
    try:
        for other in project.bake_units:
            if (
                other != unit
                and other.batch_selected
                and other.render_layer_id == unit.render_layer_id
            ):
                other.resolution = unit.resolution
    finally:
        _RESOLUTION_UPDATE_RUNNING = False


class PMVR_ObjectMetadata(bpy.types.PropertyGroup):
    source_id: bpy.props.StringProperty(name="Source ID", options={'HIDDEN'})
    render_layer_id: bpy.props.StringProperty(name="Render Layer ID", options={'HIDDEN'})
    processing_role: bpy.props.EnumProperty(name="Role", items=ROLE_ITEMS, default='UNASSIGNED')
    bake_unit_id: bpy.props.StringProperty(name="Bake Unit ID", options={'HIDDEN'})
    is_registered_source: bpy.props.BoolProperty(name="Registered Source", default=False)


class PMVR_RenderLayer(bpy.types.PropertyGroup):
    layer_id: bpy.props.StringProperty(name="Layer ID", options={'HIDDEN'})
    display_name: bpy.props.StringProperty(name="Name", default="Render Layer")
    # Kept for loading older .blend files. display_name is now the sole UI and
    # export name.
    output_base_name: bpy.props.StringProperty(name="Legacy Output Name", default="")
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)
    processing_profile: bpy.props.EnumProperty(name="Profile", items=PROFILE_ITEMS, default='BEAUTY_SCENE')
    export_usdz: bpy.props.BoolProperty(name="USDZ", default=True)
    export_glb: bpy.props.BoolProperty(name="GLB", default=False)


class PMVR_BakeUnit(bpy.types.PropertyGroup):
    unit_id: bpy.props.StringProperty(name="Unit ID", options={'HIDDEN'})
    display_name: bpy.props.StringProperty(name="Name", default="Bake Unit")
    artifact_key: bpy.props.StringProperty(name="Artifact Key", options={'HIDDEN'})
    render_layer_id: bpy.props.StringProperty(name="Render Layer ID", options={'HIDDEN'})
    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=RESOLUTION_ITEMS,
        default='4096',
        update=_batch_resolution_changed,
    )
    batch_selected: bpy.props.BoolProperty(
        name="Batch Selected",
        description="Include this unit when changing resolution as a batch",
        default=False,
        options={'SKIP_SAVE'},
    )
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)
    day_signature: bpy.props.StringProperty(name="Day Signature", options={'HIDDEN'})
    evening_signature: bpy.props.StringProperty(name="Evening Signature", options={'HIDDEN'})
    day_beauty_image: bpy.props.StringProperty(name="Day Beauty Image", options={'HIDDEN'})
    evening_beauty_image: bpy.props.StringProperty(name="Evening Beauty Image", options={'HIDDEN'})
    day_lightmap_image: bpy.props.StringProperty(name="Day Lightmap Image", options={'HIDDEN'})
    evening_lightmap_image: bpy.props.StringProperty(name="Evening Lightmap Image", options={'HIDDEN'})
    day_lightmap_signature: bpy.props.StringProperty(name="Day Lightmap Signature", options={'HIDDEN'})
    evening_lightmap_signature: bpy.props.StringProperty(name="Evening Lightmap Signature", options={'HIDDEN'})
    day_lightmap_status: bpy.props.StringProperty(name="Day Lightmap Status")
    evening_lightmap_status: bpy.props.StringProperty(name="Evening Lightmap Status")
    day_status: bpy.props.StringProperty(name="Day Status")
    evening_status: bpy.props.StringProperty(name="Evening Status")


class PMVR_BakeQueueEntry(bpy.types.PropertyGroup):
    unit_id: bpy.props.StringProperty(name="Unit ID")


class PMVR_BuildRecord(bpy.types.PropertyGroup):
    unit_id: bpy.props.StringProperty(name="Unit ID")
    lighting_state: bpy.props.EnumProperty(items=STATE_ITEMS)
    bake_mode: bpy.props.EnumProperty(items=MODE_ITEMS)
    signature: bpy.props.StringProperty(name="Signature")
    image_name: bpy.props.StringProperty(name="Image")
    timestamp: bpy.props.StringProperty(name="Timestamp")
    status: bpy.props.StringProperty(name="Status")
    message: bpy.props.StringProperty(name="Message")


class PMVR_ProjectSettings(bpy.types.PropertyGroup):
    schema_version: bpy.props.IntProperty(name="Schema Version", default=1, min=1)
    initialized: bpy.props.BoolProperty(name="Project Initialized", default=False)
    project_id: bpy.props.StringProperty(name="Project ID", options={'HIDDEN'})

    source_root_collection: bpy.props.PointerProperty(name="Source Root", type=bpy.types.Collection)
    day_lighting_collection: bpy.props.PointerProperty(name="Day Lighting", type=bpy.types.Collection)
    day_world: bpy.props.PointerProperty(name="Day World", type=bpy.types.World)
    evening_lighting_collection: bpy.props.PointerProperty(name="Evening Lighting", type=bpy.types.Collection)
    evening_world: bpy.props.PointerProperty(name="Evening World", type=bpy.types.World)
    active_lighting_state: bpy.props.EnumProperty(name="Lighting State", items=STATE_ITEMS, default='DAY')
    bake_day: bpy.props.BoolProperty(name="Day", default=True)
    bake_evening: bpy.props.BoolProperty(name="Evening", default=False)
    bake_mode: bpy.props.EnumProperty(name="Bake Mode", items=MODE_ITEMS, default='BEAUTY')

    margin: bpy.props.IntProperty(name="Margin", default=16, min=0, soft_max=128)
    cycles_samples: bpy.props.IntProperty(name="Samples", default=256, min=1, soft_max=2048)
    default_unit_resolution: bpy.props.EnumProperty(
        name="Default Unit Resolution",
        items=RESOLUTION_ITEMS,
        default='4096',
    )
    beauty_output_directory: bpy.props.StringProperty(name="Beauty Directory", subtype='DIR_PATH', default="//Beauty_Bakes/")
    lightmap_output_directory: bpy.props.StringProperty(name="Lightmap Directory", subtype='DIR_PATH', default="//Lightmaps/")
    usdz_output_directory: bpy.props.StringProperty(name="USDZ Directory", subtype='DIR_PATH', default="//USDZ/")
    glb_output_directory: bpy.props.StringProperty(name="GLB Directory", subtype='DIR_PATH', default="//GLB/")

    render_layers: bpy.props.CollectionProperty(type=PMVR_RenderLayer)
    active_render_layer_index: bpy.props.IntProperty(default=0, min=0, update=_active_layer_changed)
    bake_units: bpy.props.CollectionProperty(type=PMVR_BakeUnit)
    active_bake_unit_index: bpy.props.IntProperty(default=0, min=0)
    bake_queue: bpy.props.CollectionProperty(type=PMVR_BakeQueueEntry)
    active_bake_queue_index: bpy.props.IntProperty(default=0, min=0)
    build_records: bpy.props.CollectionProperty(type=PMVR_BuildRecord)

    show_sources: bpy.props.BoolProperty(name="Show Sources", default=True)
    show_generated: bpy.props.BoolProperty(name="Show Generated", default=True)
    last_validation_summary: bpy.props.StringProperty(name="Validation Summary", options={'SKIP_SAVE'})
    last_operation_summary: bpy.props.StringProperty(name="Operation Summary", options={'SKIP_SAVE'})
    operation_running: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    operation_progress: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR', options={'SKIP_SAVE'})


CLASSES = (
    PMVR_ObjectMetadata,
    PMVR_RenderLayer,
    PMVR_BakeUnit,
    PMVR_BakeQueueEntry,
    PMVR_BuildRecord,
    PMVR_ProjectSettings,
)


def register_properties():
    bpy.types.Object.pm_vr_pipeline = bpy.props.PointerProperty(type=PMVR_ObjectMetadata)
    bpy.types.Scene.pm_vr_project = bpy.props.PointerProperty(type=PMVR_ProjectSettings)


def unregister_properties():
    if hasattr(bpy.types.Scene, "pm_vr_project"):
        del bpy.types.Scene.pm_vr_project
    if hasattr(bpy.types.Object, "pm_vr_pipeline"):
        del bpy.types.Object.pm_vr_pipeline
