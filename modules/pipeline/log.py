"""Concise console and in-blend diagnostics for production pipeline runs."""

from datetime import datetime

import bpy


LOG_TEXT_NAME = "PMVR Pipeline Log"
MAX_LINES = 500


def write(category, message, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] [{level}] [{category}] {message}"
    print(f"[PM VR] {line}")
    try:
        text = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(LOG_TEXT_NAME)
        existing = text.as_string().splitlines()
        existing.append(line)
        if len(existing) > MAX_LINES:
            existing = existing[-MAX_LINES:]
        text.clear()
        text.write("\n".join(existing) + "\n")
    except Exception as exc:
        print(f"[PM VR][Log] Could not update Text datablock: {exc}")


def info(category, message):
    write(category, message, "INFO")


def warning(category, message):
    write(category, message, "WARNING")


def error(category, message):
    write(category, message, "ERROR")
