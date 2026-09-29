"""Persistent PropertyGroups for the semantic production pipeline."""

import bpy

from .constants import (
    DEBUG_OVERLAY_ITEMS,
    LAYER_TYPE_ITEMS,
    MODE_ITEMS,
    RESOLUTION_ITEMS,
    ROLE_ITEMS,
    SCENARIO_NONE,
    STATE_ITEMS,
)


_RESOLUTION_UPDATE_RUNNING = False
# Blender reads dynamic enum strings after the items callback returns; Python
# must keep them alive. Identical lists share one cached entry.
_ENUM_ITEMS = {}


def _overlay_updated(_owner, _context):
    from . import viewport_overlay

    viewport_overlay.tag_redraw()


def _show_sources_changed(project, _context):
    from .setup_ops import preview_sources

    preview_sources(project)


def _show_generated_changed(project, _context):
    from .setup_ops import preview_generated

    preview_generated(project)


def _bake_mode_changed(project, context):
    from .setup_ops import preview_generated, preview_state

    preview_state(project)
    preview_generated(project)
    _overlay_updated(project, context)


def _layer_type_changed(layer, context):
    scene = getattr(context, "scene", None)
    project = getattr(scene, "pm_vr_project", None) if scene else None
    if project and layer.layer_id:
        for unit in project.bake_units:
            if unit.render_layer_id != layer.layer_id:
                continue
            if unit.day_status == "Ready":
                unit.day_status = "Layer type changed — rebake required"
            if unit.evening_status == "Ready":
                unit.evening_status = "Layer type changed — rebake required"


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
    if _RESOLUTION_UPDATE_RUNNING or not context.scene:
        return
    if unit.batch_selected:
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
    _overlay_updated(unit, context)


def _cached_items(items):
    key = tuple(items)
    if len(_ENUM_ITEMS) > 512:
        _ENUM_ITEMS.clear()
    return _ENUM_ITEMS.setdefault(key, items)


def _scenario_index(project, scenario_id):
    for index, scenario in enumerate(project.bake_scenarios):
        if scenario.scenario_id == scenario_id:
            return index
    return None


def _scenario_name(project, scenario_id):
    if not scenario_id:
        return "No Scenario"
    index = _scenario_index(project, scenario_id)
    return project.bake_scenarios[index].display_name if index is not None else "Missing Scenario"


_NO_SCENARIO_DESCRIPTION = "Bake with the collections as they are in the outliner"


def _scenario_choices(project, first_value):
    return [
        (
            scenario.scenario_id,
            scenario.display_name,
            "Switch collections to this scenario while baking",
            'NONE',
            first_value + index,
        )
        for index, scenario in enumerate(project.bake_scenarios)
    ]


# Layer: 0 = No Scenario, 1.. = scenarios, then Missing for a removed ID.
def _layer_scenario_items(layer, _context):
    project = layer.id_data.pm_vr_project
    items = [('NONE', "No Scenario", _NO_SCENARIO_DESCRIPTION, 'NONE', 0), None]
    items += _scenario_choices(project, 1)
    if layer.bake_scenario_id and _scenario_index(project, layer.bake_scenario_id) is None:
        items.append((
            'MISSING', "Missing Scenario", "The scenario was removed; choose another",
            'ERROR', len(project.bake_scenarios) + 1,
        ))
    return _cached_items(items)


def _get_layer_scenario(layer):
    project = layer.id_data.pm_vr_project
    if not layer.bake_scenario_id:
        return 0
    index = _scenario_index(project, layer.bake_scenario_id)
    return len(project.bake_scenarios) + 1 if index is None else index + 1


def _set_layer_scenario(layer, value):
    project = layer.id_data.pm_vr_project
    if value == 0:
        layer.bake_scenario_id = ""
    elif value <= len(project.bake_scenarios):
        layer.bake_scenario_id = project.bake_scenarios[value - 1].scenario_id
    else:
        return
    from . import log

    log.info(
        "Setup",
        f'Layer "{layer.display_name}" bake scenario: '
        f'{_scenario_name(project, layer.bake_scenario_id)}',
    )


# Unit: 0 = follow the layer, 1 = No Scenario, 2.. = scenarios, then Missing.
def _unit_scenario_items(unit, _context):
    project = unit.id_data.pm_vr_project
    layer = next(
        (item for item in project.render_layers if item.layer_id == unit.render_layer_id),
        None,
    )
    inherited = _scenario_name(project, layer.bake_scenario_id if layer else "")
    layer_name = layer.display_name if layer else "?"
    # The link icon marks the layer's scenario; the name alone keeps queue rows narrow.
    items = [
        ('LAYER', inherited, f'Follow the scenario of layer "{layer_name}"', 'LINKED', 0),
        None,
        ('NONE', "No Scenario", _NO_SCENARIO_DESCRIPTION, 'NONE', 1),
    ]
    items += _scenario_choices(project, 2)
    if (
        unit.bake_scenario_id not in ("", SCENARIO_NONE)
        and _scenario_index(project, unit.bake_scenario_id) is None
    ):
        items.append((
            'MISSING', "Missing Scenario", "The scenario was removed; choose another",
            'ERROR', len(project.bake_scenarios) + 2,
        ))
    return _cached_items(items)


def _get_unit_scenario(unit):
    project = unit.id_data.pm_vr_project
    if not unit.bake_scenario_id:
        return 0
    if unit.bake_scenario_id == SCENARIO_NONE:
        return 1
    index = _scenario_index(project, unit.bake_scenario_id)
    return len(project.bake_scenarios) + 2 if index is None else index + 2


def _set_unit_scenario(unit, value):
    project = unit.id_data.pm_vr_project
    if value == 0:
        scenario_id = ""
    elif value == 1:
        scenario_id = SCENARIO_NONE
    elif value - 2 < len(project.bake_scenarios):
        scenario_id = project.bake_scenarios[value - 2].scenario_id
    else:
        return
    # Like resolution: checked units of the same layer change as one batch.
    units = [unit]
    if unit.batch_selected:
        units += [
            other for other in project.bake_units
            if other != unit
            and other.batch_selected
            and other.render_layer_id == unit.render_layer_id
        ]
    for item in units:
        item.bake_scenario_id = scenario_id
    from . import log

    label = "layer scenario" if not scenario_id else (
        "No Scenario" if scenario_id == SCENARIO_NONE else _scenario_name(project, scenario_id)
    )
    names = ", ".join(f'"{item.display_name}"' for item in units[:5])
    more = f" and {len(units) - 5} more" if len(units) > 5 else ""
    log.info("Setup", f"Bake scenario of {names}{more}: {label}")


class PMVR_ExtraExportLayer(bpy.types.PropertyGroup):
    layer_id: bpy.props.StringProperty(name="Export Layer ID", options={'HIDDEN'})


class PMVR_ObjectMetadata(bpy.types.PropertyGroup):
    source_id: bpy.props.StringProperty(name="Source ID", options={'HIDDEN'})
    render_layer_id: bpy.props.StringProperty(name="Render Layer ID", options={'HIDDEN'})
    processing_role: bpy.props.EnumProperty(name="Role", items=ROLE_ITEMS, default='UNASSIGNED')
    bake_unit_id: bpy.props.StringProperty(name="Bake Unit ID", options={'HIDDEN'})
    is_registered_source: bpy.props.BoolProperty(name="Registered Source", default=False)
    extra_export_layers: bpy.props.CollectionProperty(type=PMVR_ExtraExportLayer)


class PMVR_RenderLayer(bpy.types.PropertyGroup):
    layer_id: bpy.props.StringProperty(name="Layer ID", options={'HIDDEN'})
    display_name: bpy.props.StringProperty(name="Name", default="Render Layer")
    # Kept for loading older .blend files. display_name is now the sole UI and
    # export name.
    output_base_name: bpy.props.StringProperty(name="Legacy Output Name", default="")
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)
    viewport_color: bpy.props.FloatVectorProperty(
        name="Viewport Color",
        description="Color used by the Render Layers viewport overlay",
        subtype='COLOR',
        size=3,
        min=0.0,
        max=1.0,
        default=(0.74, 0.08, 0.92),
        update=_overlay_updated,
    )
    viewport_color_initialized: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    layer_type: bpy.props.EnumProperty(
        name="Type",
        description="Layer meaning and its Blender bake/export behavior",
        items=LAYER_TYPE_ITEMS,
        default='UNLIT',
        update=_layer_type_changed,
    )
    export_usdz: bpy.props.BoolProperty(name="USDZ", default=True)
    export_glb: bpy.props.BoolProperty(name="GLB", default=False)
    bake_scenario_id: bpy.props.StringProperty(name="Bake Scenario ID", options={'HIDDEN'})
    bake_scenario: bpy.props.EnumProperty(
        name="Bake Scenario",
        description="Collections switched on and off while this layer's units bake",
        items=_layer_scenario_items,
        get=_get_layer_scenario,
        set=_set_layer_scenario,
    )


def _is_empty(_self, obj):
    return obj.type == 'EMPTY'


class PMVR_BakeVariant(bpy.types.PropertyGroup):
    """A material variant of a bake unit: baked like the unit with its
    material in place of the unit's variant material (see variants.py)."""

    variant_id: bpy.props.StringProperty(name="Variant ID", options={'HIDDEN'})
    title: bpy.props.StringProperty(
        name="Name",
        description="Title on the variant card; also part of the file name",
    )
    material: bpy.props.PointerProperty(
        name="Material",
        type=bpy.types.Material,
        description="Takes the place of the unit's variant material while this variant bakes",
    )
    day_file: bpy.props.StringProperty(name="Day File", options={'HIDDEN'})
    evening_file: bpy.props.StringProperty(name="Evening File", options={'HIDDEN'})
    day_signature: bpy.props.StringProperty(name="Day Signature", options={'HIDDEN'})
    evening_signature: bpy.props.StringProperty(name="Evening Signature", options={'HIDDEN'})


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
    # Compatibility-only field for older .blend files. Bake execution is
    # controlled exclusively by the explicit queue.
    enabled: bpy.props.BoolProperty(name="Legacy Enabled", default=True, options={'HIDDEN'})
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
    # Resolution the current Beauty result was baked at (0: before this was
    # recorded). Lower than resolution means a test bake or a raised Setup.
    day_baked_resolution: bpy.props.IntProperty(name="Day Baked Resolution", default=0, options={'HIDDEN'})
    evening_baked_resolution: bpy.props.IntProperty(name="Evening Baked Resolution", default=0, options={'HIDDEN'})
    variants: bpy.props.CollectionProperty(type=PMVR_BakeVariant)
    variant_material: bpy.props.PointerProperty(
        name="Changes",
        type=bpy.types.Material,
        description="The object's material that each variant replaces",
    )
    variant_default_title: bpy.props.StringProperty(
        name="Default",
        default="Default",
        description="Title on the card of the object as it is",
    )
    variant_marker: bpy.props.PointerProperty(
        name="Marker",
        type=bpy.types.Object,
        poll=_is_empty,
        description="Optional Empty where the variant button appears; exported as VariantMarker",
    )
    # "" follows the layer's scenario, SCENARIO_NONE bakes with the outliner
    # as it is, anything else is a scenario ID.
    bake_scenario_id: bpy.props.StringProperty(name="Bake Scenario ID", options={'HIDDEN'})
    bake_scenario: bpy.props.EnumProperty(
        name="Bake Scenario",
        description=(
            "Collections switched on and off while this unit bakes. Checked "
            "units of the same layer change together"
        ),
        items=_unit_scenario_items,
        get=_get_unit_scenario,
        set=_set_unit_scenario,
    )


class PMVR_ScenarioCollection(bpy.types.PropertyGroup):
    # name holds the collection name at capture time for list filtering.
    collection: bpy.props.PointerProperty(name="Collection", type=bpy.types.Collection)
    include: bpy.props.BoolProperty(
        name="Enabled",
        description="Keep this collection enabled while the scenario bakes",
        default=True,
    )
    depth: bpy.props.IntProperty(name="Depth", default=0, min=0, options={'HIDDEN'})


class PMVR_BakeScenario(bpy.types.PropertyGroup):
    scenario_id: bpy.props.StringProperty(name="Scenario ID", options={'HIDDEN'})
    display_name: bpy.props.StringProperty(name="Name", default="Scenario")
    collections: bpy.props.CollectionProperty(type=PMVR_ScenarioCollection)
    active_collection_index: bpy.props.IntProperty(default=0, min=0)


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
    active_lighting_state: bpy.props.EnumProperty(
        name="Lighting State",
        items=STATE_ITEMS,
        default='DAY',
        update=_overlay_updated,
    )
    bake_day: bpy.props.BoolProperty(name="Day", default=True)
    bake_evening: bpy.props.BoolProperty(name="Evening", default=False)
    bake_mode: bpy.props.EnumProperty(
        name="Bake Mode",
        items=MODE_ITEMS,
        default='BEAUTY',
        update=_bake_mode_changed,
    )
    overlay_mode: bpy.props.EnumProperty(
        name="Viewport Overlay",
        description="Visualize pipeline diagnostics directly in the 3D viewport",
        items=DEBUG_OVERLAY_ITEMS,
        default='OFF',
        options={'SKIP_SAVE'},
        update=_overlay_updated,
    )
    overlay_opacity: bpy.props.FloatProperty(
        name="Intensity",
        description="Visibility of the PM VR diagnostic overlay",
        default=0.65,
        min=0.05,
        max=1.0,
        subtype='FACTOR',
        update=_overlay_updated,
    )
    debug_checker_uv: bpy.props.EnumProperty(
        name="Checker UV",
        description="UV channel used by the GPU checker diagnostic",
        items=(
            ('PRIMARY', "UVMap", "Preview the first UV channel"),
            ('BAKE', "SimpleBake", "Preview the second UV channel"),
        ),
        default='BAKE',
        update=_overlay_updated,
    )
    overlay_show_unassigned: bpy.props.BoolProperty(
        name="Show Unassigned",
        description="Show amber diagnostics for source meshes without a valid render layer assignment",
        default=False,
        update=_overlay_updated,
    )

    test_resolution: bpy.props.EnumProperty(
        name="Test Resolution",
        description=(
            "Bake the queue at a share of the Bake Resolution for a quick "
            "check. Setup keeps its values; back to 100% when a file opens"
        ),
        items=(
            ('100', "100%", "Bake at the Bake Resolution", 0),
            ('75', "75%", "Bake at three quarters of the Bake Resolution", 1),
            ('50', "50%", "Bake at half the Bake Resolution", 2),
            ('25', "25%", "Bake at a quarter of the Bake Resolution", 3),
        ),
        default='100',
    )
    bake_resolution: bpy.props.EnumProperty(
        name="Bake Resolution",
        description=(
            "Every unit bakes at this size (its own when larger) and keeps "
            "the file at it. Export scales each atlas to its unit's "
            "resolution, so a unit's resolution can change without a rebake"
        ),
        items=RESOLUTION_ITEMS,
        default='4096',
    )
    uv_padding: bpy.props.FloatProperty(
        name="Island Padding",
        description=(
            "Space between UV islands, in UV units, as set in the packer "
            "(UVPackmaster Margin). The bake margin fills that gap at any "
            "resolution and never paints over a neighbouring island"
        ),
        default=0.002,
        min=0.0,
        soft_max=0.02,
        step=0.01,
        precision=4,
    )
    cycles_samples: bpy.props.IntProperty(name="Samples", default=256, min=1, soft_max=2048)
    default_unit_resolution: bpy.props.EnumProperty(
        name="Default Unit Resolution",
        items=RESOLUTION_ITEMS,
        default='4096',
        update=_overlay_updated,
    )
    beauty_output_directory: bpy.props.StringProperty(name="Beauty Directory", subtype='DIR_PATH', default="//Beauty_Bakes/")
    lightmap_output_directory: bpy.props.StringProperty(name="Lightmap Directory", subtype='DIR_PATH', default="//Lightmaps/")
    usdz_output_directory: bpy.props.StringProperty(name="USDZ Directory", subtype='DIR_PATH', default="//USDZ/")
    glb_output_directory: bpy.props.StringProperty(name="GLB Directory", subtype='DIR_PATH', default="//GLB/")
    probe_output_directory: bpy.props.StringProperty(
        name="Probe Directory",
        description="Where Render Probes writes its EXR panoramas; empty: a probes folder next to the USDZ folder",
        subtype='DIR_PATH',
        default="",
    )
    probe_width: bpy.props.EnumProperty(
        name="Probe Size",
        description="Width of a probe panorama; the height is half of it",
        items=tuple((str(width), f"{width} × {width // 2}", "") for width in (512, 1024, 2048)),
        default='1024',
    )

    render_layers: bpy.props.CollectionProperty(type=PMVR_RenderLayer)
    active_render_layer_index: bpy.props.IntProperty(default=0, min=0, update=_active_layer_changed)
    bake_units: bpy.props.CollectionProperty(type=PMVR_BakeUnit)
    active_bake_unit_index: bpy.props.IntProperty(default=0, min=0)
    bake_queue: bpy.props.CollectionProperty(type=PMVR_BakeQueueEntry)
    active_bake_queue_index: bpy.props.IntProperty(default=0, min=0)
    build_records: bpy.props.CollectionProperty(type=PMVR_BuildRecord)
    bake_scenarios: bpy.props.CollectionProperty(type=PMVR_BakeScenario)
    active_bake_scenario_index: bpy.props.IntProperty(default=0, min=0)
    show_bake_scenarios: bpy.props.BoolProperty(name="Bake Scenarios", default=True)

    show_sources: bpy.props.BoolProperty(
        name="Show Sources",
        description="Show the source objects in the viewport",
        default=True,
        update=_show_sources_changed,
    )
    show_generated: bpy.props.BoolProperty(
        name="Show Generated",
        description="Show the baked results of the current bake mode in the viewport",
        default=True,
        update=_show_generated_changed,
    )
    last_validation_summary: bpy.props.StringProperty(name="Validation Summary", options={'SKIP_SAVE'})
    last_operation_summary: bpy.props.StringProperty(name="Operation Summary", options={'SKIP_SAVE'})
    operation_running: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    operation_progress: bpy.props.FloatProperty(default=0.0, min=0.0, max=1.0, subtype='FACTOR', options={'SKIP_SAVE'})


CLASSES = (
    PMVR_ExtraExportLayer,
    PMVR_ObjectMetadata,
    PMVR_RenderLayer,
    PMVR_BakeVariant,
    PMVR_BakeUnit,
    PMVR_ScenarioCollection,
    PMVR_BakeScenario,
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
