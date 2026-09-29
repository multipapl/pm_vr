"""Four-stage pipeline UI lists and stage drawing."""

import bpy

from .bake_scene import PipelineBakeError
from .constants import BAKE_LAYER_TYPES
from .identity import (
    extra_export_members,
    find_layer,
    find_unit,
    layer_members,
    unit_members,
    units_with_members,
)
from .probes import probe_cameras, probe_directory, probe_states
from .scenarios import Scope, active_scenario, switched_off_count
from .variants import variant_problem, variant_status
from .setup_ops import (
    active_layer,
    active_unit,
    queue_bake_size,
    baked_resolution,
    test_resolution_label,
    unassigned_visible_objects,
)


def short_resolution(size):
    return f"{size / 1024:g}K" if size >= 1024 else str(size)


def beauty_status(unit, state):
    status = (unit.day_status if state == 'DAY' else unit.evening_status) or "—"
    baked = baked_resolution(unit, state)
    if status == "Ready" and 0 < baked < int(unit.resolution):
        # A test bake, or Setup raised after baking: rebake before export.
        return f"Ready {short_resolution(baked)} (Setup {short_resolution(int(unit.resolution))})"
    return status


def last_baked(project, unit):
    """(D/E, "29.09 19:18") of the unit's latest successful Beauty bake per state."""
    latest = {}
    for record in project.build_records:
        if (
            record.unit_id == unit.unit_id and record.bake_mode == 'BEAUTY'
            and record.status == "SUCCESS" and not record.message.startswith("variant ")
        ):
            latest[record.lighting_state] = max(latest.get(record.lighting_state, ""), record.timestamp)
    result = []
    for state, label in (('DAY', "D"), ('EVENING', "E")):
        stamp = latest.get(state, "")
        if len(stamp) >= 16:
            result.append((label, f"{stamp[8:10]}.{stamp[5:7]} {stamp[11:16]}"))
    return result


def draw_state_switch(layout, project):
    row = layout.row(align=True)
    row.label(text="Lighting:")
    for state, label, icon in (('DAY', "Day", 'LIGHT_SUN'), ('EVENING', "Evening", 'LIGHT')):
        op = row.operator(
            "pmvr.set_lighting_state",
            text=label,
            icon=icon,
            depress=project.active_lighting_state == state,
        )
        op.state = state


class PMVR_UL_RenderLayers(bpy.types.UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        row = layout.row(align=True)
        row.prop(item, "viewport_color", text="")
        row.prop(item, "enabled", text="")
        row.prop(item, "display_name", text="", emboss=False, icon='RENDERLAYERS')
        row.label(text=item.bl_rna.properties["layer_type"].enum_items[item.layer_type].name)


# Lightmap bake is kept but not in production use yet; True shows its controls.
SHOW_LIGHTMAP = False

# Units with at least one member, collected once per panel draw for the list rows.
_OCCUPIED_UNITS = set()


class PMVR_UL_BakeUnits(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        units = getattr(data, propname)
        layer = active_layer(context.scene.pm_vr_project)
        flags = [
            self.bitflag_filter_item if layer and unit.render_layer_id == layer.layer_id else 0
            for unit in units
        ]
        return flags, []

    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        row = layout.row(align=True)
        empty = item.unit_id not in _OCCUPIED_UNITS
        row.prop(item, "batch_selected", text="")
        name = row.row(align=True)
        name.alert = empty
        name.prop(item, "display_name", text="", emboss=False, icon='ERROR' if empty else 'UV')
        row.prop(item, "resolution", text="")


class PMVR_UL_BakeQueue(bpy.types.UIList):
    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        unit = find_unit(context.scene.pm_vr_project, item.unit_id)
        if not unit:
            layout.label(text="Missing unit", icon='ERROR')
            return
        split = layout.split(factor=0.35, align=True)
        split.label(text=unit.display_name + (f" +{len(unit.variants)} var." if len(unit.variants) else ""))
        right = split.row(align=True)
        done = ("D" if item.day_done else "") + ("E" if item.evening_done else "")
        if done:
            # Baked in this queue; the entry leaves once every state is done.
            mark = right.row(align=True)
            mark.ui_units_x = 2.0
            mark.label(text=done, icon='CHECKMARK')
        right.prop(unit, "bake_scenario", text="")
        resolution = right.row(align=True)
        resolution.ui_units_x = 1.6
        # The size the unit ships at; the bake size is shown under the list.
        resolution.label(text=short_resolution(int(unit.resolution)))


class PMVR_UL_BakeScenarios(bpy.types.UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        row = layout.row(align=True)
        row.prop(item, "display_name", text="", emboss=False, icon='OUTLINER_COLLECTION')
        row.label(text=f"{switched_off_count(item)} off")


class PMVR_UL_ScenarioCollections(bpy.types.UIList):
    """Recorded collections in outliner order; the content of a disabled
    collection is greyed out because it is disabled with it. Deleted
    collections are not listed (they are dropped when the file loads)."""

    def filter_items(self, _context, data, propname):
        items = getattr(data, propname)
        return [self.bitflag_filter_item if item.collection else 0 for item in items], []

    def draw_item(self, _context, layout, data, item, _icon, _active_data, _active_propname, index):
        parent_off = False
        depth = item.depth
        items = data.collections
        for previous in range(index - 1, -1, -1):
            if depth == 0:
                break
            other = items[previous]
            if other.depth < depth:
                if not other.include:
                    parent_off = True
                    break
                depth = other.depth
        row = layout.row(align=True)
        row.active = not parent_off
        if item.depth:
            row.separator(factor=1.5 * item.depth)
        row.prop(item, "include", text="")
        if item.collection:
            row.label(text=item.collection.name, icon='OUTLINER_COLLECTION')
        else:
            row.label(text=f"{item.name} (deleted)", icon='ERROR')


def draw_setup(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        box = layout.box()
        box.label(text="Initialize the semantic pipeline in this .blend", icon='INFO')
        box.operator("pmvr.initialize_project", icon='PLAY')
        return

    draw_state_switch(layout, project)
    layers = layout.box()
    header = layers.row(align=True)
    header.label(text="Render Layers", icon='RENDERLAYERS')
    unassigned = len(unassigned_visible_objects(context))
    op = header.operator(
        "pmvr.select_pipeline_items",
        text=f"Unassigned: {unassigned}",
        icon='RESTRICT_SELECT_OFF',
    )
    op.target = 'UNASSIGNED'
    row = layers.row()
    row.template_list("PMVR_UL_RenderLayers", "", project, "render_layers", project, "active_render_layer_index", rows=5)
    controls = row.column(align=True)
    controls.operator("pmvr.add_render_layer", text="", icon='ADD')
    controls.operator("pmvr.remove_render_layer", text="", icon='REMOVE')
    controls.separator()
    controls.operator("pmvr.move_render_layer", text="", icon='TRIA_UP').direction = 'UP'
    controls.operator("pmvr.move_render_layer", text="", icon='TRIA_DOWN').direction = 'DOWN'
    layer = active_layer(project)
    if layer:
        detail = layers.column(align=True)
        detail.prop(layer, "display_name")
        detail.prop(layer, "layer_type")
        if layer.layer_type in BAKE_LAYER_TYPES:
            detail.prop(layer, "bake_scenario", text="Scenario")
        formats = detail.row(align=True)
        formats.prop(layer, "export_usdz", toggle=True)
        formats.prop(layer, "export_glb", toggle=True)
        nav = detail.row(align=True)
        op = nav.operator("pmvr.select_pipeline_items", text=f"Select {len(layer_members(layer.layer_id))} Sources")
        op.target = 'LAYER_SOURCES'
        if layer.layer_type in BAKE_LAYER_TYPES:
            units = sum(1 for unit in project.bake_units if unit.render_layer_id == layer.layer_id)
            nav.operator("pmvr.queue_layer_units", text=f"Queue {units} Units", icon='RENDER_STILL')

    if layer and layer.layer_type not in BAKE_LAYER_TYPES:
        draw_original_objects(layout, layer, layer_members(layer.layer_id))
    else:
        draw_bake_units(layout, project)
        # Unbaked objects in a baked layer are no longer added from Setup;
        # older files may still have some, so they stay visible and removable.
        originals = [
            obj for obj in (layer_members(layer.layer_id) if layer else [])
            if obj.pm_vr_pipeline.processing_role == 'EXPORT_ORIGINAL'
        ]
        if originals:
            draw_original_objects(layout, layer, originals, can_add=False)


def draw_original_objects(layout, layer, objects, can_add=True):
    """Glass, Emissive and Runtime are not baked and have no units: the same
    place lists the layer's objects; + adds the selection, - takes it out."""
    box = layout.box()
    box.label(text="Objects" if can_add else "Unbaked Objects", icon='OBJECT_DATA')
    members = sorted(objects, key=lambda obj: obj.name.casefold())
    row = box.row()
    names = row.box().column(align=True)
    for obj in members[:8]:
        names.label(text=obj.name, icon='OBJECT_DATA')
    if len(members) > 8:
        names.label(text=f"and {len(members) - 8} more")
    if not members:
        names.label(text="No objects yet")
    controls = row.column(align=True)
    if can_add:
        op = controls.operator("pmvr.assign_selected_to_layer", text="", icon='ADD')
        op.role = 'EXPORT_ORIGINAL'
    op = controls.operator("pmvr.unassign_selected", text="", icon='REMOVE')
    op.layer_originals_only = True
    box.label(text="Not baked: exported as they are.", icon='INFO')


def draw_variants(layout, project, unit):
    """Material variants of the unit: one small button until there are any."""
    layer = find_layer(project, unit.render_layer_id)
    if not len(unit.variants):
        if layer and layer.layer_type == 'UNLIT':
            layout.operator("pmvr.add_bake_variant", text="Add Variant", icon='MATERIAL')
        return
    box = layout.box()
    header = box.row(align=True)
    header.label(text="Material Variants", icon='MATERIAL')
    header.operator("pmvr.add_bake_variant", text="", icon='ADD')
    problem = variant_problem(project, unit)
    if problem:
        row = box.row()
        row.alert = True
        row.label(text=problem[:1].upper() + problem[1:], icon='ERROR')
    box.prop(unit, "variant_material")
    box.prop(unit, "variant_default_title")
    for index, variant in enumerate(unit.variants):
        row = box.row(align=True)
        row.prop(variant, "title", text="")
        row.prop(variant, "material", text="")
        states = [variant_status(unit, variant, state) for state in ('DAY', 'EVENING')]
        status = row.row(align=True)
        status.ui_units_x = 1.6
        status.alert = "Rebake" in states
        status.label(text=("D" if states[0] else "·") + ("E" if states[1] else "·"))
        op = row.operator("pmvr.remove_bake_variant", text="", icon='X')
        op.index = index
    box.prop(unit, "variant_marker")


def draw_bake_units(layout, project):
    occupied = units_with_members()
    _OCCUPIED_UNITS.clear()
    _OCCUPIED_UNITS.update(occupied)
    units = layout.box()
    units.label(text="Bake Units", icon='UV')
    empty = sum(1 for unit in project.bake_units if unit.unit_id not in occupied)
    if empty:
        warning = units.row(align=True)
        label = warning.row(align=True)
        label.alert = True
        label.label(text=f"{empty} empty unit(s)", icon='ERROR')
        warning.operator("pmvr.remove_empty_units", text="Remove", icon='TRASH')
    row = units.row()
    row.template_list("PMVR_UL_BakeUnits", "", project, "bake_units", project, "active_bake_unit_index", rows=5)
    controls = row.column(align=True)
    controls.operator("pmvr.add_bake_unit", text="", icon='ADD')
    controls.operator("pmvr.remove_bake_unit", text="", icon='REMOVE')
    controls.separator()
    controls.operator("pmvr.select_all_units_for_resolution", text="", icon='CHECKBOX_HLT')
    units.label(text="Checked units are edited together.", icon='INFO')
    units.label(text="+ creates separate units; Shift-click + creates one shared unit.")
    unit = active_unit(project)
    if unit:
        detail = units.column(align=True)
        detail.prop(unit, "display_name")
        detail.prop(unit, "bake_scenario", text="Scenario")
        members = detail.row(align=True)
        members.label(text=f"Members: {len(unit_members(unit.unit_id))}")
        members.operator("pmvr.assign_selected_to_unit", text="", icon='ADD')
        members.operator("pmvr.remove_selected_from_unit", text="", icon='REMOVE')
        status_row = detail.row(align=True)
        status_row.label(text=f"Beauty D: {beauty_status(unit, 'DAY')}")
        status_row.label(text=f"E: {beauty_status(unit, 'EVENING')}")
        baked = last_baked(project, unit)
        if baked:
            detail.label(text="Last baked: " + ", ".join(f"{state} {time}" for state, time in baked))
        if SHOW_LIGHTMAP:
            lightmap_row = detail.row(align=True)
            lightmap_row.label(text=f"Lightmap D: {unit.day_lightmap_status or '—'}")
            lightmap_row.label(text=f"E: {unit.evening_lightmap_status or '—'}")
        row = detail.row(align=True)
        op = row.operator("pmvr.select_pipeline_items", text="Select Sources")
        op.target = 'UNIT_SOURCES'
        draw_variants(detail, project, unit)


def draw_scenarios(layout, context, project):
    box = layout.box()
    header = box.row(align=True)
    header.prop(
        project,
        "show_bake_scenarios",
        text="Bake Scenarios",
        icon='TRIA_DOWN' if project.show_bake_scenarios else 'TRIA_RIGHT',
        emboss=False,
    )
    if not project.show_bake_scenarios:
        header.label(text=f"{len(project.bake_scenarios)}")
        return
    body = box.column()
    # Scenario definitions stay fixed while a queue runs.
    body.enabled = not project.operation_running
    row = body.row()
    row.template_list(
        "PMVR_UL_BakeScenarios", "", project, "bake_scenarios",
        project, "active_bake_scenario_index", rows=3,
    )
    controls = row.column(align=True)
    controls.operator("pmvr.add_bake_scenario", text="", icon='ADD')
    controls.operator("pmvr.remove_bake_scenario", text="", icon='REMOVE')
    scenario = active_scenario(project)
    if not scenario:
        body.label(text="Switch collections in the outliner, then + to save.", icon='INFO')
        return
    buttons = body.row(align=True)
    op = buttons.operator("pmvr.capture_bake_scenario", text="Capture Outliner", icon='IMPORT')
    op.mode = 'ALL'
    buttons.operator("pmvr.show_bake_scenario", text="Show in Outliner", icon='HIDE_OFF')
    try:
        missing = Scope(project, context.view_layer).unrecorded(scenario)
    except PipelineBakeError:
        missing = []
    if missing:
        # Unrecorded collections keep their outliner state while baking.
        warning = body.row(align=True)
        label = warning.row(align=True)
        label.alert = True
        label.label(
            text=f"{len(missing)} new collection{'s' if len(missing) > 1 else ''}",
            icon='ERROR',
        )
        button = warning.row(align=True)
        button.ui_units_x = 3.5
        op = button.operator("pmvr.capture_bake_scenario", text="Add", icon='ADD')
        op.mode = 'MISSING'
    body.template_list(
        "PMVR_UL_ScenarioCollections", "", scenario, "collections",
        scenario, "active_collection_index", rows=5,
    )
    body.label(text="Day/Evening collections follow the Lighting switch.", icon='LIGHT')
    layers = [layer for layer in project.render_layers if layer.layer_type in BAKE_LAYER_TYPES]
    if layers:
        defaults = body.column(align=True)
        defaults.label(text="Layer defaults (units can override):", icon='RENDERLAYERS')
        for layer in layers:
            split = defaults.split(factor=0.4, align=True)
            split.label(text=layer.display_name)
            split.prop(layer, "bake_scenario", text="")


def draw_bake(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        layout.operator("pmvr.initialize_project", icon='PLAY')
        return
    # What the scene shows (lighting and baked results); which states the
    # queue bakes is chosen next to the Bake button.
    draw_state_switch(layout, project)
    if SHOW_LIGHTMAP or project.bake_mode != 'BEAUTY':
        # A file left in Lightmap mode keeps the switch so it can go back.
        layout.prop(project, "bake_mode", expand=True)
    draw_scenarios(layout, context, project)
    queue = layout.box()
    mode_label = "Beauty" if project.bake_mode == 'BEAUTY' else "Lightmap"
    queue.label(text=f"{mode_label} Unit Queue", icon='SEQ_STRIP_DUPLICATE')
    row = queue.row()
    row.template_list("PMVR_UL_BakeQueue", "", project, "bake_queue", project, "active_bake_queue_index", rows=6)
    controls = row.column(align=True)
    controls.operator("pmvr.remove_queue_entry", text="", icon='REMOVE')
    controls.separator()
    op = controls.operator("pmvr.move_queue_entry", text="", icon='TRIA_UP')
    op.direction = 'UP'
    op = controls.operator("pmvr.move_queue_entry", text="", icon='TRIA_DOWN')
    op.direction = 'DOWN'
    day_done = sum(entry.day_done for entry in project.bake_queue)
    evening_done = sum(entry.evening_done for entry in project.bake_queue)
    if day_done or evening_done:
        queue.label(
            text=f"Already baked in this queue: Day {day_done}, Evening {evening_done}; Bake continues",
            icon='CHECKMARK',
        )
    buttons = queue.row(align=True)
    buttons.operator("pmvr.queue_selected_units", icon='RESTRICT_SELECT_OFF')
    buttons.operator("pmvr.clear_bake_queue", icon='TRASH')
    test = test_resolution_label(project)
    test_resolution = queue.row(align=True)
    test_resolution.alert = bool(test)
    test_resolution.label(text="Test:")
    test_resolution.prop(project, "test_resolution", expand=True)
    sizes = queue.row()
    sizes.alert = bool(test)
    sizes.label(
        text=f"Bakes at {short_resolution(queue_bake_size(project))}; export scales to unit size",
        icon='IMAGE_DATA',
    )
    states = queue.row(align=True)
    states.label(text="Bake for:")
    states.prop(project, "bake_day", text="Day")
    states.prop(project, "bake_evening", text="Evening")
    run = queue.row()
    run.scale_y = 1.4
    if project.operation_running:
        run.operator("pmvr.cancel_bake_queue", text="Cancel (Esc)", icon='CANCEL')
    else:
        run.operator(
            "pmvr.bake_queue",
            text=" • ".join(
                [f"Bake {len(project.bake_queue)} Queued Unit(s)"]
                + ([mode_label] if SHOW_LIGHTMAP or project.bake_mode != 'BEAUTY' else [])
                + ([f"Test {test}"] if test else [])
            ),
            icon='RENDER_STILL',
        )
    draw_probes(layout, project)
    if project.last_operation_summary:
        layout.label(text=project.last_operation_summary, icon='INFO')

    preview = layout.box()
    preview.label(text="Preview", icon='HIDE_OFF')
    row = preview.row(align=True)
    row.prop(project, "show_sources", toggle=True)
    row.prop(project, "show_generated", toggle=True)


def draw_probes(layout, project):
    box = layout.box()
    states = probe_states(project)
    cameras = {camera.name for state in states for camera in probe_cameras(project, state)}
    jobs = sum(len(probe_cameras(project, state)) for state in states)
    box.label(text=f"Probes: {len(cameras)} panoramic camera(s) in Runtime", icon='WORLD')
    row = box.row()
    row.enabled = bool(jobs)
    row.operator(
        "pmvr.render_probes",
        text=f"Render {jobs} Probe(s)" + (f" • {' + '.join(s.title() for s in states)}" if states else ""),
        icon='RENDER_STILL',
    )
    box.label(text=f"EXR Half ZIP -> {probe_directory(project)}", icon='FILE_FOLDER')


def draw_export(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        layout.operator("pmvr.initialize_project", icon='PLAY')
        return
    draw_state_switch(layout, project)
    box = layout.box()
    box.label(text="Semantic Render Layers", icon='EXPORT')
    box.template_list("PMVR_UL_RenderLayers", "export", project, "render_layers", project, "active_render_layer_index", rows=7)
    layer = active_layer(project)
    if layer:
        row = box.row(align=True)
        row.prop(layer, "export_usdz", toggle=True)
        row.prop(layer, "export_glb", toggle=True)
        suffix = "" if project.active_lighting_state == 'DAY' else "_Evening"
        box.label(text=f"Output: {layer.display_name}{suffix}", icon='FILE')
        extras = box.box()
        guests = extra_export_members(layer.layer_id)
        extras.label(text=f"Additional objects in this export: {len(guests)}", icon='EXPORT')
        row = extras.row(align=True)
        row.enabled = bool(context.selected_objects)
        op = row.operator("pmvr.edit_extra_exports", text="Include Selected", icon='ADD')
        op.action = 'ADD'
        op = row.operator("pmvr.edit_extra_exports", text="Remove Selected", icon='REMOVE')
        op.action = 'REMOVE'
        if guests:
            for obj in sorted(guests, key=lambda item: item.name.casefold()):
                guest_row = extras.row(align=True)
                guest_row.label(text=obj.name, icon='OBJECT_DATA')
                op = guest_row.operator("pmvr.edit_extra_exports", text="", icon='X')
                op.action = 'REMOVE'
                op.source_id = obj.pm_vr_pipeline.source_id
            op = extras.operator("pmvr.select_pipeline_items", text="Select Additional Objects", icon='RESTRICT_SELECT_OFF')
            op.target = 'LAYER_EXPORT_GUESTS'
        else:
            extras.label(text="Select sources from another layer of this type.", icon='INFO')
    buttons = layout.row(align=True)
    for format_name in ('USDZ', 'GLB', 'BOTH'):
        op = buttons.operator("pmvr.export_semantic_layers", text="Both" if format_name == 'BOTH' else format_name, icon='EXPORT')
        op.export_format = format_name
    layout.label(text="Bake members use generated Beauty; Export Original members stay untouched.", icon='INFO')
    if project.last_operation_summary:
        layout.label(text=project.last_operation_summary, icon='INFO')


HELP_SECTIONS = (
    ("Names", 'SORTALPHA', (
        "Object, mesh, material names: anything (identity uses IDs)",
        "Renaming after a bake is safe",
        "UV channels: 1st UVMap, 2nd SimpleBake",
        "Layer name = export file: Name.usdz, Name_Evening.usdz",
        "Layer names must differ (case-insensitive)",
        "Unit name = Beauty PNG name; clashes get an ID suffix",
        "Generated object = source name + .001, used in export",
    )),
    ("Texel density", 'UV', (
        "Target: ~10 px/cm (objects seen up close)",
        "Walls, floors, ceilings: 5 acceptable, 7 very good",
        "Large ceilings: ~4 px/cm is normal",
        "~20 px/cm: too much, merge into a shared unit",
        "Far below target: too low",
        "Target TD (Project Settings) drives Scene Debug colours",
    )),
    ("Scene", 'OUTLINER_COLLECTION', (
        "Assigned objects: inside Source Root",
        "Unassigned: N selects visible objects without a layer",
        "Lists: + adds the selection, - takes it out of the layer",
        "+ moves objects from other layers; Shift+ merges one unit",
        "Unit Members +/-: add or remove selected objects of a unit",
        "Glass, Emissive, Runtime: objects export unbaked",
        "PMVR_GENERATED, PMVR_WORK: managed by PM VR, keep yours out",
        "Shift+D, Alt+D, copy/paste: the copy gets its own ID",
        "Copy of a baked object: same layer, needs its own unit",
        "Copy of a generated object: becomes an ordinary object",
        "Linked duplicates: separate units only (shared UVs)",
        "Object-linked materials: supported",
    )),
    ("Bake", 'RENDER_STILL', (
        "Relative output folders (//): save the .blend first",
        "Unit members: all visible or all hidden in the state",
        "PBR: one Principled BSDF per material slot",
        "PBR colour corrections: Shader Editor, Flatten to Texture",
        "Island Padding (Project Settings): same as in the UV packer",
        "Alpha: opacity from Principled Alpha or a Transparent mix",
        "Alpha opacity may come through a node group input",
        "Lighting Day/Evening (top): shows that state's bake",
        "A lighting state switches on with all its nested collections",
        "Modifiers must not add or remove material slots",
        "Setup layer Queue N Units: the whole layer into the queue",
        "Esc or Cancel: stops the queue, current unit discarded",
        "A unit leaves the queue once baked for the checked states",
        "Stopped queue: Save; after reopening Bake continues the rest",
        "Bake Resolution (Project Settings): size of the baked files",
        "Unit resolution can change after a bake: no rebake needed",
        "Layers and units are locked while baking",
        "Unit Add Variant: material variants (Unlit, one object)",
        "Variants bake after their unit, per lighting state",
        "Variant row DE: baked for Day / Evening; red: rebake",
    )),
    ("Bake scenarios", 'OUTLINER_COLLECTION', (
        "Scenario: collections on/off inside Source Root for a bake",
        "+ saves the outliner as it is; Capture Outliner updates it",
        "Layer sets the default; a unit can override it",
        "Checked units change scenario together",
        "Day/Evening collections follow the Lighting switch",
        "Disabling a collection disables everything inside it",
        "New collections join every scenario as they are in the outliner",
        "Outliner is restored after the queue, cancel or a crash",
        "A scenario must not disable the unit's own objects",
    )),
    ("Export", 'EXPORT', (
        "Every unit needs a Ready Beauty for the active state",
        "Atlases scale to unit resolution (linear light, colour kept)",
        "Variants: USDZ/Variants/<Object>_<Variant>.usdz, swatch, JSON",
        "Day and Evening must have matching structure",
        "Additional exports: same layer type only",
        "Selection and visibility are ignored: all assigned objects export",
        "Only inside Evening lighting collection: Evening export only",
        "USD: st = baked atlas (SimpleBake), UVMap = authored layout",
        "Each USDZ is checked after writing; problems count as failed",
    )),
    ("Probes", 'WORLD', (
        "Probe: panoramic equirectangular camera in a Runtime layer",
        "Render Probes (Bake): one EXR per camera and checked state",
        "File: the camera's USD name, _Evening for Evening",
        "EXR Half ZIP (Apple cannot read DWAA)",
        "Lit like the bake: generated results hidden, state lighting on",
        "World-aligned (+Y ahead, Z up): camera rotation is ignored",
        "Folder: Project Settings, default probes/ next to the USDZ folder",
    )),
)


class PMVR_OT_ShowHelp(bpy.types.Operator):
    bl_idname = "pmvr.show_help"
    bl_label = "PM VR Rules"
    bl_description = "Naming and scene rules the pipeline relies on"

    def draw(self, _context):
        layout = self.layout
        for title, icon, lines in HELP_SECTIONS:
            box = layout.box()
            box.label(text=title, icon=icon)
            column = box.column(align=True)
            for line in lines:
                column.label(text=f"•  {line}")

    def invoke(self, context, _event):
        return context.window_manager.invoke_popup(self, width=440)

    def execute(self, _context):
        return {'FINISHED'}


CLASSES = (
    PMVR_UL_RenderLayers,
    PMVR_UL_BakeUnits,
    PMVR_UL_BakeQueue,
    PMVR_UL_BakeScenarios,
    PMVR_UL_ScenarioCollections,
    PMVR_OT_ShowHelp,
)
