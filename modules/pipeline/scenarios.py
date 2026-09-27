"""Bake scenarios: which collections inside Source Root are enabled while a unit bakes.

A scenario records the Exclude checkbox of every collection under Source Root.
The bake queue switches them before each unit and restores the outliner when it
ends. Day/Evening lighting collections, their contents and the collections that
hold them stay under the lighting state's control.

Blender does not inherit Exclude when it builds the View Layer: a collection
that is not excluded still renders its objects inside an excluded parent. The
outliner hides this by excluding children together with their parent; scenarios
apply the same rule, so switching a parent off always switches off its content.
"""

import json

import bpy

from .bake_scene import PipelineBakeError
from .constants import SCENARIO_NONE
from .identity import find_layer, find_unit, new_id, unit_members
from .state import (
    PipelineStateError,
    activate_state,
    collection_paths,
    validate_state_configuration,
)
from . import log


# The outliner state a running bake started from, mirrored into the .blend so
# a crash or a save during the bake can be undone on load.
RESTORE_COLLECTIONS = "pmvr_bake_restore_collections"


def find_scenario(project, scenario_id):
    if not scenario_id:
        return None
    return next((item for item in project.bake_scenarios if item.scenario_id == scenario_id), None)


def unit_scenario(project, unit):
    """Scenario a unit bakes with: its override, else its layer's; None keeps the outliner."""
    scenario_id = unit.bake_scenario_id
    origin = "its override"
    if scenario_id == SCENARIO_NONE:
        return None
    if not scenario_id:
        layer = find_layer(project, unit.render_layer_id)
        scenario_id = layer.bake_scenario_id if layer else ""
        if not scenario_id:
            return None
        origin = f'layer "{layer.display_name}"'
    scenario = find_scenario(project, scenario_id)
    if not scenario:
        raise PipelineBakeError(
            f'unit "{unit.display_name}": {origin} refers to a removed bake scenario'
        )
    return scenario


def _walk(layer_collection, path=()):
    """(path, layer collection) below layer_collection, parents first."""
    for child in layer_collection.children:
        key = (*path, child.collection.name_full)
        yield key, child
        yield from _walk(child, key)


def _layer_collections(view_layer):
    return dict(_walk(view_layer.layer_collection))


class Scope:
    """Layer collections a scenario controls in one View Layer."""

    def __init__(self, project, view_layer):
        root = project.source_root_collection
        if not root:
            raise PipelineBakeError("Choose Source Root Collection in Project Settings")
        paths = collection_paths(view_layer, root)
        if len(paths) != 1:
            raise PipelineBakeError("Source Root must be linked exactly once in the active View Layer")
        self.root = paths[0][-1]
        self.root_key = tuple(item.collection.name_full for item in paths[0][1:])
        self.lighting = {
            collection.name_full
            for collection in (project.day_lighting_collection, project.evening_lighting_collection)
            if collection
        }
        lighting_keys = [
            key for key, layer_collection in _walk(self.root, self.root_key)
            if layer_collection.collection.name_full in self.lighting
        ]
        # Excluding a collection that holds the lights would switch them off.
        self.locked = {
            key[:end]
            for key in lighting_keys
            for end in range(len(self.root_key) + 1, len(key))
        }

    def entries(self):
        """(path, layer collection, depth) of every controllable collection, parents first."""
        result = []

        def visit(layer_collection, key, depth):
            for child in layer_collection.children:
                child_key = (*key, child.collection.name_full)
                if child.collection.name_full in self.lighting:
                    continue
                controllable = child_key not in self.locked
                if controllable:
                    result.append((child_key, child, depth))
                visit(child, child_key, depth + int(controllable))

        visit(self.root, self.root_key, 0)
        return result

    def baseline(self):
        return {key: layer_collection.exclude for key, layer_collection, _depth in self.entries()}

    def flags(self, scenario, baseline):
        """[(path, exclude, excluded_by_scenario)] for every controllable collection.

        Without a scenario every collection returns to baseline. A scenario sets
        the collections it records; the rest keep baseline, and everything inside
        an excluded collection is excluded with it."""
        wanted = {}
        if scenario:
            for item in scenario.collections:
                if item.collection:
                    wanted[item.collection.name_full] = not item.include
        result = []

        def visit(layer_collection, key, parent_excluded, parent_by_scenario):
            for child in layer_collection.children:
                name = child.collection.name_full
                child_key = (*key, name)
                if name in self.lighting:
                    continue
                if child_key in self.locked:
                    visit(child, child_key, parent_excluded or child.exclude, parent_by_scenario)
                    continue
                base = baseline.get(child_key, child.exclude)
                if scenario is None:
                    exclude, by_scenario = base, False
                else:
                    own = wanted.get(name, base)
                    exclude = own or parent_excluded
                    by_scenario = exclude and (wanted.get(name, False) or parent_by_scenario)
                result.append((child_key, exclude, by_scenario))
                visit(child, child_key, exclude, by_scenario)

        visit(self.root, self.root_key, self.root.exclude, False)
        return result

    def unrecorded(self, scenario):
        recorded = {item.collection.name_full for item in scenario.collections if item.collection}
        names = []
        for _key, layer_collection, _depth in self.entries():
            name = layer_collection.collection.name_full
            if name not in recorded and name not in names:
                names.append(name)
        return names


def enforce(view_layer, flags):
    """Set Exclude per path, parents first; a parent's change reaches its children
    before they are compared, like clicking the outliner checkbox."""
    layer_collections = _layer_collections(view_layer)
    changed = 0
    for key, exclude in flags:
        layer_collection = layer_collections.get(tuple(key))
        if layer_collection is not None and layer_collection.exclude != bool(exclude):
            layer_collection.exclude = bool(exclude)
            changed += 1
    return changed


def _included_objects(view_layer, overrides):
    # Mirrors Blender's View Layer sync: every collection that is not itself
    # excluded contributes its objects, whatever its parents are.
    included = {obj.as_pointer() for obj in view_layer.layer_collection.collection.objects}
    for key, layer_collection in _walk(view_layer.layer_collection):
        if not overrides.get(key, layer_collection.exclude):
            included.update(obj.as_pointer() for obj in layer_collection.collection.objects)
    return included


class Visibility:
    """Which objects a scenario switches off, for one lighting state."""

    def __init__(self, view_layer, flags):
        layer_collections = _layer_collections(view_layer)
        self.included = _included_objects(
            view_layer,
            {key: exclude for key, exclude, _by in flags},
        )
        self.blocked = {}
        for key, _exclude, by_scenario in flags:
            if by_scenario:
                collection = layer_collections[key].collection
                for obj in collection.objects:
                    self.blocked.setdefault(obj.as_pointer(), collection.name)

    def hidden(self, objects):
        """Objects the scenario switches off. Objects hidden by other means (their
        own render toggle, a collection it does not record, the lighting state)
        are not its doing and keep the usual skip behavior."""
        return [
            obj for obj in objects
            if not obj.hide_render
            and obj.as_pointer() in self.blocked
            and obj.as_pointer() not in self.included
        ]

    def describe(self, objects):
        hidden = self.hidden(objects)
        shown = ", ".join(f'"{obj.name}" ({self.blocked[obj.as_pointer()]})' for obj in hidden[:4])
        more = f" and {len(hidden) - 4} more" if len(hidden) > 4 else ""
        return f"{shown}{more}"


def hidden_member_message(unit, scenario, visibility):
    members = unit_members(unit.unit_id)
    if not visibility.hidden(members):
        return ""
    return (
        f'scenario "{scenario.display_name}" switches off members of unit '
        f'"{unit.display_name}": {visibility.describe(members)}'
    )


def record(scenario, scope, keep_recorded=False):
    """Store the controllable collections in outliner order. With keep_recorded,
    collections already in the scenario keep their state and only new ones are
    taken from the outliner."""
    recorded = {
        item.collection.name_full: item.include
        for item in scenario.collections
        if item.collection
    } if keep_recorded else {}
    seen = set()
    rows = []
    for _key, layer_collection, depth in scope.entries():
        collection = layer_collection.collection
        if collection.name_full in seen:
            continue
        seen.add(collection.name_full)
        include = recorded.get(collection.name_full, not layer_collection.exclude)
        rows.append((collection, include, depth))
    scenario.collections.clear()
    for collection, include, depth in rows:
        item = scenario.collections.add()
        item.collection = collection
        item.name = collection.name
        item.include = include
        item.depth = depth
    scenario.active_collection_index = min(
        scenario.active_collection_index, max(0, len(scenario.collections) - 1)
    )
    return len(rows)


def switched_off_count(scenario):
    return sum(1 for item in scenario.collections if item.collection and not item.include)


class ScenarioSession:
    """Collection switching for one bake queue.

    Nothing is touched until the first unit with a scenario. The outliner state
    found then is the baseline: units without a scenario bake with it, and it is
    restored when the queue ends, is cancelled, or is interrupted."""

    def __init__(self, context):
        self.scene = context.scene
        self.view_layer_name = context.view_layer.name
        self.baseline = None
        self.applied = None
        self.reported_unrecorded = set()

    @property
    def active(self):
        return self.baseline is not None

    def _begin(self, scope):
        self.baseline = scope.baseline()
        self.scene[RESTORE_COLLECTIONS] = json.dumps({
            "view_layer": self.view_layer_name,
            "flags": [[list(key), exclude] for key, exclude in self.baseline.items()],
        })
        log.info(
            "Bake",
            f"Scenarios: outliner state of {len(self.baseline)} collection(s) saved; "
            "it is restored when the queue ends",
        )

    def apply(self, context, unit):
        """Switch collections for unit before it is prepared; returns its scenario.

        Raises PipelineBakeError, before changing anything, when the scenario
        would switch off the unit's own members."""
        project = context.scene.pm_vr_project
        scenario = unit_scenario(project, unit)
        if scenario is None and not self.active:
            return None
        view_layer = context.view_layer
        if view_layer.name != self.view_layer_name:
            raise PipelineBakeError("the active View Layer changed during the bake")
        scope = Scope(project, view_layer)
        if not self.active:
            self._begin(scope)
        flags = scope.flags(scenario, self.baseline)
        if scenario:
            message = hidden_member_message(unit, scenario, Visibility(view_layer, flags))
            if message:
                raise PipelineBakeError(message)
            if scenario.scenario_id not in self.reported_unrecorded:
                self.reported_unrecorded.add(scenario.scenario_id)
                missing = scope.unrecorded(scenario)
                if missing:
                    log.warning(
                        "Bake",
                        f'Scenario "{scenario.display_name}" does not record '
                        f'{len(missing)} collection(s); they keep their outliner state: '
                        + ", ".join(missing[:8]) + ("…" if len(missing) > 8 else ""),
                    )
        changed = enforce(view_layer, [(key, exclude) for key, exclude, _by in flags])
        applied = scenario.scenario_id if scenario else ""
        if applied != self.applied or changed:
            if scenario:
                off = sum(1 for _key, exclude, _by in flags if exclude)
                log.info(
                    "Bake",
                    f'Scenario "{scenario.display_name}" for unit "{unit.display_name}": '
                    f"{off} of {len(flags)} collection(s) off, {changed} switched",
                )
            else:
                log.info(
                    "Bake",
                    f'No scenario for unit "{unit.display_name}": outliner state, '
                    f"{changed} collection(s) switched back",
                )
        self.applied = applied
        return scenario

    def restore(self):
        if not self.active:
            return 0
        changed = 0
        try:
            view_layer = self.scene.view_layers.get(self.view_layer_name)
            if view_layer:
                changed = enforce(view_layer, self.baseline.items())
        finally:
            self.baseline = None
            self.applied = None
            try:
                if RESTORE_COLLECTIONS in self.scene:
                    del self.scene[RESTORE_COLLECTIONS]
            except ReferenceError:
                pass
        log.info("Bake", f"Scenarios: outliner restored ({changed} collection(s) switched back)")
        return changed


def preflight(context, unit_ids, states):
    """Problems that would stop queued units with a scenario; empty when the queue can start.

    Lighting states are switched to check visibility exactly as the bake will
    see it, and switched back afterwards."""
    project = context.scene.pm_vr_project
    problems = []
    planned = []
    for unit_id in unit_ids:
        unit = find_unit(project, unit_id)
        if not unit:
            continue
        try:
            scenario = unit_scenario(project, unit)
        except PipelineBakeError as exc:
            problems.append(str(exc))
            continue
        if scenario:
            planned.append((unit, scenario))
    if not planned:
        return problems
    view_layer = context.view_layer
    try:
        scope = Scope(project, view_layer)
    except PipelineBakeError as exc:
        return problems + [str(exc)]
    baseline = scope.baseline()
    original = project.active_lighting_state
    seen = set()
    try:
        for state in states:
            activate_state(context, state)
            visibility = {}
            for unit, scenario in planned:
                if scenario.scenario_id not in visibility:
                    visibility[scenario.scenario_id] = Visibility(
                        view_layer, scope.flags(scenario, baseline)
                    )
                message = hidden_member_message(unit, scenario, visibility[scenario.scenario_id])
                if message and message not in seen:
                    seen.add(message)
                    problems.append(message)
    except PipelineStateError as exc:
        problems.append(str(exc))
    finally:
        try:
            activate_state(context, original)
        except PipelineStateError:
            pass
    return problems


def recover_interrupted_scenarios():
    """Put back collection states a crashed or saved-during-bake queue left switched."""
    restored = 0
    for scene in bpy.data.scenes:
        raw = scene.get(RESTORE_COLLECTIONS)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
            view_layer = scene.view_layers.get(data["view_layer"])
            if view_layer:
                restored += enforce(
                    view_layer,
                    [(tuple(path), exclude) for path, exclude in data["flags"]],
                )
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("Bake", f"Could not restore collections after an interrupted bake: {exc}")
        del scene[RESTORE_COLLECTIONS]
        restored += 1
    return restored


def _scope_or_report(operator, context):
    project = context.scene.pm_vr_project
    try:
        validate_state_configuration(context)
        return Scope(project, context.view_layer)
    except (PipelineBakeError, PipelineStateError) as exc:
        operator.report({'ERROR'}, str(exc))
        return None


def active_scenario(project):
    if not project.bake_scenarios:
        return None
    return project.bake_scenarios[
        min(project.active_bake_scenario_index, len(project.bake_scenarios) - 1)
    ]


def _editable(context):
    project = getattr(context.scene, "pm_vr_project", None) if context.scene else None
    return bool(project and project.initialized and not project.operation_running)


class PMVR_OT_AddBakeScenario(bpy.types.Operator):
    bl_idname = "pmvr.add_bake_scenario"
    bl_label = "New Scenario from Outliner"
    bl_description = (
        "Create a bake scenario from the collections currently enabled and "
        "disabled inside Source Root"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _editable(context)

    def execute(self, context):
        project = context.scene.pm_vr_project
        scope = _scope_or_report(self, context)
        if not scope:
            return {'CANCELLED'}
        scenario = project.bake_scenarios.add()
        scenario.scenario_id = new_id()
        scenario.display_name = f"Scenario {len(project.bake_scenarios)}"
        record(scenario, scope)
        project.active_bake_scenario_index = len(project.bake_scenarios) - 1
        message = (
            f'Created bake scenario "{scenario.display_name}": '
            f"{switched_off_count(scenario)} of {len(scenario.collections)} collection(s) off"
        )
        self.report({'INFO'}, message)
        log.info("Setup", message)
        return {'FINISHED'}


class PMVR_OT_RemoveBakeScenario(bpy.types.Operator):
    bl_idname = "pmvr.remove_bake_scenario"
    bl_label = "Remove Scenario"
    bl_description = (
        "Remove the active bake scenario. Layers using it get No Scenario and "
        "units overriding with it follow their layer again"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _editable(context) and bool(context.scene.pm_vr_project.bake_scenarios)

    def execute(self, context):
        project = context.scene.pm_vr_project
        scenario = active_scenario(project)
        scenario_id = scenario.scenario_id
        name = scenario.display_name
        layers = 0
        for layer in project.render_layers:
            if layer.bake_scenario_id == scenario_id:
                layer.bake_scenario_id = ""
                layers += 1
        units = 0
        for unit in project.bake_units:
            if unit.bake_scenario_id == scenario_id:
                unit.bake_scenario_id = ""
                units += 1
        index = next(
            i for i, item in enumerate(project.bake_scenarios)
            if item.scenario_id == scenario_id
        )
        project.bake_scenarios.remove(index)
        project.active_bake_scenario_index = min(index, max(0, len(project.bake_scenarios) - 1))
        message = (
            f'Removed bake scenario "{name}"; {layers} layer(s) now have No Scenario, '
            f"{units} unit(s) now follow their layer"
        )
        self.report({'INFO'}, message)
        log.info("Setup", message)
        return {'FINISHED'}


class PMVR_OT_CaptureBakeScenario(bpy.types.Operator):
    bl_idname = "pmvr.capture_bake_scenario"
    bl_label = "Capture Outliner"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(
            ('ALL', "All", "Record every collection from the outliner"),
            ('MISSING', "New Only", "Record only collections the scenario does not know yet"),
        ),
        default='ALL',
        options={'SKIP_SAVE'},
    )

    @classmethod
    def description(cls, _context, properties):
        if properties.mode == 'MISSING':
            return (
                "Collections created after this scenario keep their outliner "
                "state while baking. Add records them with their current "
                "outliner state; recorded collections are unchanged"
            )
        return (
            "Replace the active scenario with the collections currently enabled "
            "and disabled in the outliner"
        )

    @classmethod
    def poll(cls, context):
        return _editable(context) and bool(context.scene.pm_vr_project.bake_scenarios)

    def execute(self, context):
        project = context.scene.pm_vr_project
        scenario = active_scenario(project)
        scope = _scope_or_report(self, context)
        if not scope:
            return {'CANCELLED'}
        before = len([item for item in scenario.collections if item.collection])
        record(scenario, scope, keep_recorded=self.mode == 'MISSING')
        if self.mode == 'MISSING':
            message = (
                f'Scenario "{scenario.display_name}": added '
                f"{max(0, len(scenario.collections) - before)} new collection(s)"
            )
        else:
            message = (
                f'Scenario "{scenario.display_name}" captured: '
                f"{switched_off_count(scenario)} of {len(scenario.collections)} collection(s) off"
            )
        self.report({'INFO'}, message)
        log.info("Setup", message)
        return {'FINISHED'}


class PMVR_OT_ShowBakeScenario(bpy.types.Operator):
    bl_idname = "pmvr.show_bake_scenario"
    bl_label = "Show in Outliner"
    bl_description = (
        "Switch the outliner collections to the active scenario, to check or "
        "edit it there. Ctrl+Z switches them back. Export also skips disabled "
        "collections, so switch back before exporting"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _editable(context) and bool(context.scene.pm_vr_project.bake_scenarios)

    def execute(self, context):
        project = context.scene.pm_vr_project
        scenario = active_scenario(project)
        scope = _scope_or_report(self, context)
        if not scope:
            return {'CANCELLED'}
        flags = scope.flags(scenario, {})
        changed = enforce(context.view_layer, [(key, exclude) for key, exclude, _by in flags])
        self.report(
            {'INFO'},
            f'Outliner shows scenario "{scenario.display_name}" '
            f"({changed} collection(s) switched; Ctrl+Z switches back)",
        )
        return {'FINISHED'}


CLASSES = (
    PMVR_OT_AddBakeScenario,
    PMVR_OT_RemoveBakeScenario,
    PMVR_OT_CaptureBakeScenario,
    PMVR_OT_ShowBakeScenario,
)
