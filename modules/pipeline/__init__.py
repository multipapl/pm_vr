"""PM VR semantic production pipeline package."""

import bpy
from bpy.app.handlers import persistent

from . import (
    authoring,
    bake,
    bake_scene,
    data,
    export,
    folder_organization,
    generated,
    identity,
    log,
    looks,
    probes,
    preview,
    scenarios,
    scene_debug,
    selection_sync,
    setup_ops,
    ui,
    variants,
    viewport_overlay,
    working_directory,
)


@persistent
def _load_post(_filepath):
    working_directory.migrate_all()
    for scene in bpy.data.scenes:
        if scene.pm_vr_project.initialized:
            looks.ensure(scene.pm_vr_project)
            probes.migrate(scene.pm_vr_project)
    # Files saved by earlier versions may hold generated state materials
    # without a fake user; protect them before the next save drops them.
    generated.protect_all_generated_materials()
    _settle_generated_uvs()
    # A file saved during a bake (Save During Bake) has the running flag
    # stored; nothing runs in a file that was just opened.
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project is not None and project.operation_running:
            project.operation_running = False
    restored = bake_scene.recover_interrupted_bake()
    restored += scenarios.recover_interrupted_scenarios()
    if restored:
        log.warning(
            "Bake",
            f"Restored {restored} item(s) left by an interrupted bake "
            "(render visibility, generated collection, work data, scenario collections)",
        )
    identity.remember_identity_owners()
    setup_ops.release_roleless_members()
    setup_ops.cap_removed_resolutions()
    setup_ops.reset_test_resolution()
    scenarios.prune_scenarios()
    scenarios.sync_scenarios(force=True)
    for scene in bpy.data.scenes:
        if scene.pm_vr_project.initialized:
            preview.migrate(scene.pm_vr_project)


@persistent
def _save_post(_filepath):
    for scene in bpy.data.scenes:
        project = getattr(scene, 'pm_vr_project', None)
        if project is not None:
            working_directory.ensure_new_folders(project)


def _settle_generated_uvs():
    generated.settle_baked_textures()
    switched, pinned = generated.settle_generated_uvs()
    if switched or pinned:
        log.info(
            "Setup",
            f"UV maps: SimpleBake made active and render UV on {switched} generated object(s), "
            f"{pinned} normal map node(s) pinned to UVMap",
        )


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
    try:
        scenarios.sync_scenarios()
    except (AttributeError, ReferenceError, RuntimeError):
        pass


@persistent
def _undo_redo_post(*_args):
    # Undo can bring back a copy that still carries the original's ID.
    _separate_copies(force=True)


_HANDLERS = (
    ("load_post", _load_post),
    ("save_post", _save_post),
    ("depsgraph_update_post", _depsgraph_update_post),
    ("undo_post", _undo_redo_post),
    ("redo_post", _undo_redo_post),
)


def register():
    for cls in (*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *looks.CLASSES, *scenarios.CLASSES, *bake.CLASSES, *export.CLASSES, *variants.CLASSES, *probes.CLASSES, *authoring.CLASSES, *folder_organization.CLASSES):
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
        working_directory.migrate_all()
        for scene in bpy.data.scenes:
            if scene.pm_vr_project.initialized:
                looks.ensure(scene.pm_vr_project)
                probes.migrate(scene.pm_vr_project)
        identity.remember_identity_owners()
        generated.protect_all_generated_materials()
        _settle_generated_uvs()
        setup_ops.release_roleless_members()
        setup_ops.cap_removed_resolutions()
        scenarios.prune_scenarios()
        scenarios.sync_scenarios(force=True)
        for scene in bpy.data.scenes:
            if scene.pm_vr_project.initialized:
                preview.migrate(scene.pm_vr_project)
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
    probes.shutdown()
    scene_debug.unregister()
    viewport_overlay.unregister()
    selection_sync.unregister()
    data.unregister_properties()
    for cls in reversed((*data.CLASSES, *ui.CLASSES, *setup_ops.CLASSES, *looks.CLASSES, *scenarios.CLASSES, *bake.CLASSES, *export.CLASSES, *variants.CLASSES, *probes.CLASSES, *authoring.CLASSES, *folder_organization.CLASSES)):
        bpy.utils.unregister_class(cls)


def draw_stage(layout, context, stage):
    if stage == 'SETUP':
        ui.draw_setup(layout, context)
        authoring.draw(layout, context)
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
