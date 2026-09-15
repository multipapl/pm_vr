"""Batch runner and public Blender bake operator."""

from dataclasses import dataclass, field

import bpy

from .bake import BakeCancelled, process_candidate
from .log import BakeLogger
from .progress import BakeProgressFeedback, shutdown as shutdown_feedback
from .state import ContextState
from .validation import prepare_candidates


@dataclass
class BakeSummary:
    successful: int = 0
    skipped: int = 0
    warnings: int = 0
    failed: int = 0
    fatal_error: str = ""
    successful_transactions: list = field(default_factory=list)

    def message(self):
        return (
            f"Lightmaps: {self.successful} successful, "
            f"{self.skipped} skipped, "
            f"{self.warnings} completed with warnings, "
            f"{self.failed} failed"
        )


def run_batch(context, operator=None):
    settings = context.scene.pm_lightmap_settings
    feedback = BakeProgressFeedback(context)
    logger = BakeLogger(operator, feedback)
    summary = BakeSummary()
    context_state = ContextState(context)
    candidates = []
    successful = set()

    try:
        feedback.start(len(settings.objects))
    except Exception as exc:
        print(f"[PM Lightmap] WARNING: live feedback unavailable — {exc}")
        feedback.enabled = False
        shutdown_feedback()
    try:
        logger.info(f"Batch start: {len(settings.objects)} queued object(s)")
        try:
            context_state.enter_object_mode(context)
            context.scene.render.engine = 'CYCLES'
            candidates, summary.skipped, summary.failed = prepare_candidates(
                context,
                settings,
                logger,
            )
            logger.set_candidate_count(len(candidates))

            for index, candidate in enumerate(candidates, start=1):
                logger.begin_object(
                    candidate.source_name,
                    index,
                    len(candidates),
                )
                try:
                    succeeded, warnings = process_candidate(
                        context,
                        candidate,
                        settings,
                        logger,
                    )
                    if succeeded:
                        successful.add(id(candidate.transaction))
                        summary.successful_transactions.append(
                            candidate.transaction
                        )
                        if warnings:
                            summary.warnings += 1
                        else:
                            summary.successful += 1
                except BakeCancelled:
                    summary.fatal_error = "Bake cancelled"
                    logger.warning(summary.fatal_error)
                    break
                except Exception as exc:
                    logger.error(f"{candidate.source_name}: failed — {exc}")
                    summary.failed += 1
                finally:
                    logger.complete_object()
        except KeyboardInterrupt:
            summary.fatal_error = "Bake cancelled"
            logger.warning(summary.fatal_error)
        except Exception as exc:
            summary.fatal_error = str(exc)
            logger.error(f"Batch stopped — {exc}", report=True)
        finally:
            for candidate in candidates:
                transaction = candidate.transaction
                if id(transaction) in successful:
                    try:
                        transaction.finalize_visibility()
                    except Exception as exc:
                        logger.error(
                            f"{candidate.source_name}: "
                            f"could not finalize visibility — {exc}"
                        )
                else:
                    try:
                        transaction.restore_previous_visibility()
                    except Exception as exc:
                        logger.error(
                            f"{candidate.source_name}: "
                            f"could not restore visibility — {exc}"
                        )
            try:
                context_state.restore(context)
            except Exception as exc:
                summary.fatal_error = (
                    f"Could not fully restore Blender context — {exc}"
                )
                logger.error(summary.fatal_error, report=True)

        logger.info(f"Batch finish: {summary.message()}")
    finally:
        try:
            feedback.finish(
                summary.message(),
                has_errors=bool(
                    summary.failed
                    or summary.skipped
                    or summary.warnings
                    or summary.fatal_error
                ),
            )
        except Exception as exc:
            print(
                f"[PM Lightmap] WARNING: "
                f"could not close live feedback — {exc}"
            )
            shutdown_feedback()
    return summary


class PM_OT_LightmapBake(bpy.types.Operator):
    bl_idname = "pm.lightmap_bake"
    bl_label = "Bake Lightmaps"
    bl_description = "Bake classic scene-linear diffuse lightmaps with Cycles"

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "pm_lightmap_settings", None)
        return bool(settings and settings.objects)

    def execute(self, context):
        if bpy.app.background:
            self.report(
                {'ERROR'},
                "Background/headless lightmap baking is not supported in v1",
            )
            return {'CANCELLED'}

        summary = run_batch(context, self)
        if summary.fatal_error:
            self.report({'ERROR'}, summary.message())
            return {'CANCELLED'}

        report_type = (
            {'WARNING'}
            if summary.failed or summary.skipped or summary.warnings
            else {'INFO'}
        )
        self.report(report_type, summary.message())
        return {'FINISHED'}


CLASSES = (PM_OT_LightmapBake,)
