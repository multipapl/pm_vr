"""Small logging adapter shared by the baker services."""

from .constants import LOG_PREFIX


class BakeLogger:
    def __init__(self, operator=None, feedback=None):
        self.operator = operator
        self.feedback = feedback

    def _write(self, level, message):
        print(f"{LOG_PREFIX} {level}: {message}")
        self._notify("add_message", level, message)

    def _notify(self, method, *args):
        if not self.feedback:
            return
        try:
            getattr(self.feedback, method)(*args)
        except Exception as exc:
            print(
                f"{LOG_PREFIX} WARNING: live feedback disabled — {exc}"
            )
            self.feedback = None

    def set_candidate_count(self, count):
        self._notify("set_candidate_count", count)

    def begin_object(self, name, index, count):
        self._notify("begin_object", name, index, count)

    def stage(self, message, step, step_count=7):
        self._notify("set_stage", message, step, step_count)
        self._write("STATUS", message)

    def complete_object(self):
        self._notify("complete_object")

    def info(self, message):
        self._write("INFO", message)

    def warning(self, message, report=False):
        self._write("WARNING", message)
        if report and self.operator:
            self.operator.report({'WARNING'}, message)

    def error(self, message, report=False):
        self._write("ERROR", message)
        if report and self.operator:
            self.operator.report({'ERROR'}, message)
