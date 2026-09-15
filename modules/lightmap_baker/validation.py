"""Independent bake-list validation and candidate preparation."""

from dataclasses import dataclass

import bpy

from .assets import AssetTransaction
from .constants import BAKE_UV_NAME, RESOLUTION_VALUES, TAG_GENERATED
from .images import (
    build_final_path,
    validate_export_path,
)
from .ownership import AssetCollisionError, validate_name_collisions


@dataclass
class BakeCandidate:
    source: object
    source_name: str
    resolution: int
    margin: int
    final_path: str
    transaction: AssetTransaction


def _object_exists(obj):
    return bool(obj and bpy.data.objects.get(obj.name) is obj)


def validate_item(context, settings, item, logger):
    source = item.source_object
    name = source.name if source else (item.source_name or "<Missing Object>")

    if not _object_exists(source):
        raise ValueError("object no longer exists")
    if source.type != 'MESH' or not source.data:
        raise ValueError("object is not a mesh")
    if source.get(TAG_GENERATED, False):
        raise ValueError("generated _LM objects cannot be bake sources")
    if context.view_layer.objects.get(source.name) is not source:
        raise ValueError("object is not available in the active View Layer")
    if source.data.uv_layers.get(BAKE_UV_NAME) is None:
        raise ValueError(f'missing UV layer "{BAKE_UV_NAME}"')

    slot_count = len(source.material_slots)
    if slot_count == 0:
        raise ValueError("object has no material")
    if slot_count > 1:
        raise ValueError("object has more than one material slot")
    if source.material_slots[0].material is None:
        raise ValueError("object has an empty material slot")

    resolution = int(
        item.resolution
        if item.use_resolution_override
        else settings.resolution
    )
    if resolution not in RESOLUTION_VALUES:
        raise ValueError(f"unsupported resolution {resolution}")

    validate_name_collisions(source)
    transaction = AssetTransaction(context, source, logger)
    final_path = ""
    if settings.export_to_disk:
        final_path = build_final_path(settings, source)
        validate_export_path(final_path, transaction.old_assets.images)

    return BakeCandidate(
        source=source,
        source_name=name,
        resolution=resolution,
        margin=settings.margin,
        final_path=final_path,
        transaction=transaction,
    )


def prepare_candidates(context, settings, logger):
    candidates = []
    seen = set()
    skipped = 0
    failed = 0

    for item in list(settings.objects):
        source = item.source_object
        name = source.name if source else (item.source_name or "<Missing Object>")
        pointer = source.as_pointer() if source else None
        if pointer and pointer in seen:
            logger.warning(f"{name}: skipped duplicate bake-list entry")
            skipped += 1
            continue

        candidate = None
        try:
            candidate = validate_item(context, settings, item, logger)
            candidate.transaction.prepare_scene()
            candidates.append(candidate)
            seen.add(pointer)
        except (ValueError, AssetCollisionError, RuntimeError) as exc:
            if candidate:
                candidate.transaction.restore_previous_visibility()
            logger.warning(f"{name}: skipped — {exc}")
            skipped += 1
        except Exception as exc:
            if candidate:
                candidate.transaction.restore_previous_visibility()
            logger.error(f"{name}: validation failed — {exc}")
            failed += 1

    return candidates, skipped, failed
