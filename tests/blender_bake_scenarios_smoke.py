"""Bake scenarios: collections switched per unit while the queue bakes.

Run with Blender --background --factory-startup --python this_file.py.

The scene has a Kitchen counter shadowed by a canopy that lives in the Bedroom
collection. Baking the counter with the Kitchen scenario (Bedroom off) must be
visibly brighter than baking it with the outliner as it is. Also covers:
capture scope (lighting collections and the collection holding them are left
out), layer default plus unit override, the pre-start check that refuses a
scenario hiding the unit's own objects, parent/child propagation, exact
restore of the outliner (including children remembered as excluded), recovery
of a file saved mid-bake, collections created after capture, scenario removal,
batch override through checked units, and Validate Pipeline.
"""

import os
import sys
import tempfile

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, scenarios, validation  # noqa: E402
from PM_VR.modules.pipeline.constants import SCENARIO_NONE  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402


def layer_collection(name, root=None):
    root = root or bpy.context.view_layer.layer_collection
    for child in root.children:
        if child.collection.name == name:
            return child
        found = layer_collection(name, child)
        if found:
            return found
    return None


def excluded(name):
    return layer_collection(name).exclude


def all_flags():
    """Exclude flags under the source root (PMVR_GENERATED appears after the first bake)."""
    return {
        key: lc.exclude
        for key, lc in scenarios._walk(bpy.context.view_layer.layer_collection)
        if key[0] == "Scn Root"
    }


def add_plane(collection, name, location, size=2.0, color=(0.8, 0.8, 0.8)):
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.uv_layers[0].name = "UVMap"
    bake_uv = obj.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    material = bpy.data.materials.new(f"{name} Material")
    material.use_nodes = True
    node = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs["Base Color"].default_value = (*color, 1.0)
    obj.data.materials.append(material)
    return obj


def child(parent, name):
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    project.cycles_samples = 4
    project.bake_resolution = '256'
    project.uv_padding = 0.008
    output = tempfile.mkdtemp(prefix="pmvr_scenarios_")
    project.beauty_output_directory = output + os.sep
    root = bpy.data.collections.new("Scn Root")
    scene.collection.children.link(root)
    lights = child(root, "Lights")
    day = child(lights, "Day")
    evening = child(lights, "Evening")
    extras = child(lights, "Lamp Extras")
    kitchen = child(root, "Kitchen")
    props = child(kitchen, "Kitchen Props")
    bedroom = child(root, "Bedroom")
    archive = child(root, "Archive")
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("Scn Day World")
    project.evening_world = bpy.data.worlds.new("Scn Evening World")
    for world in (project.day_world, project.evening_world):
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.05
    sun = bpy.data.objects.new("Scn Sun", bpy.data.lights.new("Scn Sun", 'SUN'))
    sun.data.energy = 4.0
    day.objects.link(sun)
    lamp = bpy.data.objects.new("Scn Lamp", bpy.data.lights.new("Scn Lamp", 'POINT'))
    lamp.data.energy = 500.0
    lamp.location = (0, 0, 3)
    evening.objects.link(lamp)
    extras.objects.link(bpy.data.objects.new("Scn Extra", None))

    counter = add_plane(kitchen, "Counter", (0, 0, 0))
    mug = add_plane(props, "Mug", (0, 0, 0.3), size=0.2)
    bed = add_plane(bedroom, "Bed", (10, 0, 0))
    canopy = add_plane(bedroom, "Canopy", (0, 0, 2), size=6.0)
    add_plane(archive, "Old Roof", (10, 0, 2), size=6.0)
    sign = add_plane(evening, "Evening Sign", (-10, 0, 0))

    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = "LO"
    units = {}
    for name, obj in (("K", counter), ("B", bed), ("E", sign)):
        unit = project.bake_units.add()
        unit.unit_id = new_id()
        unit.artifact_key = unit.unit_id
        unit.display_name = name
        unit.render_layer_id = layer.layer_id
        unit.resolution = '256'
        metadata = obj.pm_vr_pipeline
        metadata.source_id = new_id()
        metadata.is_registered_source = True
        metadata.render_layer_id = layer.layer_id
        metadata.processing_role = 'BAKE'
        metadata.bake_unit_id = unit.unit_id
        units[name] = unit.unit_id

    activate_state(bpy.context, 'DAY')
    # Working state: Kitchen Props switched off on its own, Archive off.
    layer_collection("Kitchen Props").exclude = True
    layer_collection("Archive").exclude = True
    blend = os.path.join(output, "scenarios.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    return output, blend, units, (counter, mug, bed, canopy)


def project():
    return bpy.context.scene.pm_vr_project


def unit(unit_id):
    return next(u for u in project().bake_units if u.unit_id == unit_id)


def scenario_named(name):
    return next(s for s in project().bake_scenarios if s.display_name == name)


def new_scenario(name):
    assert bpy.ops.pmvr.add_bake_scenario() == {'FINISHED'}
    scenario = project().bake_scenarios[-1]
    scenario.display_name = name
    return scenario


def bake_job(session, unit_id, state='DAY'):
    """One queue job, in the queue's order: state, scenario, then the unit."""
    activate_state(bpy.context, state)
    session.apply(bpy.context, unit(unit_id))
    runtime = bake.BeautyBakeRuntime(bpy.context, unit(unit_id))
    try:
        if runtime.prepare() == "SKIPPED":
            runtime.cleanup(keep_image=False)
            return "SKIPPED"
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        runtime.finish()
        return "SUCCESS"
    except Exception as exc:
        runtime.fail(exc)
        raise


def mean_brightness(path):
    import numpy as np

    image = bpy.data.images.load(path, check_existing=False)
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    pixels = pixels.reshape(-1, 4)
    return float(pixels[pixels[:, 3] > 0.5][:, :3].mean())


def main():
    PM_VR.register()
    output, blend, units, (counter, mug, bed, canopy) = build()
    original = all_flags()

    # Capture: only collections a scenario may switch.
    layer_collection("Bedroom").exclude = True
    kitchen_scn = new_scenario("Kitchen")
    layer_collection("Bedroom").exclude = False
    layer_collection("Kitchen").exclude = True
    bedroom_scn = new_scenario("Bedroom")
    layer_collection("Kitchen").exclude = False
    layer_collection("Kitchen Props").exclude = True  # re-excluded by the parent toggle above
    recorded = [item.collection.name for item in kitchen_scn.collections]
    assert recorded == ["Lamp Extras", "Kitchen", "Kitchen Props", "Bedroom", "Archive"], recorded
    states = {item.collection.name: item.include for item in kitchen_scn.collections}
    assert states == {
        "Lamp Extras": True, "Kitchen": True, "Kitchen Props": False,
        "Bedroom": False, "Archive": False,
    }, states
    assert [item.depth for item in kitchen_scn.collections] == [0, 0, 1, 0, 0]
    assert all_flags() == original, "capture must not change the outliner"
    print("scenarios: capture scope OK")

    # Layer default, then the pre-start check refuses Bed hidden by its own scenario.
    layer = project().render_layers[0]
    layer.bake_scenario = kitchen_scn.scenario_id
    assert layer.bake_scenario_id == kitchen_scn.scenario_id
    assert unit(units["B"]).bake_scenario == 'LAYER'
    problems = scenarios.preflight(bpy.context, list(units.values()), ['DAY', 'EVENING'])
    assert len(problems) == 1 and '"Bed" (Bedroom)' in problems[0] and '"B"' in problems[0], problems
    assert project().active_lighting_state == 'DAY'
    issues = [i.message for i in validation.validate_all(bpy.context) if i.severity == 'ERROR']
    assert any('"Bed"' in message for message in issues), issues
    # The evening-only sign is hidden by the lighting state, not by the scenario.
    assert not any('"E"' in problem for problem in problems)
    unit(units["B"]).bake_scenario = bedroom_scn.scenario_id
    assert unit(units["B"]).bake_scenario_id == bedroom_scn.scenario_id
    assert scenarios.preflight(bpy.context, list(units.values()), ['DAY', 'EVENING']) == []
    assert all_flags() == original, "pre-start check must not change the outliner"
    print("scenarios: layer default, unit override and pre-start check OK")

    # Evidence: the canopy in Bedroom shadows the counter unless Kitchen's scenario runs.
    counter_png = os.path.join(output, "LO_K_Beauty.png")
    unit(units["K"]).bake_scenario = 'NONE'
    assert unit(units["K"]).bake_scenario_id == SCENARIO_NONE
    session = scenarios.ScenarioSession(bpy.context)
    assert bake_job(session, units["K"]) == "SUCCESS"
    assert not session.active and all_flags() == original, "No Scenario must leave the outliner alone"
    shadowed = mean_brightness(counter_png)

    unit(units["K"]).bake_scenario = 'LAYER'
    session = scenarios.ScenarioSession(bpy.context)
    activate_state(bpy.context, 'DAY')
    session.apply(bpy.context, unit(units["K"]))
    assert excluded("Bedroom") and not excluded("Kitchen")
    assert excluded("Kitchen Props") and excluded("Archive") and not excluded("Lamp Extras")
    assert not excluded("Day") and excluded("Evening") and not excluded("Lights")
    assert scenarios.RESTORE_COLLECTIONS in bpy.context.scene
    assert bpy.context.view_layer.objects.get("Canopy") is None
    assert bake_job(session, units["K"]) == "SUCCESS"
    lit = mean_brightness(counter_png)
    assert lit > shadowed * 1.5, (shadowed, lit)
    print(f"scenarios: counter brightness {shadowed:.3f} with Bedroom on, {lit:.3f} with Kitchen scenario OK")

    # Next unit switches to its own scenario; Kitchen Props goes with Kitchen.
    assert bake_job(session, units["B"]) == "SUCCESS"
    assert excluded("Kitchen") and excluded("Kitchen Props") and not excluded("Bedroom")
    assert bpy.context.view_layer.objects.get("Counter") is None
    assert bake_job(session, units["B"], 'EVENING') == "SUCCESS"
    assert excluded("Day") and not excluded("Evening"), "scenarios must not touch lighting"
    assert bake_job(session, units["E"], 'DAY') == "SKIPPED"
    activate_state(bpy.context, 'DAY')
    session.restore()
    assert all_flags() == original, (original, all_flags())
    assert scenarios.RESTORE_COLLECTIONS not in bpy.context.scene
    # Kitchen Props was switched off on its own: it must still come back off
    # when Kitchen is toggled in the outliner, as before the bake.
    layer_collection("Kitchen").exclude = True
    layer_collection("Kitchen").exclude = False
    assert excluded("Kitchen Props") and not excluded("Kitchen")
    print("scenarios: per-unit switching and exact restore OK")

    # The Lighting switch shows that state's baked result.
    def shown_state(source):
        source_id = source.pm_vr_pipeline.source_id
        generated = next(
            o for o in bpy.data.objects
            if o.get("pmvr_generated") and o.get("pmvr_source_id") == source_id
        )
        material = generated.material_slots[0].material
        return generated.get("pmvr_state"), material.get("pmvr_state")
    for state in ('EVENING', 'DAY'):
        assert bpy.ops.pmvr.set_lighting_state(state=state) == {'FINISHED'}
        check_state = shown_state(bed)
        assert check_state == (state, state), (state, check_state)
    assert all_flags() == original
    print("scenarios: lighting switch shows the baked state OK")

    # A parent switched off in the list takes its children with it, even when
    # a child is still ticked (Blender would otherwise render the child).
    kitchen_item = next(i for i in bedroom_scn.collections if i.collection.name == "Kitchen")
    props_item = next(i for i in bedroom_scn.collections if i.collection.name == "Kitchen Props")
    props_item.include = True
    scope = scenarios.Scope(project(), bpy.context.view_layer)
    flags = {key[-1]: (exclude, by) for key, exclude, by in scope.flags(bedroom_scn, scope.baseline())}
    assert not kitchen_item.include and flags["Kitchen Props"] == (True, True), flags
    visibility = scenarios.Visibility(bpy.context.view_layer, scope.flags(bedroom_scn, scope.baseline()))
    assert mug.as_pointer() not in visibility.included
    print("scenarios: parent propagation OK")

    # Saved in the middle of a bake: reopening restores the outliner.
    session = scenarios.ScenarioSession(bpy.context)
    session.apply(bpy.context, unit(units["B"]))
    assert excluded("Kitchen")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    session.restore()
    bpy.ops.wm.open_mainfile(filepath=blend)
    assert not excluded("Kitchen") and excluded("Kitchen Props") and not excluded("Bedroom")
    assert excluded("Archive") and scenarios.RESTORE_COLLECTIONS not in bpy.context.scene
    print("scenarios: recovery after mid-bake save OK")

    # A collection created later joins every scenario with its outliner
    # state as soon as the scene updates; one inside a lighting collection
    # does not (the Lighting switch owns those).
    root = project().source_root_collection
    extra = child(root, "Garage")
    layer_collection("Garage").exclude = True
    child(bpy.data.collections["Day"], "DayLamps")
    bpy.context.view_layer.update()
    kitchen_scn = scenario_named("Kitchen")
    scope = scenarios.Scope(project(), bpy.context.view_layer)
    for scenario in project().bake_scenarios:
        assert scope.unrecorded(scenario) == [], (scenario.display_name, scope.unrecorded(scenario))
        garage = next(i for i in scenario.collections if i.collection == extra)
        assert not garage.include, scenario.display_name
        assert not any(i.collection and i.collection.name == "DayLamps" for i in scenario.collections)
    assert not next(i for i in kitchen_scn.collections if i.collection.name == "Bedroom").include
    session = scenarios.ScenarioSession(bpy.context)
    session.apply(bpy.context, unit(units["K"]))
    assert excluded("Garage") and excluded("Bedroom")
    session.restore()
    print("scenarios: new collections join the scenarios OK")

    # Checked units change together; removal releases every reference.
    for item in project().bake_units:
        item.batch_selected = item.unit_id in (units["K"], units["B"])
    unit(units["K"]).bake_scenario = 'NONE'
    assert unit(units["B"]).bake_scenario_id == SCENARIO_NONE
    assert unit(units["E"]).bake_scenario_id == ""
    unit(units["B"]).bake_scenario = scenario_named("Bedroom").scenario_id
    project().active_bake_scenario_index = list(project().bake_scenarios).index(scenario_named("Bedroom"))
    assert bpy.ops.pmvr.remove_bake_scenario() == {'FINISHED'}
    assert unit(units["B"]).bake_scenario_id == "" and unit(units["K"]).bake_scenario_id == ""
    assert [s.display_name for s in project().bake_scenarios] == ["Kitchen"]
    before = all_flags()
    assert bpy.ops.pmvr.show_bake_scenario() == {'FINISHED'}
    assert excluded("Bedroom")
    for item in project().bake_units:
        item.batch_selected = False
    unit(units["B"]).bake_scenario_id = "0123456789abcdef"
    assert unit(units["B"]).bake_scenario == 'MISSING'
    problems = scenarios.preflight(bpy.context, [units["B"]], ['DAY'])
    assert problems and "removed bake scenario" in problems[0], problems
    assert before != all_flags()
    print("scenarios: batch override, removal and show in outliner OK")

    # A deleted collection is dropped from the scenarios that recorded it.
    kitchen_scn = scenario_named("Kitchen")
    assert any(i.collection and i.collection.name == "Garage" for i in kitchen_scn.collections)
    bpy.data.collections.remove(bpy.data.collections["Garage"])
    assert any(i.collection is None for i in kitchen_scn.collections)
    assert scenarios.prune_scenarios() == 1
    assert all(i.collection for i in kitchen_scn.collections)
    print("scenarios: deleted collections pruned OK")

    # The Lighting switch brings a state on whole. Blender alone keeps a
    # nested collection off when it was off (or created) while its parent was
    # off; production had 143 lamps per state stuck like this.
    day_lamps = layer_collection("DayLamps")
    lamp = bpy.data.objects.new("Nested Day Lamp", bpy.data.lights.new("Nested Day Lamp", 'POINT'))
    bpy.data.collections["DayLamps"].objects.link(lamp)
    assert bpy.ops.pmvr.set_lighting_state(state='DAY') == {'FINISHED'}
    day_lamps.exclude = True
    assert bpy.ops.pmvr.set_lighting_state(state='EVENING') == {'FINISHED'}
    late = child(bpy.data.collections["Day"], "DayStairs")
    evening_group = child(bpy.data.collections["Evening"], "EveningShelves")
    bpy.context.view_layer.update()
    # Plain Blender: switching the parent on leaves the remembered child off.
    layer_collection("Day").exclude = False
    assert layer_collection("DayLamps").exclude, "expected Blender to keep the child off"
    layer_collection("Day").exclude = True
    assert bpy.ops.pmvr.set_lighting_state(state='DAY') == {'FINISHED'}
    for name in ("Day", "DayLamps", "DayStairs"):
        assert not layer_collection(name).exclude, f"{name} stayed off in Day"
    assert bpy.context.view_layer.objects.get("Nested Day Lamp") is not None
    assert excluded("Evening") and excluded("EveningShelves")
    assert bpy.ops.pmvr.set_lighting_state(state='EVENING') == {'FINISHED'}
    assert excluded("Day") and excluded("DayLamps") and excluded("DayStairs")
    assert not excluded("Evening") and not excluded("EveningShelves")
    assert bpy.context.view_layer.objects.get("Nested Day Lamp") is None
    del late, evening_group
    print("scenarios: lighting switch includes nested collections OK")

    print("PM_VR_BAKE_SCENARIOS_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
