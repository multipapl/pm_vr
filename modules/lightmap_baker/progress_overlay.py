"""Drawing helpers for the lightmap bake viewport overlay."""

import time

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader


def _font_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, 72)


def _fit_text(font_id, text, max_width):
    if blf.dimensions(font_id, text)[0] <= max_width:
        return text

    suffix = "..."
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle] + suffix
        if blf.dimensions(font_id, candidate)[0] <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + suffix


def _draw_text(font_id, text, x, y, color, max_width):
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, _fit_text(font_id, text, max_width))


def _draw_rect(shader, x, y, width, height, color):
    vertices = (
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
    )
    batch = batch_for_shader(
        shader,
        'TRIS',
        {"pos": vertices},
        indices=((0, 1, 2), (0, 2, 3)),
    )
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _message_color(level):
    if level == "ERROR":
        return (1.0, 0.35, 0.3, 1.0)
    if level == "WARNING":
        return (1.0, 0.72, 0.25, 1.0)
    if level == "STATUS":
        return (0.48, 0.78, 1.0, 1.0)
    return (0.82, 0.84, 0.88, 1.0)


class ViewportProgressOverlay:
    def __init__(self):
        self.feedback = None
        self.handle = None

    def show(self, feedback):
        self.feedback = feedback
        if self.handle is None:
            self.handle = bpy.types.SpaceView3D.draw_handler_add(
                self._draw,
                (),
                'WINDOW',
                'POST_PIXEL',
            )
        self.redraw()

    def hide(self):
        if self.handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    self.handle,
                    'WINDOW',
                )
            except (ReferenceError, RuntimeError, TypeError):
                pass
            self.handle = None
        self.feedback = None
        self.redraw()

    def redraw(self):
        window_manager = getattr(bpy.context, "window_manager", None)
        if not window_manager:
            return
        for window in window_manager.windows:
            screen = window.screen
            if not screen:
                continue
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    def flush(self):
        """Draw pending overlay changes before a blocking Blender operation."""
        self.redraw()
        if bpy.app.background:
            return
        try:
            bpy.ops.wm.redraw_timer(
                type='DRAW_WIN_SWAP',
                iterations=1,
            )
        except (AttributeError, RuntimeError):
            pass

    def _draw(self):
        feedback = self.feedback
        region = getattr(bpy.context, "region", None)
        if (
            not feedback
            or not region
            or region.width < 260
            or region.height < 180
        ):
            return

        try:
            self._draw_panel(feedback, region)
        except Exception as exc:
            gpu.state.blend_set('NONE')
            print(
                "[PM Lightmap] WARNING: "
                f"viewport feedback draw failed: {exc}"
            )

    def _draw_panel(self, feedback, region):
        font_id = 0
        panel_x = 16
        panel_width = min(620, region.width - 32)
        history_count = max(
            0,
            min(len(feedback.messages), (region.height - 160) // 19),
        )
        history = (
            feedback.messages[-history_count:]
            if history_count
            else []
        )
        panel_height = 136 + (len(history) * 19)
        panel_y = 16
        top = panel_y + panel_height
        text_x = panel_x + 14
        text_width = panel_width - 28

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        _draw_rect(
            shader,
            panel_x,
            panel_y,
            panel_width,
            panel_height,
            (0.025, 0.03, 0.045, 0.88),
        )
        self._draw_progress_bar(
            shader,
            feedback,
            text_x,
            top - 104,
            text_width,
        )
        gpu.state.blend_set('NONE')

        elapsed = max(0.0, time.monotonic() - feedback.started_at)
        _font_size(font_id, 16)
        _draw_text(
            font_id,
            f"PM LIGHTMAP BAKER   {feedback.percent:.0f}%"
            f"   {elapsed:.0f}s",
            text_x,
            top - 28,
            (0.92, 0.95, 1.0, 1.0),
            text_width,
        )
        self._draw_status(
            font_id,
            feedback,
            text_x,
            top,
            text_width,
        )
        self._draw_history(
            font_id,
            history,
            text_x,
            top - 130,
            text_width,
        )

    @staticmethod
    def _draw_progress_bar(shader, feedback, x, y, width):
        _draw_rect(
            shader,
            x,
            y,
            width,
            10,
            (0.12, 0.14, 0.18, 1.0),
        )
        _draw_rect(
            shader,
            x,
            y,
            width * (feedback.percent / 100.0),
            10,
            feedback.bar_color,
        )

    @staticmethod
    def _draw_status(font_id, feedback, x, top, width):
        _font_size(font_id, 14)
        object_label = feedback.object_name or "Preparing bake queue"
        if feedback.object_count and feedback.object_index:
            object_label = (
                f"Object {feedback.object_index}/{feedback.object_count}"
                f"  |  {object_label}"
            )
        _draw_text(
            font_id,
            object_label,
            x,
            top - 54,
            (0.78, 0.86, 1.0, 1.0),
            width,
        )
        _draw_text(
            font_id,
            feedback.stage,
            x,
            top - 77,
            (0.65, 0.69, 0.76, 1.0),
            width,
        )

    @staticmethod
    def _draw_history(font_id, history, x, y, width):
        _font_size(font_id, 12)
        for message in reversed(history):
            _draw_text(
                font_id,
                message.text,
                x,
                y,
                _message_color(message.level),
                width,
            )
            y -= 19
