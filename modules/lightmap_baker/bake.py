"""Per-object Cycles bake, guide generation, denoise, and commit."""

import uuid

import bpy

from .assets import create_generated_bundle
from .compositor import denoise_image
from .constants import BAKE_UV_NAME
from .images import create_float_image, remove_image, stage_export
from .receiver import TemporaryReceiver
from .state import (
    BakeSettingsState,
    select_only,
)


class BakeCancelled(RuntimeError):
    pass


def _supported_bake_kwargs(kwargs):
    try:
        properties = bpy.ops.object.bake.get_rna_type().properties
        supported = {prop.identifier for prop in properties}
        return {
            key: value
            for key, value in kwargs.items()
            if key in supported
        }
    except Exception:
        return kwargs


def _run_cycles_bake(context, bake_type, margin, pass_filter=None):
    bake_state = BakeSettingsState(context.scene)
    bake_state.configure(bake_type, margin, pass_filter)
    try:
        kwargs = {
            "type": bake_type,
            "use_clear": True,
            "target": 'IMAGE_TEXTURES',
            "save_mode": 'INTERNAL',
            "margin": margin,
            "margin_type": 'EXTEND',
            "use_selected_to_active": False,
            "uv_layer": BAKE_UV_NAME,
        }
        if pass_filter is not None:
            kwargs["pass_filter"] = pass_filter
        if bake_type == 'NORMAL':
            kwargs["normal_space"] = 'OBJECT'

        try:
            result = bpy.ops.object.bake(**_supported_bake_kwargs(kwargs))
        except RuntimeError as exc:
            if "cancel" in str(exc).lower():
                raise BakeCancelled(str(exc)) from exc
            raise
        if 'FINISHED' not in result:
            raise BakeCancelled(f"{bake_type} bake was cancelled")
    finally:
        bake_state.restore()


def _bake_raw_and_guides(context, candidate, logger):
    token = uuid.uuid4().hex
    raw = create_float_image(
        f"__PM_LM_RAW_{token}",
        candidate.resolution,
    )
    albedo = None
    normal = None
    guide_warning = ""

    try:
        with TemporaryReceiver(candidate.source) as receiver:
            select_only(context, candidate.source)
            receiver.set_target_image(raw)
            logger.stage(
                f"{candidate.source_name}: baking full lightmap",
                1,
            )
            _run_cycles_bake(
                context,
                bake_type='DIFFUSE',
                margin=candidate.margin,
                pass_filter={'DIRECT', 'INDIRECT'},
            )
            logger.info(f"{candidate.source_name}: raw lightmap bake complete")

            try:
                albedo = create_float_image(
                    f"__PM_LM_ALBEDO_{token}",
                    candidate.resolution,
                )
                receiver.set_target_image(albedo)
                logger.stage(
                    f"{candidate.source_name}: baking denoise albedo guide",
                    2,
                )
                _run_cycles_bake(
                    context,
                    bake_type='DIFFUSE',
                    margin=candidate.margin,
                    pass_filter={'COLOR'},
                )

                normal = create_float_image(
                    f"__PM_LM_NORMAL_{token}",
                    candidate.resolution,
                    background=(0.5, 0.5, 1.0, 1.0),
                )
                receiver.set_target_image(normal)
                logger.stage(
                    f"{candidate.source_name}: baking denoise normal guide",
                    3,
                )
                _run_cycles_bake(
                    context,
                    bake_type='NORMAL',
                    margin=candidate.margin,
                )
            except BakeCancelled:
                raise
            except Exception as exc:
                guide_warning = f"denoise guides failed: {exc}"

        if not guide_warning and albedo and normal:
            try:
                logger.stage(
                    f"{candidate.source_name}: compositor denoise",
                    4,
                )
                denoise_image(context.scene, raw, albedo, normal)
                logger.info(f"{candidate.source_name}: compositor denoise complete")
            except Exception as exc:
                guide_warning = f"compositor denoise failed: {exc}"

        return raw, albedo, normal, guide_warning
    except Exception:
        remove_image(raw)
        remove_image(albedo)
        remove_image(normal)
        raise


def process_candidate(context, candidate, settings, logger):
    source = candidate.source
    warnings = []
    raw = None
    albedo = None
    normal = None
    bundle = None
    staged_export = None

    logger.info(
        f"{source.name}: start, resolution "
        f"{candidate.resolution} × {candidate.resolution}"
    )
    try:
        raw, albedo, normal, denoise_warning = _bake_raw_and_guides(
            context,
            candidate,
            logger,
        )
        if denoise_warning:
            warnings.append(denoise_warning)
            logger.warning(f"{source.name}: {denoise_warning}")

        logger.stage(f"{source.name}: creating _LM object and material", 5)
        operation_id = uuid.uuid4().hex
        bundle = create_generated_bundle(
            context,
            source,
            raw,
            operation_id,
        )
        if bundle.material_connected:
            logger.info(f"{source.name}: lightmap connected before Base Color")
        else:
            warning = (
                "no unambiguous Principled BSDF; "
                "lightmap nodes were left unconnected"
            )
            warnings.append(warning)
            logger.warning(f"{source.name}: {warning}")

        if settings.export_to_disk:
            logger.stage(f"{source.name}: saving scene-linear EXR", 6)
            staged_export = stage_export(raw, candidate.final_path)
        else:
            logger.stage(f"{source.name}: preparing internal lightmap", 6)

        had_previous = bool(candidate.transaction.old_assets.objects)
        logger.stage(f"{source.name}: committing baked result", 7)
        candidate.transaction.commit(bundle, staged_export)
        if settings.export_to_disk:
            logger.info(f'{source.name}: exported "{candidate.final_path}"')
        if had_previous:
            logger.info(f"{source.name}: replaced previous PM _LM result")

        return True, warnings
    except Exception:
        if bundle and not candidate.transaction.committed:
            bundle.cleanup()
        elif raw and raw.users == 0:
            remove_image(raw)
        raise
    finally:
        remove_image(albedo)
        remove_image(normal)
        if staged_export:
            staged_export.cleanup()
