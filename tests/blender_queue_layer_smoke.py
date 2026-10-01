"""Setup's Queue N Units puts a whole baked layer into the bake queue.

Run with Blender --background --factory-startup --python this_file.py.

LO has units A, B (already queued) and C (no objects); TR has D; Glass is
not baked. Queueing LO adds A only, after B, and leaves D alone; the Glass
layer has no such button (poll fails); a second press adds nothing.
"""

import os
import sys

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402


PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)
        print(f"[PM VR] FAIL: {message}")


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    project = bpy.context.scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    layers = {}
    for name, kind in (("LO", 'UNLIT'), ("TR", 'ALPHA'), ("Glass", 'GLASS')):
        layer = project.render_layers.add()
        layer.layer_id, layer.display_name, layer.layer_type = new_id(), name, kind
        layers[name] = layer.layer_id
    units = {}
    for name, layer_name, has_member in (("A", "LO", True), ("B", "LO", True), ("C", "LO", False), ("D", "TR", True)):
        unit = project.bake_units.add()
        unit.unit_id = unit.artifact_key = new_id()
        unit.display_name, unit.render_layer_id = name, layers[layer_name]
        units[name] = unit.unit_id
        if has_member:
            obj = bpy.data.objects.new(f"{name}Obj", bpy.data.meshes.new(f"{name}Mesh"))
            bpy.context.scene.collection.objects.link(obj)
            meta = obj.pm_vr_pipeline
            meta.source_id = new_id()
            meta.is_registered_source = True
            meta.render_layer_id = layers[layer_name]
            meta.processing_role = 'BAKE'
            meta.bake_unit_id = unit.unit_id
    project.bake_queue.add().unit_id = units["B"]
    names = {unit_id: name for name, unit_id in units.items()}

    project.active_render_layer_index = 0
    check(bpy.ops.pmvr.queue_layer_units() == {'FINISHED'}, "LO not queued")
    queue = [names[entry.unit_id] for entry in project.bake_queue]
    check(queue == ["B", "A"], f"queue {queue}, expected B, A")
    try:
        again = bpy.ops.pmvr.queue_layer_units()
    except RuntimeError:
        again = {'CANCELLED'}
    check(again == {'CANCELLED'} and len(project.bake_queue) == 2, "second press changed the queue")

    project.active_render_layer_index = 1
    bpy.ops.pmvr.queue_layer_units()
    queue = [names[entry.unit_id] for entry in project.bake_queue]
    check(queue == ["B", "A", "D"], f"queue {queue}, expected B, A, D")

    project.active_render_layer_index = 2
    check(not bpy.ops.pmvr.queue_layer_units.poll(), "Glass layer can be queued")

    # A unit baked for Day in this queue (waiting for Evening) is queued again
    # by adding it: its marks are cleared, nothing is duplicated.
    project.bake_queue[1].day_done = True
    project.active_render_layer_index = 0
    check(bpy.ops.pmvr.queue_layer_units() == {'FINISHED'}, "re-queue of a baked unit refused")
    marks = [(names[e.unit_id], e.day_done, e.evening_done) for e in project.bake_queue]
    check(marks == [("B", False, False), ("A", False, False), ("D", False, False)], f"after re-queue {marks}")
    bpy.context.view_layer.objects.active = None
    for obj in bpy.context.scene.objects:
        obj.select_set(obj.name == "DObj")
    project.bake_queue[2].evening_done = True
    check(bpy.ops.pmvr.queue_selected_units() == {'FINISHED'}, "Add Selected Units did not re-queue")
    check(not project.bake_queue[2].evening_done and len(project.bake_queue) == 3, "selected re-queue")

    print("PM_VR_QUEUE_LAYER_SMOKE_FAILED" if PROBLEMS else "PM_VR_QUEUE_LAYER_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
