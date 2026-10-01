"""Smoke-test layer-scoped units and batched resolution in Blender."""

import os
import sys

import bpy


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline.identity import new_id  # noqa: E402
from PM_VR.modules.pipeline.setup_ops import active_unit  # noqa: E402


def add_layer(project, name):
    layer = project.render_layers.add()
    layer.layer_id = new_id()
    layer.display_name = name
    return layer


def add_mesh(name, x):
    bpy.ops.mesh.primitive_plane_add(size=1, location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    return obj


def select(*objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def main():
    PM_VR.register()
    project = bpy.context.scene.pm_vr_project
    project.initialized = True
    project.project_id = new_id()
    layer_a = add_layer(project, "Scene")
    layer_b = add_layer(project, "PBR")
    project.active_render_layer_index = 0

    one = add_mesh("One", 0)
    two = add_mesh("Two", 2)
    select(one, two)
    result = bpy.ops.pmvr.add_bake_unit(merge_selected=False)
    assert result == {'FINISHED'}
    assert len(project.bake_units) == 2
    assert one.pm_vr_pipeline.bake_unit_id != two.pm_vr_pipeline.bake_unit_id
    assert all(unit.render_layer_id == layer_a.layer_id for unit in project.bake_units)

    assert bpy.ops.pmvr.select_all_units_for_resolution() == {'FINISHED'}
    assert all(unit.batch_selected for unit in project.bake_units)
    project.bake_units[0].resolution = '2048'
    assert project.bake_units[1].resolution == '2048'

    project.active_render_layer_index = 1
    assert active_unit(project) is None
    three = add_mesh("Three", 4)
    four = add_mesh("Four", 6)
    select(three, four)
    result = bpy.ops.pmvr.add_bake_unit(merge_selected=True)
    assert result == {'FINISHED'}
    assert len(project.bake_units) == 3
    unit = active_unit(project)
    assert unit and unit.render_layer_id == layer_b.layer_id
    assert three.pm_vr_pipeline.bake_unit_id == four.pm_vr_pipeline.bake_unit_id == unit.unit_id
    assert project.bake_day and not project.bake_evening
    print("PM_VR_PIPELINE_SETUP_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
