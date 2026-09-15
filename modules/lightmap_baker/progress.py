"""Bake progress state shared by the logger and viewport overlay."""

from dataclasses import dataclass
from datetime import datetime
import time

import bpy

from .progress_overlay import ViewportProgressOverlay


HISTORY_SIZE = 5
LINGER_SECONDS = 5.0

_active_feedback = None
_cleanup_timer_registered = False
_overlay = ViewportProgressOverlay()


@dataclass
class FeedbackMessage:
    text: str
    level: str


def _cleanup_timer():
    global _active_feedback
    global _cleanup_timer_registered

    feedback = _active_feedback
    if feedback and feedback.running:
        _overlay.redraw()
        return 0.5
    if feedback and time.monotonic() < feedback.linger_until:
        return min(0.5, feedback.linger_until - time.monotonic())

    _overlay.hide()
    _active_feedback = None
    _cleanup_timer_registered = False
    return None


def _ensure_cleanup_timer():
    global _cleanup_timer_registered
    if _cleanup_timer_registered:
        return
    bpy.app.timers.register(_cleanup_timer, first_interval=0.5)
    _cleanup_timer_registered = True


class BakeProgressFeedback:
    """Mutable UI state owned by one foreground batch."""

    def __init__(self, context):
        self.enabled = not bpy.app.background
        self.messages = []
        self.percent = 0.0
        self.object_name = ""
        self.object_index = 0
        self.object_count = 0
        self.stage = "Starting"
        self.started_at = time.monotonic()
        self.linger_until = 0.0
        self.running = False
        self.bar_color = (0.16, 0.55, 1.0, 1.0)

    def start(self, queued_count):
        global _active_feedback
        if not self.enabled:
            return

        self.running = True
        self.started_at = time.monotonic()
        self.stage = f"Validating {queued_count} queued object(s)"
        _active_feedback = self
        _overlay.show(self)
        _ensure_cleanup_timer()
        self._redraw()

    def add_message(self, level, message):
        if not self.enabled:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.messages.append(
            FeedbackMessage(f"[{timestamp}] {message}", level)
        )
        self.messages = self.messages[-HISTORY_SIZE:]
        self._redraw()

    def set_candidate_count(self, count):
        if not self.enabled:
            return
        self.object_count = count
        self.stage = (
            f"Ready to bake {count} valid object(s)"
            if count
            else "No valid objects to bake"
        )
        self._set_percent(0.0)

    def begin_object(self, name, index, count):
        if not self.enabled:
            return
        self.object_name = name
        self.object_index = index
        self.object_count = count
        self.stage = "Preparing object"
        self._set_percent(((index - 1) / max(1, count)) * 100.0)

    def set_stage(self, message, step, step_count):
        if not self.enabled:
            return
        self.stage = f"Stage {step}/{step_count} — {message}"
        within_object = max(0.0, min(1.0, (step - 0.5) / step_count))
        completed = max(0, self.object_index - 1) + within_object
        self._set_percent(
            (completed / max(1, self.object_count)) * 100.0,
            force_redraw=True,
        )

    def complete_object(self):
        if not self.enabled:
            return
        self._set_percent(
            (self.object_index / max(1, self.object_count)) * 100.0
        )

    def finish(self, message, has_errors=False):
        if not self.enabled:
            return
        self.running = False
        self.stage = message
        self.percent = 100.0
        self.bar_color = (
            (0.95, 0.52, 0.18, 1.0)
            if has_errors
            else (0.2, 0.72, 0.38, 1.0)
        )
        self.linger_until = time.monotonic() + LINGER_SECONDS
        self._redraw()

    def _set_percent(self, percent, force_redraw=False):
        self.percent = max(0.0, min(100.0, percent))
        if force_redraw:
            _overlay.flush()
        else:
            self._redraw()

    def _redraw(self):
        _overlay.redraw()


def shutdown():
    """Remove handlers and timers when the add-on is disabled or reloaded."""
    global _active_feedback
    global _cleanup_timer_registered

    if (
        _cleanup_timer_registered
        and bpy.app.timers.is_registered(_cleanup_timer)
    ):
        bpy.app.timers.unregister(_cleanup_timer)
    _cleanup_timer_registered = False
    _overlay.hide()
    _active_feedback = None
