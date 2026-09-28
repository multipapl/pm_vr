"""Day/Evening activation and LayerCollection path validation."""


class PipelineStateError(RuntimeError):
    pass


def walk_layer_collections(root, path=()):
    current_path = (*path, root)
    yield current_path
    for child in root.children:
        yield from walk_layer_collections(child, current_path)


def collection_paths(view_layer, collection):
    if collection is None:
        return []
    return [
        path for path in walk_layer_collections(view_layer.layer_collection)
        if path[-1].collection == collection
    ]


def unique_layer_collection(view_layer, collection, label):
    paths = collection_paths(view_layer, collection)
    if not paths:
        raise PipelineStateError(f'{label} collection is not in active View Layer')
    if len(paths) > 1:
        raise PipelineStateError(f'{label} collection is linked through multiple View Layer paths')
    return paths[0][-1], paths[0]


def collection_contains(parent, child):
    if parent == child:
        return True
    return any(collection_contains(item, child) for item in parent.children)


def validate_state_configuration(context):
    project = context.scene.pm_vr_project
    if not project.source_root_collection:
        raise PipelineStateError("Choose Source Root Collection in Project Settings")
    if not project.day_lighting_collection or not project.evening_lighting_collection:
        raise PipelineStateError("Choose both Day and Evening lighting collections")
    if project.day_lighting_collection == project.evening_lighting_collection:
        raise PipelineStateError("Day and Evening lighting collections must be different")
    root = project.source_root_collection
    day = project.day_lighting_collection
    evening = project.evening_lighting_collection
    if not collection_contains(root, day) or not collection_contains(root, evening):
        raise PipelineStateError("Both lighting collections must be inside Source Root")
    if collection_contains(day, evening) or collection_contains(evening, day):
        raise PipelineStateError("Day and Evening lighting collections cannot contain one another")
    unique_layer_collection(context.view_layer, root, "Source Root")
    day_layer, _ = unique_layer_collection(context.view_layer, day, "Day Lighting")
    evening_layer, _ = unique_layer_collection(context.view_layer, evening, "Evening Lighting")
    return day_layer, evening_layer


def _switch_subtree(layer_collection, exclude):
    """Set Exclude on a lighting collection and everything inside it, parents
    first. Blender re-enables a child only if it was on when its parent was
    switched off, so nested light groups created or switched off while their
    state was inactive stayed off; a lighting state always comes on whole."""
    if layer_collection.exclude != exclude:
        layer_collection.exclude = exclude
    for child in layer_collection.children:
        _switch_subtree(child, exclude)


def activate_state(context, state):
    project = context.scene.pm_vr_project
    day_layer, evening_layer = validate_state_configuration(context)
    world = project.day_world if state == 'DAY' else project.evening_world
    if world is None:
        raise PipelineStateError(f"Choose the {state.title()} World in Project Settings")
    active, other = (day_layer, evening_layer) if state == 'DAY' else (evening_layer, day_layer)
    _switch_subtree(other, True)
    _switch_subtree(active, False)
    context.scene.world = world
    project.active_lighting_state = state
    return state
