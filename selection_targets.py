def is_collection_instance(obj):
    return bool(
        obj
        and obj.type == 'EMPTY'
        and getattr(obj, "instance_type", None) == 'COLLECTION'
        and getattr(obj, "instance_collection", None) is not None
    )


def iter_collection_mesh_objects(collection, seen_collections=None, seen_objects=None):
    if not collection:
        return []

    if seen_collections is None:
        seen_collections = set()
    if seen_objects is None:
        seen_objects = set()

    collection_key = collection.as_pointer()
    if collection_key in seen_collections:
        return []
    seen_collections.add(collection_key)

    resolved = []

    for obj in collection.objects:
        if obj.type == 'MESH':
            object_key = obj.as_pointer()
            if object_key not in seen_objects:
                seen_objects.add(object_key)
                resolved.append(obj)
        elif is_collection_instance(obj):
            resolved.extend(
                iter_collection_mesh_objects(
                    obj.instance_collection,
                    seen_collections=seen_collections,
                    seen_objects=seen_objects,
                )
            )

    for child in collection.children:
        resolved.extend(
            iter_collection_mesh_objects(
                child,
                seen_collections=seen_collections,
                seen_objects=seen_objects,
            )
        )

    return resolved


def resolve_operable_mesh_targets(objects):
    resolved = []
    seen_objects = set()

    for obj in objects or []:
        if not obj:
            continue

        if obj.type == 'MESH':
            object_key = obj.as_pointer()
            if object_key not in seen_objects:
                seen_objects.add(object_key)
                resolved.append(obj)
            continue

        if is_collection_instance(obj):
            resolved.extend(iter_collection_mesh_objects(obj.instance_collection, seen_objects=seen_objects))

    return resolved


def get_selected_target_objects(context):
    if not context:
        return []
    return resolve_operable_mesh_targets(context.selected_objects)


def get_active_target_objects(context):
    if not context or not context.active_object:
        return []
    return resolve_operable_mesh_targets([context.active_object])


def get_scene_target_objects(scene):
    if not scene:
        return []
    return resolve_operable_mesh_targets(scene.objects)
