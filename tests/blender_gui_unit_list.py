"""Interactive regression: the Setup unit list after removing and unassigning.

Needs a real window (the failures happen while the panel draws):

    blender --factory-startup --python tests/blender_gui_unit_list.py

Units of two layers are interleaved in the unit collection. Removing a unit
must leave a unit of the same layer active, and drawing the panel must not
raise. Unassigning every member of a unit must not leave an empty unit behind
(recreating the unit then showed a duplicate name). A unit emptied by deleting
its object is flagged, and Remove Empty Units clears it.
Prints PM_VR_GUI_UNIT_LIST_OK or PM_VR_GUI_UNIT_LIST_FAILED and quits.
"""

import os
import sys
import traceback

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import setup_ops, ui  # noqa: E402

STATE = {"step": 0, "problems": [], "raised": [], "draws": 0}


def recording(function, label):
    """Blender prints panel and poll exceptions itself; record them here."""

    def wrapper(*args, **kwargs):
        if label == "draw_setup":
            STATE["draws"] += 1
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            STATE["raised"].append(f"{label}: {exc}")
            raise

    return wrapper


def project():
    return bpy.context.scene.pm_vr_project


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return window, area


def redraw():
    _window, area = view3d()
    area.tag_redraw()


def layer_index(name):
    return next(i for i, layer in enumerate(project().render_layers) if layer.display_name == name)


def select(names):
    bpy.ops.object.select_all(action='DESELECT')
    for name in names:
        bpy.data.objects[name].select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects[names[0]]


def new_units(layer, names):
    project().active_render_layer_index = layer_index(layer)
    select(names)
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}


def units(layer=None):
    layer_id = project().render_layers[layer_index(layer)].layer_id if layer else None
    return [u.display_name for u in project().bake_units if not layer_id or u.render_layer_id == layer_id]


def check(condition, message):
    if not condition:
        STATE["problems"].append(message)


def setup():
    PM_VR.register()
    # Show the panel on the sidebar tab a fresh window opens with.
    bpy.utils.unregister_class(PM_VR.PMVR_PT_MainPanel)
    PM_VR.PMVR_PT_MainPanel.bl_category = "Item"
    bpy.utils.register_class(PM_VR.PMVR_PT_MainPanel)
    ui.draw_setup = recording(ui.draw_setup, "draw_setup")
    ui.active_unit = recording(ui.active_unit, "active_unit (draw)")
    setup_ops.active_unit = recording(setup_ops.active_unit, "active_unit (poll/operator)")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    root = bpy.data.collections.new("Root")
    scene.collection.children.link(root)
    scene.pm_vr_project.source_root_collection = root
    bpy.ops.pmvr.initialize_project()
    for index, name in enumerate(("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")):
        bpy.ops.mesh.primitive_cube_add(location=(index * 3, 0, 0))
        obj = bpy.context.object
        obj.name = name
        obj.data.uv_layers[0].name = "UVMap"
        obj.data.uv_layers.new(name="SimpleBake")
    new_units("Unlit", ["A"])
    new_units("PBR", ["B"])
    new_units("Unlit", ["C"])
    new_units("Unlit", ["D"])
    window, area = view3d()
    area.spaces.active.show_region_ui = True
    # The maximized view opens its sidebar on the add-on's tab.
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.screen.screen_full_area()
    scene.pm_vr_ui_state.stage = 'SETUP'
    project().active_render_layer_index = layer_index("Unlit")


def draw_errors():
    if not STATE["draws"]:
        return ["the Setup panel was not drawn"]
    errors = list(STATE["raised"])
    STATE["raised"].clear()
    STATE["draws"] = 0
    return errors


def tick():
    try:
        STATE["step"] += 1
        step = STATE["step"]
        if step == 1:
            setup()
            redraw()
            return 1.0
        if step == 2:
            check(not draw_errors(), "panel raised before any edit")
            # Remove A: the next unit in the collection belongs to PBR.
            project().active_bake_unit_index = 0
            assert bpy.ops.pmvr.remove_bake_unit() == {'FINISHED'}
            active = project().bake_units[project().active_bake_unit_index]
            check(active.display_name == "C", f"after removing A the active unit is {active.display_name}")
            redraw()
            return 0.5
        if step == 3:
            errors = draw_errors()
            check(not errors, f"panel raised after removing a unit: {errors}")
            meta = bpy.data.objects["A"].pm_vr_pipeline
            check(
                not meta.render_layer_id and meta.processing_role == 'UNASSIGNED',
                "removing unit A left its object in the layer",
            )
            # Unassign C: its unit must not stay behind empty.
            select(["C"])
            bpy.ops.pmvr.unassign_selected()
            check(units("Unlit") == ["D"], f"unassign left units {units('Unlit')}")
            new_units("Unlit", ["C"])
            check(units("Unlit") == ["D", "C"], f"recreating C gave {units('Unlit')}")
            # A unit emptied outside PM VR (object deleted) is flagged and removable.
            new_units("Unlit", ["E"])
            bpy.data.objects.remove(bpy.data.objects["E"], do_unlink=True)
            check("E" in units("Unlit"), "unit E vanished on its own")
            redraw()
            return 0.5
        if step == 4:
            errors = draw_errors()
            check(not errors, f"panel raised with an empty unit: {errors}")
            assert bpy.ops.pmvr.remove_empty_units() == {'FINISHED'}
            check(units("Unlit") == ["D", "C"], f"remove empty units left {units('Unlit')}")
            check(units("PBR") == ["B"], f"PBR units changed: {units('PBR')}")
            # Stale index pointing into another layer: drawing must stay quiet.
            project().active_bake_unit_index = units().index("B")
            redraw()
            return 0.5
        if step == 5:
            errors = draw_errors()
            check(not errors, f"panel raised with the active unit in another layer: {errors}")
            unlit_id = project().render_layers[layer_index("Unlit")].layer_id
            # Members + / - of a shared unit.
            project().active_render_layer_index = layer_index("Unlit")
            select(["F", "G"])
            assert bpy.ops.pmvr.add_bake_unit(merge_selected=True) == {'FINISHED'}
            shared = project().bake_units[project().active_bake_unit_index].unit_id
            select(["G"])
            assert bpy.ops.pmvr.remove_selected_from_unit() == {'FINISHED'}
            g = bpy.data.objects["G"].pm_vr_pipeline
            check(not g.render_layer_id and not g.bake_unit_id, "G stayed in the layer after Members -")
            assert bpy.ops.pmvr.assign_selected_to_unit() == {'FINISHED'}
            check(g.bake_unit_id == shared and g.render_layer_id == unlit_id, "Members + did not add G back")
            select(["F", "G"])
            assert bpy.ops.pmvr.remove_selected_from_unit() == {'FINISHED'}
            check(not any(u.unit_id == shared for u in project().bake_units), "emptied shared unit stayed")
            # An unbaked object in a baked layer from an older file: listed, and
            # its - leaves the baked objects of the layer alone.
            h = bpy.data.objects["H"].pm_vr_pipeline
            h.source_id, h.is_registered_source = "legacy_h", True
            h.render_layer_id, h.processing_role = unlit_id, 'EXPORT_ORIGINAL'
            # Left in a layer without a role by the old unit removal.
            i = bpy.data.objects["I"].pm_vr_pipeline
            i.source_id, i.is_registered_source = "legacy_i", True
            i.render_layer_id, i.processing_role = unlit_id, 'UNASSIGNED'
            check(setup_ops.release_roleless_members() == 1 and not i.render_layer_id, "roleless member not released")
            redraw()
            return 0.5
        if step == 6:
            errors = draw_errors()
            check(not errors, f"panel raised with an unbaked object in a baked layer: {errors}")
            select(["H", "D"])
            assert bpy.ops.pmvr.unassign_selected(layer_originals_only=True) == {'FINISHED'}
            check(not bpy.data.objects["H"].pm_vr_pipeline.render_layer_id, "H stayed after -")
            check(bpy.data.objects["D"].pm_vr_pipeline.processing_role == 'BAKE', "- of unbaked objects removed D")
            # Glass: + adds as original, - removes it again.
            project().active_render_layer_index = layer_index("Glass")
            select(["J"])
            assert bpy.ops.pmvr.assign_selected_to_layer(role='EXPORT_ORIGINAL') == {'FINISHED'}
            check(bpy.data.objects["J"].pm_vr_pipeline.processing_role == 'EXPORT_ORIGINAL', "Glass + failed")
            redraw()
            return 0.5
        if step == 7:
            errors = draw_errors()
            check(not errors, f"panel raised on the Glass layer: {errors}")
            select(["J"])
            assert bpy.ops.pmvr.unassign_selected(layer_originals_only=True) == {'FINISHED'}
            check(not bpy.data.objects["J"].pm_vr_pipeline.render_layer_id, "Glass - failed")
            # + moves: D goes from its Unlit unit to a new PBR unit.
            project().active_render_layer_index = layer_index("PBR")
            select(["D"])
            assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
            check(units("PBR") == ["B", "D"] and "D" not in units("Unlit"), f"+ did not move D: {units()}")
            # + again on objects that already have a unit here changes nothing.
            try:
                result = bpy.ops.pmvr.add_bake_unit()
            except RuntimeError as exc:
                result = {str(exc)}
            check(result == {'CANCELLED'} and units("PBR") == ["B", "D"], f"second + changed units: {result} {units()}")
            # Shift+ merges B and D into one shared unit.
            select(["B", "D"])
            assert bpy.ops.pmvr.add_bake_unit(merge_selected=True) == {'FINISHED'}
            pbr = units("PBR")
            check(len(pbr) == 1 and pbr[0].startswith("PBR Unit"), f"Shift+ did not merge: {pbr}")
            b, d = bpy.data.objects["B"].pm_vr_pipeline, bpy.data.objects["D"].pm_vr_pipeline
            check(b.bake_unit_id == d.bake_unit_id != "", "B and D do not share the merged unit")
            try:
                result = bpy.ops.pmvr.add_bake_unit(merge_selected=True)
            except RuntimeError as exc:
                result = {str(exc)}
            check(result == {'CANCELLED'}, f"Shift+ on an existing shared unit recreated it: {result}")
            # Preview switches apply at once, also with a source in a disabled collection.
            hidden = bpy.data.collections.new("Disabled")
            bpy.context.scene.collection.children.link(hidden)
            hidden.objects.link(bpy.data.objects["C"])
            bpy.data.objects["C"].users_collection[0].objects.unlink(bpy.data.objects["C"])
            bpy.context.view_layer.layer_collection.children["Disabled"].exclude = True
            project().show_sources = False
            check(bpy.data.objects["B"].hide_get(), "Show Sources off did not hide B")
            project().show_sources = True
            check(not bpy.data.objects["B"].hide_get(), "Show Sources on did not show B")
            # Select Unassigned: visible objects inside Source Root without a layer.
            root = project().source_root_collection
            off = bpy.data.collections.new("Off")
            root.children.link(off)
            made = {}
            for name, collection in (("K", root), ("L", root), ("M", root), ("O", off)):
                obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
                collection.objects.link(obj)
                made[name] = obj
            lamp = bpy.data.objects.new("N", bpy.data.lights.new("N", 'POINT'))
            root.objects.link(lamp)
            made["L"].hide_set(True)
            project().active_render_layer_index = layer_index("Glass")
            select(["M"])
            bpy.ops.pmvr.assign_selected_to_layer(role='EXPORT_ORIGINAL')
            bpy.context.view_layer.layer_collection.children["Root"].children["Off"].exclude = True
            assert bpy.ops.pmvr.select_pipeline_items(target='UNASSIGNED') == {'FINISHED'}
            picked = sorted(obj.name for obj in bpy.context.selected_objects)
            check(picked == ["K"], f"Select Unassigned picked {picked}")
            redraw()
            return 0.5
        errors = draw_errors()
        check(not errors, f"panel raised after moving and merging: {errors}")
        from PM_VR.modules.pipeline import validation
        messages = [issue.message for issue in validation.validate_all(bpy.context)]
        check(not any("role is Unassigned" in m for m in messages), f"validation: {messages}")
        for problem in STATE["problems"]:
            print(f"[PM VR GUI] FAIL: {problem}")
        print("PM_VR_GUI_UNIT_LIST_FAILED" if STATE["problems"] else "PM_VR_GUI_UNIT_LIST_OK")
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        traceback.print_exc()
        for problem in STATE["problems"]:
            print(f"[PM VR GUI] FAIL: {problem}")
        print("PM_VR_GUI_UNIT_LIST_FAILED")
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1.5)
