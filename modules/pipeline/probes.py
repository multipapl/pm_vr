"""World-aligned reflection probes from configured cameras in every look."""

import math
import os
import time
import uuid

import bpy
from . import looks, platform
from mathutils import Matrix

from ..lightmap_baker.progress import BakeProgressFeedback
from .bake_scene import EvaluationSnapshot, ensure_scene_collection, pipeline_collection, remove_work_collection
from .constants import WORK_COLLECTION
from .export import other_state_objects
from .identity import export_layer_members
from .state import activate_state, collection_contains
from .variants import usd_name
from . import bake, log


def migrate(project):
    if project.probe_collection_version == 0 and project.initialized:
        collection = bpy.data.collections.get('Probes')
        if (project.legacy_working_directory and not project.probe_collection and collection
                and project.source_root_collection and collection_contains(project.source_root_collection, collection)):
            project.probe_collection = collection
        project.probe_collection_version = 1


def runtime_warnings(project):
    if not project.probe_collection:
        return []
    runtime = {obj.as_pointer() for layer in project.render_layers if layer.layer_type == 'RUNTIME'
               for obj in export_layer_members(layer.layer_id)}
    return [(obj.name, 'Probe camera is not assigned to a Runtime layer')
            for obj in project.probe_collection.all_objects
            if obj.type == 'CAMERA' and obj.as_pointer() not in runtime]


def probe_cameras(project, state):
    """Equirectangular cameras assigned to Runtime layers, by name. Cameras
    that live only in the other state's lighting collection are left out,
    as in export."""
    migrate(project)
    if project.probe_collection:
        return sorted((obj for obj in project.probe_collection.all_objects if obj.type == 'CAMERA'),
                      key=lambda obj: obj.name.casefold())
    skipped = other_state_objects(project, state)
    cameras = {}
    for layer in project.render_layers:
        if layer.layer_type != 'RUNTIME':
            continue
        for obj in export_layer_members(layer.layer_id):
            if (
                obj.type == 'CAMERA'
                and obj.data.type == 'PANO'
                and getattr(obj.data, "panorama_type", 'EQUIRECTANGULAR') == 'EQUIRECTANGULAR'
                and obj.as_pointer() not in skipped
            ):
                cameras[obj.name] = obj
    return [cameras[name] for name in sorted(cameras, key=str.casefold)]


def probe_states(project):
    looks.ensure(project)
    return [look.look_id for look in project.lighting_looks]


def probe_directory(project):
    if project.probe_output_directory:
        return bpy.path.abspath(project.probe_output_directory)
    usdz = os.path.normpath(bpy.path.abspath(project.usdz_output_directory))
    return os.path.join(os.path.dirname(usdz), "probes")


def probe_path(project, camera, state):
    # Named like the camera's prim in the Runtime USDZ: the app pairs them by name.
    suffix = looks.suffix(project, state)
    return os.path.join(probe_directory(project), f"{usd_name(camera.name)}{suffix}.exr")


def preview_path(project, exr_path):
    return os.path.join(bpy.path.abspath(project.probe_preview_directory),
                        os.path.splitext(os.path.basename(exr_path))[0] + '.jpg')


def _atomic_render(result, path, scene):
    folder, filename = os.path.split(path)
    os.makedirs(folder, exist_ok=True)
    stem, extension = os.path.splitext(filename)
    staged = os.path.join(folder, f'.{stem}.pmvr_tmp_{uuid.uuid4().hex[:12]}{extension}')
    try:
        result.save_render(staged, scene=scene)
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def _render_result():
    return next((image for image in bpy.data.images if image.type == 'RENDER_RESULT'), None)


class ProbeSession:
    """Scene settings for probe renders; end() puts every one of them back.

    The scene renders as for a bake (generated results and objects outside
    Source Root hidden, the state's lighting on) from a temporary camera at
    each probe, world-aligned: looking along +Y with Z up, whatever the
    probe camera's own rotation. Its lens settings are the probe camera's."""

    RENDER = (
        "engine", "resolution_x", "resolution_y", "resolution_percentage",
        "pixel_aspect_x", "pixel_aspect_y", "use_border", "use_compositing",
        "use_sequencer", "film_transparent", "use_multiview", "use_persistent_data",
    )
    IMAGE = ("media_type", "file_format", "color_mode", "color_depth", "exr_codec")
    CYCLES = ("samples", "use_denoising", "denoiser", "denoising_input_passes")

    def __init__(self, context):
        scene = context.scene
        self.context = context
        self.scene = scene
        self.project = scene.pm_vr_project
        self.original_state = looks.active_id(self.project)
        self.state = None
        self.camera = scene.camera
        self.render = {name: getattr(scene.render, name) for name in self.RENDER if hasattr(scene.render, name)}
        settings = scene.render.image_settings
        self.image = {name: getattr(settings, name) for name in self.IMAGE if hasattr(settings, name)}
        self.cycles = {name: getattr(scene.cycles, name) for name in self.CYCLES if hasattr(scene.cycles, name)}
        self.display = None
        self.evaluation = EvaluationSnapshot(context)
        self.work = None
        self.probe = None

    def begin(self):
        render = self.scene.render
        render.engine = 'CYCLES'
        width = int(self.project.probe_width)
        render.resolution_x, render.resolution_y = width, width // 2
        render.resolution_percentage = 100
        render.pixel_aspect_x = render.pixel_aspect_y = 1.0
        render.use_border = False
        render.use_compositing = False
        render.use_sequencer = False
        render.film_transparent = False
        render.use_multiview = False
        # Between probes of one state only the camera moves.
        render.use_persistent_data = True
        settings = render.image_settings
        if hasattr(settings, "media_type"):
            settings.media_type = 'IMAGE'
        settings.file_format = 'OPEN_EXR'
        settings.color_mode = 'RGB'
        settings.color_depth = '16'
        # DWAA/DWAB are lossy, and Apple's EXR reader refuses them.
        settings.exr_codec = 'ZIP'
        self.scene.cycles.samples = self.project.cycles_samples
        # Cycles' own denoiser with its albedo and normal passes; the scene's
        # compositor, where a render may be denoised, is off for probes.
        cycles = self.scene.cycles
        cycles.use_denoising = True
        for name, value in (("denoiser", 'OPENIMAGEDENOISE'), ("denoising_input_passes", 'RGB_ALBEDO_NORMAL')):
            try:
                setattr(cycles, name, value)
            except (AttributeError, TypeError, ValueError):
                pass
        if not bpy.app.background:
            view = self.context.preferences.view
            self.display = view.render_display_type
            view.render_display_type = 'NONE'
        self.evaluation.isolate_source_root(self.project)
        platform.hide_runtime_helpers(self.evaluation, self.project)
        self.work = pipeline_collection(WORK_COLLECTION)
        ensure_scene_collection(self.scene, self.work)

    def aim(self, camera, state):
        if state != self.state:
            activate_state(self.context, state)
            self.state = state
        if self.probe is None:
            data = bpy.data.cameras.new('__PMVR_PROBE_CAMERA')
            data.type = 'PANO'
            data.panorama_type = 'EQUIRECTANGULAR'
            self.probe = bpy.data.objects.new("__PMVR_PROBE_CAMERA", data)
            self.work.objects.link(self.probe)
        self.probe.matrix_world = (
            Matrix.Translation(camera.matrix_world.translation)
            @ Matrix.Rotation(math.radians(90.0), 4, 'X')
        )
        self.scene.camera = self.probe

    def save(self, path):
        result = _render_result()
        if not result:
            raise RuntimeError("no render result")
        _atomic_render(result, path, self.scene)
        settings = self.scene.render.image_settings
        original = {name: getattr(settings, name) for name in (*self.IMAGE, 'quality') if hasattr(settings, name)}
        try:
            settings.file_format, settings.color_mode, settings.color_depth = 'JPEG', 'RGB', '8'
            settings.quality = 90
            _atomic_render(result, preview_path(self.project, path), self.scene)
        except Exception as exc:
            log.warning('Probes', f'EXR saved; local JPEG preview could not be written: {exc}')
        finally:
            for name, value in original.items():
                setattr(settings, name, value)

    def end(self):
        steps = (
            self._remove_camera,
            self._restore_render,
            self.evaluation.restore,
            lambda: activate_state(self.context, self.original_state),
        )
        for step in steps:
            try:
                step()
            except Exception as exc:
                log.error("Probes", f"Could not restore the scene after probes: {exc}", with_traceback=True)

    def _remove_camera(self):
        if self.probe is not None:
            data = self.probe.data
            bpy.data.objects.remove(self.probe)
            if data.users == 0:
                bpy.data.cameras.remove(data)
            self.probe = None
        remove_work_collection(self.work)
        self.work = None

    def _restore_render(self):
        self.scene.camera = self.camera
        render = self.scene.render
        # Off first: frees the data kept between probes.
        render.use_persistent_data = False
        for name, value in self.render.items():
            setattr(render, name, value)
        settings = render.image_settings
        for name, value in self.image.items():
            try:
                setattr(settings, name, value)
            except (TypeError, ValueError):
                pass
        for name, value in self.cycles.items():
            try:
                setattr(self.scene.cycles, name, value)
            except (TypeError, ValueError):
                pass
        if self.display is not None:
            self.context.preferences.view.render_display_type = self.display


_RENDER_JOB = {"cancelled": False, "completed": False}


def _on_render_cancel(*_args):
    _RENDER_JOB["cancelled"] = True


def _on_render_complete(*_args):
    _RENDER_JOB["completed"] = True


def _render_handlers():
    handlers = bpy.app.handlers
    return ((handlers.render_cancel, _on_render_cancel), (handlers.render_complete, _on_render_complete))


def _remove_render_handlers():
    for handler_list, callback in _render_handlers():
        for existing in list(handler_list):
            if (
                getattr(existing, "__name__", "") == callback.__name__
                and getattr(existing, "__module__", "") == __name__
            ):
                handler_list.remove(existing)


def _install_render_handlers():
    _remove_render_handlers()
    for handler_list, callback in _render_handlers():
        handler_list.append(callback)


def shutdown():
    _remove_render_handlers()


class PMVR_OT_RenderProbes(bpy.types.Operator):
    bl_idname = "pmvr.render_probes"
    bl_label = "Render Probes"
    bl_description = (
        "Render world-aligned EXR panoramas from the probe camera collection "
        "for all project lighting looks, with local JPEG previews. "
        "Esc cancels; finished probes are kept"
    )

    @classmethod
    def poll(cls, context):
        return not context.scene.pm_vr_project.operation_running

    def execute(self, context):
        project = context.scene.pm_vr_project
        states = probe_states(project)
        if not states:
            self.report({'ERROR'}, "Choose Day, Evening, or both")
            return {'CANCELLED'}
        if not bpy.data.filepath and any(value.startswith('//') for value in
                (project.probe_output_directory or project.usdz_output_directory, project.probe_preview_directory)):
            self.report({'ERROR'}, "Save the .blend file before using a relative probe folder")
            return {'CANCELLED'}
        for name, problem in runtime_warnings(project):
            log.warning('Probes', f'{name}: {problem}')
        self._jobs = [(state, camera.name) for state in states for camera in probe_cameras(project, state)]
        if not self._jobs:
            self.report({'ERROR'}, "No panoramic (equirectangular) camera in a Runtime layer")
            return {'CANCELLED'}
        self._project = project
        self._states = states
        self._cursor = 0
        self._succeeded = self._failed = 0
        self._cancel_reason = ""
        self._waiting = False
        self._seen_running = False
        self._started_at = 0.0
        self._timer = None
        self._run_started_at = time.monotonic()
        log.info(
            "Probes",
            f"Start: {len(self._jobs)} probe(s), states {', '.join(states)}, "
            f"{project.probe_width} x {int(project.probe_width) // 2}, {project.cycles_samples} samples, "
            f"-> {probe_directory(project)}",
        )
        log.info("Probes", log.environment(context))
        for camera in {camera.name: camera for state in states for camera in probe_cameras(project, state)}.values():
            rotation = camera.matrix_world.to_euler()
            if abs(rotation.x - math.pi / 2) > 1e-3 or abs(rotation.y) > 1e-3 or abs(rotation.z) > 1e-3:
                log.info(
                    "Probes",
                    f'"{camera.name}" is rotated ({", ".join(f"{math.degrees(a):.1f}" for a in rotation)}); '
                    "its panorama is rendered world-aligned like the others",
                )
        self._session = ProbeSession(context)
        project.operation_running = True
        bake._QUEUE["running"] = True
        bake._QUEUE["cancel_requested"] = False
        self._feedback = BakeProgressFeedback(context, title="PM VR PROBES")
        try:
            self._session.begin()
            if bpy.app.background:
                for _job in self._jobs:
                    job = self._next_job()
                    if job:
                        bpy.ops.render.render(write_still=False)
                        self._save(job)
                return self._finish(context, cancelled=False)
            self._feedback.start(len(self._jobs))
            self._feedback.add_message('INFO', "Esc cancels; finished probes are kept")
            _install_render_handlers()
            self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
            context.window_manager.modal_handler_add(self)
            self._handler_added = True
            self._start_next(context)
        except Exception as exc:
            log.error("Probes", f"Could not render probes: {exc}", with_traceback=True)
            self._failed += 1
            if not getattr(self, "_handler_added", False):
                return self._finish(context, cancelled=True)
            self._end_modal(context, cancelled=True)
        # Once the handler is registered, a run that already ended still
        # returns RUNNING_MODAL and ends from modal() (see the bake queue).
        return {'RUNNING_MODAL'}

    def _next_job(self):
        """Aim the session at the next job; (state, camera) or None when the
        camera is gone."""
        state, name = self._jobs[self._cursor]
        self._cursor += 1
        self._project.operation_progress = (self._cursor - 1) / len(self._jobs)
        camera = bpy.data.objects.get(name)
        self._feedback.begin_object(f"{looks.name(self._project, state)} — {name}", self._cursor, len(self._jobs))
        if not camera or camera.type != 'CAMERA':
            self._failed += 1
            log.error("Probes", f'"{name}" disappeared before its {looks.name(self._project, state)} render')
            return None
        self._session.aim(camera, state)
        self._started_at = time.monotonic()
        return state, camera

    def _save(self, job):
        state, camera = job
        path = probe_path(self._project, camera, state)
        try:
            self._session.save(path)
        except Exception as exc:
            self._failed += 1
            log.error("Probes", f'{looks.name(self._project, state)} probe "{camera.name}" not saved: {exc}', with_traceback=True)
            return
        self._succeeded += 1
        self._feedback.complete_object()
        log.info(
            "Probes",
            f'Rendered {looks.name(self._project, state)} probe "{camera.name}" '
            f"({log.duration(time.monotonic() - self._started_at)}) -> {path}",
        )

    def _start_next(self, context):
        while self._cursor < len(self._jobs):
            if bake._QUEUE["cancel_requested"]:
                self._cancel_reason = self._cancel_reason or "Cancel button"
                return self._end_modal(context, cancelled=True)
            job = self._next_job()
            if not job:
                continue
            self._job = job
            _RENDER_JOB["cancelled"] = _RENDER_JOB["completed"] = False
            result = bpy.ops.render.render('INVOKE_DEFAULT', write_still=False)
            if 'CANCELLED' in result:
                self._failed += 1
                self._cancel_reason = "the render could not start"
                return self._end_modal(context, cancelled=True)
            self._waiting = True
            self._seen_running = bpy.app.is_job_running('RENDER')
            return {'RUNNING_MODAL'}
        return self._end_modal(context, cancelled=False)

    def modal(self, context, event):
        ended = getattr(self, "_ended_result", None)
        if ended:
            return ended
        try:
            return self._modal(context, event)
        except Exception as exc:
            log.error("Probes", f"Unexpected error: {exc}", with_traceback=True)
            self._failed += 1
            return self._end_modal(context, cancelled=True)

    def _modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._cancel_reason = self._cancel_reason or "Esc pressed"
            if not self._waiting:
                return self._end_modal(context, cancelled=True)
            return {'PASS_THROUGH'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        if bake._QUEUE["cancel_requested"] and not self._cancel_reason:
            self._cancel_reason = "Cancel button"
        if not self._waiting:
            return {'PASS_THROUGH'}
        if bpy.app.is_job_running('RENDER'):
            self._seen_running = True
            return {'PASS_THROUGH'}
        if (
            not self._seen_running
            and not _RENDER_JOB["completed"]
            and not _RENDER_JOB["cancelled"]
            and time.monotonic() - self._started_at < 1.0
        ):
            return {'PASS_THROUGH'}
        self._waiting = False
        if _RENDER_JOB["cancelled"]:
            # Esc inside the render job: the image is unfinished, keep the old file.
            self._cancel_reason = self._cancel_reason or "render cancelled"
            return self._end_modal(context, cancelled=True)
        self._save(self._job)
        if self._cancel_reason:
            return self._end_modal(context, cancelled=True)
        return self._start_next(context)

    def _end_modal(self, context, cancelled):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        _remove_render_handlers()
        self._ended_result = self._finish(context, cancelled)
        return self._ended_result

    def _finish(self, context, cancelled):
        self._session.end()
        bake._QUEUE["running"] = False
        bake._QUEUE["cancel_requested"] = False
        self._project.operation_running = False
        self._project.operation_progress = 1.0
        states = " + ".join(looks.name(self._project, state) for state in self._states)
        summary = f"Probes ({states}): {self._succeeded} ready, {self._failed} failed"
        if cancelled:
            summary += f", cancelled ({self._cancel_reason or 'error'})"
        self._project.last_operation_summary = summary
        log.info("Probes", f"{summary} in {log.duration(time.monotonic() - self._run_started_at)}")
        self._feedback.finish(summary, has_errors=bool(self._failed or cancelled))
        self.report({'WARNING'} if self._failed or cancelled else {'INFO'}, summary)
        return {'FINISHED'} if self._succeeded and not cancelled else {'CANCELLED'}

    def cancel(self, context):
        # A file load or a closed window cancels modal operators.
        if getattr(self, "_ended_result", None):
            return
        try:
            if self._timer:
                context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        _remove_render_handlers()
        try:
            self._session.end()
        except Exception:
            pass
        bake._QUEUE["running"] = False
        bake._QUEUE["cancel_requested"] = False
        try:
            self._project.operation_running = False
        except Exception:
            pass


CLASSES = (PMVR_OT_RenderProbes,)
