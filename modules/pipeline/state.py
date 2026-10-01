"""Project lighting activation and LayerCollection path validation."""

from . import looks


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
    try:
        looks.validate_names(project)
    except ValueError as exc:
        raise PipelineStateError(str(exc)) from exc
    root = project.source_root_collection
    unique_layer_collection(context.view_layer, root, "Source Root")
    configured = []
    for look in project.lighting_looks:
        collection = look.lighting_collection
        if collection is None:
            raise PipelineStateError(f'Choose the {look.display_name} lighting collection')
        if not collection_contains(root, collection):
            raise PipelineStateError('Lighting collections must be inside Source Root')
        for other in project.lighting_looks:
            if other.look_id != look.look_id and other.lighting_collection:
                if collection_contains(collection, other.lighting_collection):
                    raise PipelineStateError('Lighting collections must differ and cannot contain one another')
        layer, _ = unique_layer_collection(context.view_layer, collection, look.display_name + ' Lighting')
        configured.append((look.look_id, layer))
    return configured


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
    configured = validate_state_configuration(context)
    look = looks.find(project, state)
    if look is None:
        raise PipelineStateError('Lighting look was removed')
    world = look.world
    if world is None:
        raise PipelineStateError(f"Choose the {look.display_name} World in Project Settings")
    for look_id, layer in configured:
        if look_id != state:
            _switch_subtree(layer, True)
    _switch_subtree(next(layer for look_id, layer in configured if look_id == state), False)
    context.scene.world = world
    if state in looks.LEGACY:
        project.active_lighting_state = state
    project.active_look_id = state
    return state
