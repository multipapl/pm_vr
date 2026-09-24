"""PM VR semantic production pipeline package."""

import bpy
from bpy.app.handlers import persistent

from . import (
    bake,
    bake_scene,
    data,
    export,
    generated,
    identity,
    log,
    scene_debug,
    selection_sync,
    setup_ops,
    ui,
    viewport_overlay,
)


@persistent
def _load_post(_filepath):
    # Files saved by earlier versions may hold generated state materials
    # without a fake user; protect them before the next save drops them.
    generated.protect_all_generated_materials()
    restored = bake_scene.recover_interrupted_bake()
    if restored:
        log.warning(
            "Bake",
            f"Restored {restored} item(s) left by an interrupted bake "
            "(render visibility, generated collection, work data)",
        )
    identity.remember_identity_owners()


def _separate_copies(force):
    try:
        separated = identity.separate_copied_identities(force=force)
    except (AttributeError, ReferenceError, RuntimeError):
        return
    if separated:
        log.info("Setup", f"Gave {separated} copied object(s) their own pipeline identity")


@persistent
def _depsgraph_update_post(_scene, _depsgraph):
    _separate_copies(force=False)


@persistent
def _undo_redo_post(*_args):
    # Undo can bring back a copy that still carries the original's ID.
    _separate_copies(force=True)


_HANDLERS = (
    ("load_post", _load_post),
    ("depsgraph_update_post", _depsgraph_update_post),
    ("undo_post", _undo_redo_post),
    ("redo_post", _undo_redo_post),
)


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES):
        bpy.utils.register_class(cls)
    data.register_properties()
    selection_sync.register()
    viewport_overlay.register()
    scene_debug.register()
    for handler_name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, handler_name)
        if handler not in handlers:
            handlers.append(handler)
    try:
        identity.remember_identity_owners()
        generated.protect_all_generated_materials()
    except AttributeError:
        # bpy.data is restricted while add-ons register at startup; the
        # load_post handler covers the file that is opened next.
        pass


def unregister():
    for handler_name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, handler_name)
        if handler in handlers:
            handlers.remove(handler)
    bake.shutdown()
    scene_debug.unregister()
    viewport_overlay.unregister()
    selection_sync.unregister()
    data.unregister_properties()
    for cls in reversed((*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *bake.CLASSES, *export.CLASSES)):
        bpy.utils.unregister_class(cls)


def draw_stage(layout, context, stage):
    if stage == 'SETUP':
        ui.draw_setup(layout, context)
    elif stage == 'BAKE':
        ui.draw_bake(layout, context)
    elif stage == 'EXPORT':
        ui.draw_export(layout, context)


def draw_scene_debug(layout, context):
    scene_debug.draw_controls(layout, context, context.scene.pm_vr_project)


def draw_ui(layout, context):
    """Compatibility entry point used by the add-on UI smoke harness."""
    ui.draw_setup(layout, context)


def request_selection_sync():
    selection_sync.request_sync()
