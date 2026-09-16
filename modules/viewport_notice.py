"""Short, reusable viewport notices styled like the PM Lightmap overlay."""

from dataclasses import dataclass
import time

import blf
import bpy
import gpu

from .lightmap_baker.progress_overlay import (
    _draw_rect,
    _draw_text,
    _font_size,
    _message_color,
)


@dataclass
class ViewportNotice:
    title: str
    summary: str
    lines: list
    level: str
    expires_at: float


_notice = None
_handle = None
_timer_registered = False


def _redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    if not window_manager:
        return
    for window in window_manager.windows:
        if not window.screen:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _remove_handler():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except (ReferenceError, RuntimeError, TypeError):
            pass
        _handle = None
    _redraw()


def _timer():
    global _notice, _timer_registered
    if _notice and time.monotonic() < _notice.expires_at:
        _redraw()
        return min(0.5, _notice.expires_at - time.monotonic())
    _notice = None
    _remove_handler()
    _timer_registered = False
    return None


def _draw():
    notice = _notice
    region = getattr(bpy.context, "region", None)
    if not notice or not region or region.width < 260 or region.height < 160:
        return
    try:
        font_id = 0
        panel_x = 16
        panel_y = 16
        panel_width = min(620, region.width - 32)
        lines = notice.lines[:5]
        panel_height = 92 + len(lines) * 21
        top = panel_y + panel_height
        text_x = panel_x + 16
        text_width = panel_width - 32
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        _draw_rect(
            shader,
            panel_x,
            panel_y,
            panel_width,
            panel_height,
            (0.025, 0.03, 0.045, 0.9),
        )
        accent = {
            'ERROR': (0.95, 0.25, 0.2, 1.0),
            'WARNING': (0.95, 0.58, 0.12, 1.0),
            'SUCCESS': (0.2, 0.72, 0.38, 1.0),
        }.get(notice.level, (0.16, 0.55, 1.0, 1.0))
        _draw_rect(shader, panel_x, panel_y, 6, panel_height, accent)
        gpu.state.blend_set('NONE')

        _font_size(font_id, 16)
        _draw_text(
            font_id,
            notice.title,
            text_x,
            top - 27,
            (0.92, 0.95, 1.0, 1.0),
            text_width,
        )
        _font_size(font_id, 13)
        _draw_text(
            font_id,
            notice.summary,
            text_x,
            top - 53,
            (0.72, 0.8, 0.92, 1.0),
            text_width,
        )
        y = top - 78
        for line_level, text in lines:
            _font_size(font_id, 12)
            _draw_text(
                font_id,
                text,
                text_x,
                y,
                _message_color(line_level),
                text_width,
            )
            y -= 21
    except Exception as exc:
        gpu.state.blend_set('NONE')
        print(f"[PM VR] Viewport notice draw failed: {exc}")


def show(title, summary, lines=(), level='INFO', seconds=8.0):
    global _notice, _handle, _timer_registered
    if bpy.app.background:
        return
    _notice = ViewportNotice(
        title=title,
        summary=summary,
        lines=list(lines),
        level=level,
        expires_at=time.monotonic() + seconds,
    )
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw,
            (),
            'WINDOW',
            'POST_PIXEL',
        )
    if not _timer_registered:
        bpy.app.timers.register(_timer, first_interval=0.25)
        _timer_registered = True
    _redraw()


def shutdown():
    global _notice, _timer_registered
    if _timer_registered and bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    _timer_registered = False
    _notice = None
    _remove_handler()
