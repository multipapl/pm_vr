"""Bake queue: Beauty unit runtime, Lightmap units and the modal queue operator."""

from datetime import datetime
import time
import uuid

import bpy

from ..lightmap_baker.compositor import denoise_external_beauty, denoise_image
from ..lightmap_baker.images import create_float_image, remove_image
from ..lightmap_baker.progress import BakeProgressFeedback
from ..lightmap_baker.state import ContextState
from .bake_files import (
    beauty_image_name,
    commit_staged_file,
    stage_beauty_image,
    stage_lightmap_image,
)
from .bake_scene import (
    BakeConfigurationSnapshot,
    EvaluationSnapshot,
    PipelineBakeCancelled,
    PipelineBakeError,
    bake_receivers,
    clear_collection,
    copy_receiver,
    ensure_scene_collection,
    pipeline_collection,
    remove_work_collection,
    restore_viewport_shading,
    select_only,
    set_target_image,
    signature_for_receivers,
    supported_bake_kwargs,
    switch_viewports_to_wireframe,
)
from .constants import WORK_COLLECTION
from .generated import (
    check_commit,
    commit_generated_geometry,
    commit_materials,
    discard_unused_materials,
    prepare_lightmap_materials,
    prepare_materials,
    tag_image,
)
from .identity import find_layer, find_unit, unit_members
from . import log, viewport_overlay
from .state import activate_state
from .validation import object_render_visible, validate_unit


def _record(project, unit, state, signature, image, status, message="", mode='BEAUTY'):
    record = project.build_records.add()
    record.unit_id = unit.unit_id
    record.lighting_state = state
    record.bake_mode = mode
    record.signature = signature
    record.image_name = image.name if image else ""
    record.timestamp = datetime.now().isoformat(timespec="seconds")
    record.status = status
    record.message = message


def _show_bake_stage(operator, message, step, step_count):
    feedback = getattr(operator, "_pmvr_feedback", None) if operator else None
    if feedback:
        feedback.set_stage(message, step, step_count)


class BeautyBakeRuntime:
    """One prepared Beauty unit, advanced by the modal queue operator."""

    def __init__(self, context, unit, operator=None):
        self.context = context
        self.project = context.scene.pm_vr_project
        self.unit_id = unit.unit_id
        self.operator = operator
        self.state = self.project.active_lighting_state
        self._resolve()
        self.members = []
        self.receivers = []
        self.image = None
        self.signature = ""
        self.snapshot = None
        self.context_state = None
        self.config = None
        self.work_collection = None
        self.created_materials = []
        self.warnings = []
        self.finished = False
        self.started_at = time.monotonic()

    def _resolve(self):
        # Collection items move in memory when the collection grows or shrinks;
        # look the unit and layer up by stable ID instead of holding them.
        self.unit = find_unit(self.project, self.unit_id)
        if not self.unit:
            raise PipelineBakeError("bake unit was removed during the bake")
        self.layer = find_layer(self.project, self.unit.render_layer_id)
        if not self.layer:
            raise PipelineBakeError(f'unit "{self.unit.display_name}" has no render layer')

    def prepare(self):
        issues = validate_unit(self.context, self.unit, require_visible=True)
        errors = [issue.message for issue in issues if issue.severity == 'ERROR']
        if errors:
            raise PipelineBakeError("; ".join(errors))
        self.members = unit_members(self.unit.unit_id)
        visible = [
            obj for obj in self.members
            if object_render_visible(obj, self.context.view_layer)
        ]
        if not visible:
            return "SKIPPED"

        self.work_collection = pipeline_collection(WORK_COLLECTION)
        ensure_scene_collection(self.context.scene, self.work_collection)
        clear_collection(self.work_collection)
        self.snapshot = EvaluationSnapshot(self.context)
        self.context_state = ContextState(self.context)
        self.snapshot.isolate_source_root(self.project)
        for source in self.members:
            self.snapshot.hide(source)
        self.context.scene.cycles.samples = self.project.cycles_samples
        self.image = create_float_image(
            beauty_image_name(self.layer, self.unit, self.state),
            int(self.unit.resolution),
        )
        try:
            self.image.colorspace_settings.name = 'sRGB'
        except (TypeError, ValueError):
            pass
        tag_image(
            self.image,
            self.unit,
            self.layer,
            self.state,
            'BEAUTY',
        )
        for source in self.members:
            self.receivers.append(
                copy_receiver(
                    self.context,
                    source,
                    self.work_collection,
                    self.image,
                    self.layer.layer_type,
                )
            )
        self.signature = signature_for_receivers(
            self.receivers,
            self.layer.layer_type,
        )
        all_passes = {
            'DIRECT', 'INDIRECT', 'COLOR', 'DIFFUSE',
            'GLOSSY', 'TRANSMISSION', 'EMIT',
        }
        self.config = BakeConfigurationSnapshot(self.context.scene)
        self.config.configure('COMBINED', self.project.margin, False, all_passes)
        log.info(
            "Beauty",
            f'Start {self.state.title()} unit "{self.unit.display_name}": '
            f'{len(self.receivers)} object(s), {self.unit.resolution}px, '
            f'{self.project.cycles_samples} samples',
        )
        return "READY"

    def select_receiver(self, index):
        self._resolve()
        receiver = self.receivers[index]
        set_target_image(receiver, self.image)
        select_only(self.context, receiver["object"])
        _show_bake_stage(
            self.operator,
            f"Combined {index + 1}/{len(self.receivers)} — {receiver['source'].name}",
            index + 1,
            len(self.receivers) + 3,
        )
        return receiver

    def bake_kwargs(self):
        bake = self.context.scene.render.bake
        return supported_bake_kwargs({
            "type": 'COMBINED',
            "use_clear": False,
            "target": 'IMAGE_TEXTURES',
            "margin": self.project.margin,
            "margin_type": getattr(bake, "margin_type", 'ADJACENT_FACES'),
            "normal_space": bake.normal_space,
            "normal_r": bake.normal_r,
            "normal_g": bake.normal_g,
            "normal_b": bake.normal_b,
        })

    def finish(self):
        self._resolve()
        step_count = len(self.receivers) + 3
        _show_bake_stage(
            self.operator,
            "Save external Beauty",
            len(self.receivers) + 1,
            step_count,
        )
        # Match SimpleBake: establish an external file before compositor work.
        # It is staged beside the final path; the previous successful file is
        # replaced only after every Blender-side step has been prepared.
        staged_file = stage_beauty_image(
            self.context, self.unit, self.state, self.image
        )
        published = False
        try:
            _show_bake_stage(
                self.operator,
                "Compositor denoise",
                len(self.receivers) + 2,
                step_count,
            )
            try:
                denoise_external_beauty(
                    self.context.scene,
                    self.image,
                    staged_file.staging_path,
                )
            except Exception as exc:
                self.warnings.append(f"denoise failed, raw Beauty used: {exc}")
                if self.operator:
                    self.operator.report(
                        {'WARNING'},
                        f"Denoise failed; using raw Beauty: {exc}",
                    )
                log.warning(
                    "Beauty",
                    f'{self.unit.display_name}: denoise failed; using raw Beauty: {exc}',
                )
            _show_bake_stage(
                self.operator,
                "Build preview result",
                len(self.receivers) + 3,
                step_count,
            )
            staged, self.created_materials = prepare_materials(
                self.unit,
                self.layer,
                self.members,
                self.state,
                self.image,
            )
            check_commit(
                self.unit,
                self.layer,
                self.receivers,
                staged,
                self.signature,
            )
            commit_staged_file(staged_file, self.image, 'PNG')
            published = True
            commit_generated_geometry(
                self.context,
                self.unit,
                self.layer,
                self.receivers,
                self.signature,
            )
            commit_materials(
                self.unit,
                self.layer,
                self.members,
                self.state,
                staged,
                self.created_materials,
            )
        except Exception:
            if published:
                staged_file.rollback()
            raise
        finally:
            staged_file.cleanup()
        staged_file.finalize()
        log.info("Beauty", f'Saved external image: "{staged_file.final_path}"')
        old_image_name = (
            self.unit.day_beauty_image
            if self.state == 'DAY'
            else self.unit.evening_beauty_image
        )
        if old_image_name and old_image_name != self.image.name:
            old_image = bpy.data.images.get(old_image_name)
            if old_image and old_image.users == 0:
                bpy.data.images.remove(old_image)
        self.image.name = beauty_image_name(
            self.layer,
            self.unit,
            self.state,
        )
        if self.state == 'DAY':
            self.unit.day_signature = self.signature
            self.unit.day_beauty_image = self.image.name
            self.unit.day_status = "Ready"
        else:
            self.unit.evening_signature = self.signature
            self.unit.evening_beauty_image = self.image.name
            self.unit.evening_status = "Ready"
        other_signature = (
            self.unit.evening_signature
            if self.state == 'DAY'
            else self.unit.day_signature
        )
        if other_signature and other_signature != self.signature:
            if self.state == 'DAY':
                self.unit.evening_status = (
                    "Structurally incompatible — rebake required"
                )
            else:
                self.unit.day_status = (
                    "Structurally incompatible — rebake required"
                )
        _record(
            self.project,
            self.unit,
            self.state,
            self.signature,
            self.image,
            "SUCCESS",
            "; ".join(self.warnings),
        )
        viewport_overlay.mark_baked(
            self.project,
            self.unit,
            self.state,
            'BEAUTY',
        )
        self.finished = True
        log.info(
            "Beauty",
            f'Completed {self.state.title()} unit "{self.unit.display_name}" '
            f'in {log.duration(time.monotonic() - self.started_at)}',
        )
        self.cleanup(keep_image=True)

    def _label(self):
        try:
            self._resolve()
        except PipelineBakeError:
            return None
        return self.unit.display_name

    def fail(self, exc):
        label = self._label()
        # Validation and transaction errors explain themselves; anything else
        # is a bug and needs its traceback. fail() is called from except blocks.
        log.error(
            "Beauty",
            f'Failed {self.state.title()} unit "{label or self.unit_id}" after '
            f'{log.duration(time.monotonic() - self.started_at)}: {exc}',
            with_traceback=not isinstance(exc, PipelineBakeError),
        )
        if label is not None:
            _record(
                self.project,
                self.unit,
                self.state,
                "",
                self.image,
                "FAILED",
                str(exc),
            )
        self.cleanup(keep_image=False)

    def cancel(self, reason):
        label = self._label()
        log.warning(
            "Beauty",
            f'Cancelled {self.state.title()} unit "{label or self.unit_id}": '
            f'{reason}; previous result kept',
        )
        if label is not None:
            _record(
                self.project,
                self.unit,
                self.state,
                "",
                None,
                "CANCELLED",
                reason,
            )
        self.cleanup(keep_image=False)

    def cleanup(self, keep_image=False):
        cleanup_errors = []
        if self.config:
            try:
                self.config.restore()
            except Exception as exc:
                cleanup_errors.append(f"bake settings: {exc}")
            self.config = None
        if self.work_collection:
            try:
                remove_work_collection(self.work_collection)
            except Exception as exc:
                cleanup_errors.append(f"work collection: {exc}")
            self.work_collection = None
        for material in self.created_materials:
            try:
                if material.users == 0:
                    bpy.data.materials.remove(material)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(f"material: {exc}")
        if not keep_image and self.image:
            try:
                if self.image.users == 0:
                    remove_image(self.image)
            except (ReferenceError, RuntimeError) as exc:
                cleanup_errors.append(f"image: {exc}")
        if self.snapshot:
            try:
                self.snapshot.restore()
            except Exception as exc:
                cleanup_errors.append(f"visibility: {exc}")
            self.snapshot = None
        if self.context_state:
            try:
                self.context_state.restore(self.context)
            except Exception as exc:
                cleanup_errors.append(f"context: {exc}")
            self.context_state = None
        if cleanup_errors:
            log.warning(
                "Beauty",
                "Cleanup completed with warnings: " + "; ".join(cleanup_errors),
            )


def bake_lightmap_unit(context, unit, operator=None):
    project = context.scene.pm_vr_project
    layer = find_layer(project, unit.render_layer_id)
    state = project.active_lighting_state
    issues = validate_unit(context, unit, require_visible=True)
    errors = [issue.message for issue in issues if issue.severity == 'ERROR']
    if errors:
        raise PipelineBakeError("; ".join(errors))
    members = unit_members(unit.unit_id)
    if not any(object_render_visible(obj, context.view_layer) for obj in members):
        return "SKIPPED", "fully hidden in active state"

    work_collection = pipeline_collection(WORK_COLLECTION)
    ensure_scene_collection(context.scene, work_collection)
    clear_collection(work_collection)
    snapshot = EvaluationSnapshot(context)
    context_state = ContextState(context)
    raw = albedo = normal = None
    receivers = []
    created_materials = []
    try:
        snapshot.isolate_source_root(project)
        for source in members:
            snapshot.hide(source)
        context.scene.cycles.samples = project.cycles_samples
        resolution = int(unit.resolution)
        raw = create_float_image(
            f"PMVR_Lightmap_{unit.artifact_key[:8]}_{state}_{uuid.uuid4().hex[:8]}",
            resolution,
        )
        tag_image(raw, unit, layer, state, 'LIGHTMAP')
        albedo = create_float_image(
            f"__PMVR_LM_ALBEDO_{uuid.uuid4().hex}", resolution, (1.0, 1.0, 1.0, 1.0)
        )
        normal = create_float_image(
            f"__PMVR_LM_NORMAL_{uuid.uuid4().hex}", resolution, (0.5, 0.5, 1.0, 1.0)
        )
        for source in members:
            receivers.append(
                copy_receiver(context, source, work_collection, raw, 'LIGHTMAP')
            )
        signature = signature_for_receivers(receivers, 'LIGHTMAP')
        _show_bake_stage(operator, "Lightmap / Lighting", 1, 5)
        bake_receivers(
            context,
            receivers,
            raw,
            'DIFFUSE',
            project.margin,
            {'DIRECT', 'INDIRECT'},
        )
        context.scene.cycles.samples = 1
        try:
            _show_bake_stage(operator, "Denoise guide / Albedo", 2, 5)
            bake_receivers(
                context,
                receivers,
                albedo,
                'DIFFUSE',
                project.margin,
                {'COLOR'},
            )
            _show_bake_stage(operator, "Denoise guide / Normal", 3, 5)
            bake_receivers(context, receivers, normal, 'NORMAL', project.margin)
        finally:
            context.scene.cycles.samples = project.cycles_samples
        try:
            _show_bake_stage(operator, "Compositor denoise", 4, 5)
            denoise_image(context.scene, raw, albedo, normal)
        except Exception as exc:
            if operator:
                operator.report({'WARNING'}, f"Lightmap denoise failed; using raw result: {exc}")
            log.warning("Lightmap", f'{unit.display_name}: denoise failed; using raw result: {exc}')
        _show_bake_stage(operator, "Build preview result", 5, 5)
        staged, created_materials = prepare_lightmap_materials(
            unit, layer, members, state, raw
        )
        check_commit(unit, layer, receivers, staged, signature, mode='LIGHTMAP')
        staged_file = stage_lightmap_image(context, unit, state, raw)
        published = False
        try:
            commit_staged_file(staged_file, raw, 'OPEN_EXR')
            published = True
            commit_generated_geometry(
                context, unit, layer, receivers, signature, mode='LIGHTMAP'
            )
            commit_materials(
                unit,
                layer,
                members,
                state,
                staged,
                created_materials,
                mode='LIGHTMAP',
            )
        except Exception:
            if published:
                staged_file.rollback()
            raise
        finally:
            staged_file.cleanup()
        staged_file.finalize()
        if state == 'DAY':
            old_image_name = unit.day_lightmap_image
            unit.day_lightmap_signature = signature
            unit.day_lightmap_image = raw.name
            unit.day_lightmap_status = "Ready"
        else:
            old_image_name = unit.evening_lightmap_image
            unit.evening_lightmap_signature = signature
            unit.evening_lightmap_image = raw.name
            unit.evening_lightmap_status = "Ready"
        if old_image_name and old_image_name != raw.name:
            old_image = bpy.data.images.get(old_image_name)
            if old_image and old_image.users == 0:
                bpy.data.images.remove(old_image)
        _record(project, unit, state, signature, raw, "SUCCESS", mode='LIGHTMAP')
        viewport_overlay.mark_baked(project, unit, state, 'LIGHTMAP')
        return "SUCCESS", "Lightmap ready"
    except Exception as exc:
        _record(
            project,
            unit,
            state,
            "",
            raw,
            "FAILED",
            str(exc),
            mode='LIGHTMAP',
        )
        discard_unused_materials(created_materials)
        if raw and raw.users == 0:
            remove_image(raw)
        raise
    finally:
        remove_work_collection(work_collection)
        remove_image(albedo)
        remove_image(normal)
        snapshot.restore()
        context_state.restore(context)


# Cycles bake jobs report cancellation only through app handlers: the job's
# own modal handler consumes Esc before the queue operator can see it.
_BAKE_JOB = {"cancelled": False, "completed": False}
_QUEUE = {"running": False, "cancel_requested": False}


def _on_bake_job_cancel(*_args):
    _BAKE_JOB["cancelled"] = True


def _on_bake_job_complete(*_args):
    _BAKE_JOB["completed"] = True


def _bake_job_handler_lists():
    handlers = bpy.app.handlers
    return (
        (getattr(handlers, "object_bake_cancel", None), _on_bake_job_cancel),
        (getattr(handlers, "object_bake_complete", None), _on_bake_job_complete),
    )


def _remove_bake_job_handlers():
    for handler_list, callback in _bake_job_handler_lists():
        if handler_list is None:
            continue
        # Match by name so a reloaded module also removes stale callbacks.
        for existing in list(handler_list):
            if (
                getattr(existing, "__name__", "") == callback.__name__
                and getattr(existing, "__module__", "") == __name__
            ):
                handler_list.remove(existing)


def _install_bake_job_handlers():
    _remove_bake_job_handlers()
    for handler_list, callback in _bake_job_handler_lists():
        if handler_list is not None:
            handler_list.append(callback)


def _reset_bake_job_flags():
    _BAKE_JOB["cancelled"] = False
    _BAKE_JOB["completed"] = False


def shutdown():
    """Detach bake-job handlers when the add-on is unregistered."""
    _remove_bake_job_handlers()
    _QUEUE["running"] = False
    _QUEUE["cancel_requested"] = False


def _ensure_object_mode(context):
    obj = getattr(context, "active_object", None)
    if obj and obj.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass


class PMVR_OT_CancelBakeQueue(bpy.types.Operator):
    bl_idname = "pmvr.cancel_bake_queue"
    bl_label = "Cancel Bake"
    bl_description = (
        "Stop the running bake queue. The unit in progress is discarded and "
        "earlier results stay unchanged. Esc also stops a running Cycles pass "
        "immediately"
    )

    @classmethod
    def poll(cls, context):
        return bool(context.scene and context.scene.pm_vr_project.operation_running)

    def execute(self, context):
        if not _QUEUE["running"]:
            # No queue owns the flag (for example after an interrupted run);
            # release the Bake button instead of leaving it disabled.
            context.scene.pm_vr_project.operation_running = False
            self.report({'INFO'}, "No bake is running; Bake is available again")
            return {'FINISHED'}
        _QUEUE["cancel_requested"] = True
        self.report(
            {'WARNING'},
            "Cancelling after the current Cycles pass; press Esc to stop it now",
        )
        return {'FINISHED'}


class PMVR_OT_BakeQueue(bpy.types.Operator):
    bl_idname = "pmvr.bake_queue"
    bl_label = "Bake Queue"
    bl_description = "Bake every queued unit for the checked Day/Evening states"

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(project.bake_queue and not project.operation_running)

    def execute(self, context):
        if bpy.app.background:
            self.report({'ERROR'}, "Interactive Cycles bake is required")
            return {'CANCELLED'}
        project = context.scene.pm_vr_project
        states = []
        if project.bake_day:
            states.append('DAY')
        if project.bake_evening:
            states.append('EVENING')
        if not states:
            self.report({'ERROR'}, "Choose Day, Evening, or both")
            return {'CANCELLED'}
        _ensure_object_mode(context)
        self._viewport_shading = switch_viewports_to_wireframe(context)
        _QUEUE["cancel_requested"] = False
        self._queue_started_at = time.monotonic()
        log.info(
            "Bake",
            f"Queue start: {len(project.bake_queue)} unit(s), "
            f"states {', '.join(states)}, mode {project.bake_mode}, "
            f"{project.cycles_samples} samples, margin {project.margin}px",
        )
        log.info("Bake", log.environment(context))
        if project.bake_mode == 'LIGHTMAP':
            return self._execute_lightmap(context, states)

        entries = [entry.unit_id for entry in project.bake_queue]
        self._project = project
        self._states = states
        self._original_state = project.active_lighting_state
        self._jobs = [
            (state, unit_id) for state in states for unit_id in entries
        ]
        self._job_cursor = 0
        self._current_runtime = None
        self._receiver_index = 0
        self._waiting_for_bake = False
        self._job_seen_running = False
        self._job_started_at = 0.0
        self._cancel_requested = False
        self._cancel_reason = ""
        self._discarded_unit = ""
        self._succeeded = self._skipped = self._failed = self._warned = 0
        self._timer = None
        self._feedback = BakeProgressFeedback(
            context,
            title="PM VR BEAUTY BAKER",
        )
        self._pmvr_feedback = self._feedback
        project.operation_running = True
        _QUEUE["running"] = True
        _install_bake_job_handlers()
        try:
            self._feedback.start(len(self._jobs))
            self._feedback.set_candidate_count(len(self._jobs))
            self._feedback.add_message(
                'INFO',
                "Esc cancels the whole queue; finished units are kept",
            )
            context.window_manager.progress_begin(0, len(self._jobs))
            self._timer = context.window_manager.event_timer_add(
                0.2,
                window=context.window,
            )
            context.window_manager.modal_handler_add(self)
            result = self._start_next_job(context)
            if result:
                return result
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._failed += 1
            log.error("Beauty", f"Could not start modal bake: {exc}", with_traceback=True)
            if self._current_runtime:
                self._current_runtime.fail(exc)
                self._current_runtime = None
            return self._finish_modal(context, cancelled=True)

    def _request_cancel(self, reason):
        if not self._cancel_requested:
            self._cancel_requested = True
            self._cancel_reason = reason
            log.warning("Bake", f"Cancel requested: {reason}")
            self._feedback.add_message('WARNING', f"Cancelling: {reason}")

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:
            # Never leave the scene isolated or the Bake button disabled after
            # an unexpected error inside an unattended queue.
            log.error("Bake", f"Unexpected queue error: {exc}", with_traceback=True)
            if self._current_runtime:
                self._failed += 1
                try:
                    self._current_runtime.fail(exc)
                except Exception as cleanup_exc:
                    log.error("Bake", f"Cleanup after queue error failed: {cleanup_exc}")
                self._current_runtime = None
            return self._finish_modal(context, cancelled=True)

    def _modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._request_cancel("Esc pressed")
            if not self._waiting_for_bake:
                return self._finish_modal(context, cancelled=True)
            return {'PASS_THROUGH'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if _QUEUE["cancel_requested"]:
            self._request_cancel("Cancel button")
        if not self._waiting_for_bake:
            if self._cancel_requested:
                return self._finish_modal(context, cancelled=True)
            return {'PASS_THROUGH'}

        running = bpy.app.is_job_running('OBJECT_BAKE')
        if running:
            self._job_seen_running = True
            return {'PASS_THROUGH'}
        if (
            not self._job_seen_running
            and not _BAKE_JOB["completed"]
            and not _BAKE_JOB["cancelled"]
            and time.monotonic() - self._job_started_at < 1.0
        ):
            return {'PASS_THROUGH'}

        self._waiting_for_bake = False
        if _BAKE_JOB["cancelled"]:
            # Esc in the Cycles job or its status-bar cancel button. The
            # interrupted member left a partial atlas: discard the whole unit.
            self._request_cancel("Cycles bake was cancelled")
        runtime = self._current_runtime
        if runtime and not self._cancel_requested:
            receiver = runtime.receivers[self._receiver_index]
            log.info(
                "Beauty",
                f'Baked {self._receiver_index + 1}/{len(runtime.receivers)} '
                f'"{receiver["source"].name}" in unit '
                f'"{runtime._label() or runtime.unit_id}" '
                f'({log.duration(time.monotonic() - self._job_started_at)})',
            )
        if self._cancel_requested:
            return self._finish_modal(context, cancelled=True)
        self._receiver_index += 1
        if self._receiver_index < len(self._current_runtime.receivers):
            try:
                return self._start_receiver(context)
            except Exception as exc:
                self._failed += 1
                self._current_runtime.fail(exc)
                self._feedback.complete_object()
                self._current_runtime = None
                result = self._start_next_job(context)
                return result or {'RUNNING_MODAL'}
        try:
            self._current_runtime.finish()
            self._succeeded += 1
            if self._current_runtime.warnings:
                self._warned += 1
        except Exception as exc:
            self._failed += 1
            self._current_runtime.fail(exc)
        finally:
            self._feedback.complete_object()
            self._current_runtime = None
        result = self._start_next_job(context)
        return result or {'RUNNING_MODAL'}

    def _start_next_job(self, context):
        while self._job_cursor < len(self._jobs):
            if self._cancel_requested or _QUEUE["cancel_requested"]:
                self._request_cancel(self._cancel_reason or "Cancel button")
                return self._finish_modal(context, cancelled=True)
            state, unit_id = self._jobs[self._job_cursor]
            self._job_cursor += 1
            self._project.operation_progress = (
                (self._job_cursor - 1) / max(1, len(self._jobs))
            )
            context.window_manager.progress_update(self._job_cursor - 1)
            unit = find_unit(self._project, unit_id)
            self._feedback.begin_object(
                f"{state.title()} — {unit.display_name if unit else 'Missing unit'}",
                self._job_cursor,
                len(self._jobs),
            )
            if not unit:
                self._skipped += 1
                self._feedback.complete_object()
                continue
            try:
                activate_state(context, state)
                runtime = BeautyBakeRuntime(context, unit, self)
                # Own the runtime before preparation starts. Preparation can
                # fail after it has hidden sources, changed bake settings, or
                # created PMVR_WORK data, and must always be cleaned up.
                self._current_runtime = runtime
                status = runtime.prepare()
                if status == "SKIPPED":
                    runtime.cleanup(keep_image=False)
                    self._current_runtime = None
                    self._skipped += 1
                    self._feedback.complete_object()
                    continue
                self._receiver_index = 0
                return self._start_receiver(context)
            except Exception as exc:
                self._failed += 1
                if self._current_runtime:
                    self._current_runtime.fail(exc)
                    self._current_runtime = None
                else:
                    log.error(
                        "Beauty",
                        f'Failed {state.title()} unit "{unit.display_name}": {exc}',
                        with_traceback=not isinstance(exc, PipelineBakeError),
                    )
                self._feedback.complete_object()
        return self._finish_modal(context, cancelled=False)

    def _start_receiver(self, _context):
        runtime = self._current_runtime
        runtime.select_receiver(self._receiver_index)
        _reset_bake_job_flags()
        result = bpy.ops.object.bake(
            'INVOKE_DEFAULT',
            **runtime.bake_kwargs(),
        )
        if 'CANCELLED' in result:
            self._request_cancel("Cycles bake could not start")
            return self._finish_modal(runtime.context, cancelled=True)
        self._waiting_for_bake = True
        self._job_seen_running = bpy.app.is_job_running('OBJECT_BAKE')
        self._job_started_at = time.monotonic()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        # Blender cancels modal operators when a file is loaded or the window
        # closes. Old datablock references may already be invalid.
        try:
            if self._current_runtime:
                self._current_runtime.cleanup(keep_image=False)
        except Exception:
            pass
        self._current_runtime = None
        try:
            if self._timer:
                context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        self._timer = None
        _remove_bake_job_handlers()
        _QUEUE["running"] = False
        _QUEUE["cancel_requested"] = False
        try:
            self._project.operation_running = False
        except Exception:
            pass

    def _finish_modal(self, context, cancelled):
        if self._current_runtime:
            self._discarded_unit = self._current_runtime._label() or "removed unit"
            self._current_runtime.cancel(self._cancel_reason or "queue stopped")
            self._current_runtime = None
        timer = getattr(self, "_timer", None)
        if timer:
            context.window_manager.event_timer_remove(timer)
            self._timer = None
        _remove_bake_job_handlers()
        _QUEUE["running"] = False
        _QUEUE["cancel_requested"] = False
        context.window_manager.progress_end()
        self._project.operation_running = False
        self._project.operation_progress = 1.0
        try:
            activate_state(context, self._original_state)
        except Exception as exc:
            log.warning("Bake", f"Could not restore {self._original_state}: {exc}")
        restore_viewport_shading(self._viewport_shading)
        state_label = " + ".join(state.title() for state in self._states)
        summary = (
            f"Beauty ({state_label}): {self._succeeded} ready, "
            f"{self._skipped} skipped, {self._failed} failed"
        )
        if self._warned:
            summary += f", {self._warned} with warnings"
        if cancelled:
            summary += ", cancelled"
            if self._discarded_unit:
                summary += f' ("{self._discarded_unit}" discarded)'
        self._project.last_operation_summary = summary
        log.info("Bake", f"{summary} in {log.duration(time.monotonic() - self._queue_started_at)}")
        self._feedback.finish(
            summary,
            has_errors=bool(self._failed or cancelled or self._warned),
        )
        self.report(
            {'WARNING'} if self._failed or cancelled or self._warned else {'INFO'},
            summary + ("; see the PMVR Pipeline Log text" if self._failed or self._warned else ""),
        )
        return (
            {'FINISHED'}
            if (self._succeeded or self._skipped) and not cancelled
            else {'CANCELLED'}
        )

    def _execute_lightmap(self, context, states):
        project = context.scene.pm_vr_project
        entries = [entry.unit_id for entry in project.bake_queue]
        original_state = project.active_lighting_state
        total_jobs = len(entries) * len(states)
        feedback = BakeProgressFeedback(context)
        project.operation_running = True
        succeeded = skipped = failed = 0
        cancelled = False
        try:
            feedback.start(total_jobs)
            feedback.set_candidate_count(total_jobs)
            self._pmvr_feedback = feedback
            context.window_manager.progress_begin(0, total_jobs)
            job_index = 0
            for state in states:
                if cancelled:
                    break
                activate_state(context, state)
                for unit_id in entries:
                    job_index += 1
                    unit = find_unit(project, unit_id)
                    feedback.begin_object(
                        f"{state.title()} — {unit.display_name if unit else 'Missing unit'}",
                        job_index,
                        total_jobs,
                    )
                    if not unit:
                        skipped += 1
                        continue
                    unit_started = time.monotonic()
                    try:
                        status, _message = bake_lightmap_unit(
                            context,
                            unit,
                            self,
                        )
                        if status == "SKIPPED":
                            skipped += 1
                            log.info("Lightmap", f'[{state}] Skipped "{unit.display_name}": fully hidden')
                        else:
                            succeeded += 1
                            log.info(
                                "Lightmap",
                                f'[{state}] Completed "{unit.display_name}" '
                                f'in {log.duration(time.monotonic() - unit_started)}',
                            )
                    except PipelineBakeCancelled as exc:
                        cancelled = True
                        log.warning("Lightmap", f'Cancelled in "{unit.display_name}": {exc}')
                        break
                    except Exception as exc:
                        failed += 1
                        log.error(
                            "Lightmap",
                            f'[{state}] Failed "{unit.display_name}": {exc}',
                            with_traceback=not isinstance(exc, PipelineBakeError),
                        )
                    finally:
                        feedback.complete_object()
        finally:
            context.window_manager.progress_end()
            project.operation_running = False
            restore_viewport_shading(self._viewport_shading)
            try:
                activate_state(context, original_state)
            except Exception as exc:
                log.warning("Bake", f"Restore warning: {exc}")
        state_label = " + ".join(state.title() for state in states)
        summary = (
            f"Lightmap ({state_label}): {succeeded} ready, "
            f"{skipped} skipped, {failed} failed"
        )
        if cancelled:
            summary += ", cancelled"
        project.last_operation_summary = summary
        log.info("Bake", f"{summary} in {log.duration(time.monotonic() - self._queue_started_at)}")
        feedback.finish(summary, has_errors=bool(failed or cancelled))
        self.report({'WARNING'} if failed or cancelled else {'INFO'}, summary)
        return {'FINISHED'} if (succeeded or skipped) and not cancelled else {'CANCELLED'}


CLASSES = (PMVR_OT_BakeQueue, PMVR_OT_CancelBakeQueue)
