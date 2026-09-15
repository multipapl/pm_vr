"""Generated-asset ownership metadata and collision checks."""

from dataclasses import dataclass, field

import bpy

from .constants import (
    ASSET_IMAGE,
    ASSET_MATERIAL,
    ASSET_MESH,
    ASSET_OBJECT,
    BAKER_VERSION,
    OUTPUT_COLLECTION_NAME,
    RESULT_SUFFIX,
    TAG_ASSET_TYPE,
    TAG_GENERATED,
    TAG_OPERATION,
    TAG_SOURCE_NAME,
    TAG_SOURCE_REF,
    TAG_VERSION,
)


class AssetCollisionError(RuntimeError):
    pass


def result_name(source):
    return f"{source.name}{RESULT_SUFFIX}"


def _source_matches(id_block, source):
    try:
        if id_block.get(TAG_SOURCE_REF) is source:
            return True
    except (ReferenceError, TypeError):
        pass
    return id_block.get(TAG_SOURCE_NAME, "") == source.name


def is_owned(id_block, source=None, asset_type=None):
    if not id_block or not id_block.get(TAG_GENERATED, False):
        return False
    if source and not _source_matches(id_block, source):
        return False
    if asset_type and id_block.get(TAG_ASSET_TYPE) != asset_type:
        return False
    return True


def tag_asset(id_block, source, asset_type, operation_id):
    id_block[TAG_GENERATED] = True
    id_block[TAG_SOURCE_NAME] = source.name
    try:
        id_block[TAG_SOURCE_REF] = source
    except (TypeError, ValueError):
        pass
    id_block[TAG_ASSET_TYPE] = asset_type
    id_block[TAG_VERSION] = BAKER_VERSION
    id_block[TAG_OPERATION] = operation_id


@dataclass
class OwnedAssets:
    objects: list = field(default_factory=list)
    meshes: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    images: list = field(default_factory=list)

    def all_id_blocks(self):
        return [
            *self.objects,
            *self.meshes,
            *self.materials,
            *self.images,
        ]


def collect_owned_assets(source):
    return OwnedAssets(
        objects=[
            item
            for item in bpy.data.objects
            if is_owned(item, source, ASSET_OBJECT)
        ],
        meshes=[
            item
            for item in bpy.data.meshes
            if is_owned(item, source, ASSET_MESH)
        ],
        materials=[
            item
            for item in bpy.data.materials
            if is_owned(item, source, ASSET_MATERIAL)
        ],
        images=[
            item
            for item in bpy.data.images
            if is_owned(item, source, ASSET_IMAGE)
        ],
    )


def validate_name_collisions(source):
    name = result_name(source)
    checks = (
        (bpy.data.objects.get(name), ASSET_OBJECT, "object"),
        (bpy.data.meshes.get(name), ASSET_MESH, "mesh"),
        (bpy.data.materials.get(name), ASSET_MATERIAL, "material"),
        (bpy.data.images.get(name), ASSET_IMAGE, "image"),
    )
    for id_block, asset_type, label in checks:
        if id_block and not is_owned(id_block, source, asset_type):
            raise AssetCollisionError(
                f'untagged {label} datablock "{name}" already exists'
            )


def get_output_collection(scene):
    collection = bpy.data.collections.get(OUTPUT_COLLECTION_NAME)
    if not collection:
        collection = bpy.data.collections.new(OUTPUT_COLLECTION_NAME)

    linked_to_scene = any(
        child is collection
        for child in scene.collection.children
    )
    if not linked_to_scene:
        scene.collection.children.link(collection)
    return collection
