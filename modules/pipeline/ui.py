"""Four-stage pipeline UI lists and stage drawing."""

import bpy

from .identity import find_layer, find_unit, layer_members, unit_members
from .setup_ops import active_layer, active_unit
from . import viewport_overlay


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
        row.label(text=item.bl_rna.properties["processing_profile"].enum_items[item.processing_profile].name)


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
        row.prop(item, "batch_selected", text="")
        row.prop(item, "display_name", text="", emboss=False, icon='UV')
        row.prop(item, "resolution", text="")


class PMVR_UL_BakeQueue(bpy.types.UIList):
    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        unit = find_unit(context.scene.pm_vr_project, item.unit_id)
        row = layout.row(align=True)
        row.label(text=unit.display_name if unit else "Missing unit", icon='UV' if unit else 'ERROR')
        if unit:
            row.label(text=unit.resolution)


def draw_setup(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        box = layout.box()
        box.label(text="Initialize the semantic pipeline in this .blend", icon='INFO')
        box.operator("pmvr.initialize_project", icon='PLAY')
        return

    draw_state_switch(layout, project)
    viewport_overlay.draw_controls(layout, project)

    layers = layout.box()
    layers.label(text="Render Layers", icon='RENDERLAYERS')
    row = layers.row()
    row.template_list("PMVR_UL_RenderLayers", "", project, "render_layers", project, "active_render_layer_index", rows=5)
    controls = row.column(align=True)
    controls.operator("pmvr.add_render_layer", text="", icon='ADD')
    controls.operator("pmvr.remove_render_layer", text="", icon='REMOVE')
    layer = active_layer(project)
    if layer:
        detail = layers.column(align=True)
        detail.prop(layer, "display_name")
        detail.prop(layer, "processing_profile")
        formats = detail.row(align=True)
        formats.prop(layer, "export_usdz", toggle=True)
        formats.prop(layer, "export_glb", toggle=True)
        assign = detail.row(align=True)
        op = assign.operator("pmvr.assign_selected_to_layer", text="Export Original", icon='OBJECT_DATA')
        op.role = 'EXPORT_ORIGINAL'
        assign.operator("pmvr.unassign_selected", text="Unassign", icon='X')
        nav = detail.row(align=True)
        op = nav.operator("pmvr.select_pipeline_items", text=f"Select {len(layer_members(layer.layer_id))} Sources")
        op.target = 'LAYER_SOURCES'

    units = layout.box()
    units.label(text="Bake Units", icon='UV')
    row = units.row()
    row.template_list("PMVR_UL_BakeUnits", "", project, "bake_units", project, "active_bake_unit_index", rows=5)
    controls = row.column(align=True)
    controls.operator("pmvr.add_bake_unit", text="", icon='ADD')
    controls.operator("pmvr.remove_bake_unit", text="", icon='REMOVE')
    controls.separator()
    controls.operator("pmvr.select_all_units_for_resolution", text="", icon='CHECKBOX_HLT')
    units.label(text="Drag over checkboxes to build a resolution batch.", icon='INFO')
    units.label(text="+ creates separate units; Shift-click + creates one shared unit.")
    unit = active_unit(project)
    if unit:
        detail = units.column(align=True)
        detail.prop(unit, "display_name")
        detail.label(text=f"Members: {len(unit_members(unit.unit_id))}")
        status_row = detail.row(align=True)
        status_row.label(text=f"Beauty D: {unit.day_status or '—'}")
        status_row.label(text=f"E: {unit.evening_status or '—'}")
        lightmap_row = detail.row(align=True)
        lightmap_row.label(text=f"Lightmap D: {unit.day_lightmap_status or '—'}")
        lightmap_row.label(text=f"E: {unit.evening_lightmap_status or '—'}")
        row = detail.row(align=True)
        op = row.operator("pmvr.select_pipeline_items", text="Select Sources")
        op.target = 'UNIT_SOURCES'

    selected = context.active_object
    if selected and hasattr(selected, "pm_vr_pipeline") and selected.pm_vr_pipeline.is_registered_source:
        meta = selected.pm_vr_pipeline
        selected_box = layout.box()
        selected_box.label(text=f"Active Source: {selected.name}", icon='OBJECT_DATA')
        selected_layer = find_layer(project, meta.render_layer_id)
        selected_unit = find_unit(project, meta.bake_unit_id)
        selected_box.label(text=f"Layer: {selected_layer.display_name if selected_layer else 'Unassigned'}")
        selected_box.label(text=f"Role: {meta.bl_rna.properties['processing_role'].enum_items[meta.processing_role].name}")
        if meta.processing_role == 'BAKE':
            selected_box.label(text=f"Unit: {selected_unit.display_name if selected_unit else 'Missing'}")

def draw_bake(layout, context):
    project = context.scene.pm_vr_project
    if not project.initialized:
        layout.operator("pmvr.initialize_project", icon='PLAY')
        return
    states = layout.row(align=True)
    states.label(text="Bake:")
    states.prop(project, "bake_day", text="Day", icon='LIGHT_SUN', toggle=True)
    states.prop(project, "bake_evening", text="Evening", icon='LIGHT', toggle=True)
    layout.prop(project, "bake_mode", expand=True)
    viewport_overlay.draw_controls(layout, project)
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
    buttons = queue.row(align=True)
    buttons.operator("pmvr.queue_selected_units", icon='RESTRICT_SELECT_OFF')
    buttons.operator("pmvr.clear_bake_queue", icon='TRASH')
    test_resolution = queue.row(align=True)
    test_resolution.label(text="Test Resolution:")
    op = test_resolution.operator("pmvr.scale_queued_resolution", text="÷2")
    op.direction = 'HALF'
    op = test_resolution.operator("pmvr.scale_queued_resolution", text="×2")
    op.direction = 'DOUBLE'
    run = queue.row()
    run.enabled = not project.operation_running
    run.scale_y = 1.4
    run.operator(
        "pmvr.bake_queue",
        text=f"Bake {len(project.bake_queue)} Queued Unit(s) • {mode_label}",
        icon='RENDER_STILL',
    )
    if project.last_operation_summary:
        layout.label(text=project.last_operation_summary, icon='INFO')

    preview = layout.box()
    preview.label(text=f"Preview: {project.active_lighting_state.title()} {mode_label}", icon='HIDE_OFF')
    row = preview.row(align=True)
    row.prop(project, "show_sources", toggle=True)
    row.prop(project, "show_generated", toggle=True)
    preview.operator("pmvr.apply_preview_visibility", icon='FILE_REFRESH')
    nav = preview.row(align=True)
    op = nav.operator("pmvr.select_pipeline_items", text="Active Unit Sources")
    op.target = 'UNIT_SOURCES'
    op = nav.operator("pmvr.select_pipeline_items", text="Select Baked Output")
    op.target = 'UNIT_GENERATED'


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
    buttons = layout.row(align=True)
    for format_name in ('USDZ', 'GLB', 'BOTH'):
        op = buttons.operator("pmvr.export_semantic_layers", text="Both" if format_name == 'BOTH' else format_name, icon='EXPORT')
        op.export_format = format_name
    layout.label(text="Bake members use generated Beauty; Export Original members stay untouched.", icon='INFO')
    if project.last_operation_summary:
        layout.label(text=project.last_operation_summary, icon='INFO')


CLASSES = (PMVR_UL_RenderLayers, PMVR_UL_BakeUnits, PMVR_UL_BakeQueue)
