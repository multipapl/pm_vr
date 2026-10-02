"""Exercise the PM VR preparation operators with Blender 5.2."""

from pathlib import Path
import sys

import bpy


ADDONS_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDONS_DIRECTORY))

import PM_VR  # noqa: E402


def _clear_objects():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def main():
    PM_VR.register()
    try:
        _clear_objects()
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        obj = bpy.context.object
        obj.name = "CompatObject"
        obj.data.name = "CompatObjectMesh"

        assert bpy.ops.pm_vr.sync_names_from_objects(scope='SELECTED') == {'FINISHED'}
        uv_count_before_check = len(obj.data.uv_layers)
        assert bpy.ops.pm_vr.check_uv_channels(scope='SELECTED') == {'FINISHED'}
        assert len(obj.data.uv_layers) == uv_count_before_check
        assert obj.select_get()
        assert bpy.ops.pm_vr.fix_uv_channels(scope='SELECTED') == {'FINISHED'}
        assert [layer.name for layer in obj.data.uv_layers[:2]] == ["UVMap", "SimpleBake"]
        saved_uv = tuple(obj.data.uv_layers[1].data[0].uv)
        obj.data.uv_layers[1].data[0].uv = (0.123, 0.456)
        assert bpy.ops.pm_vr.fix_uv_channels(scope='SELECTED') == {'FINISHED'}
        preserved_uv = tuple(obj.data.uv_layers[1].data[0].uv)
        assert abs(preserved_uv[0] - 0.123) < 1e-5
        assert abs(preserved_uv[1] - 0.456) < 1e-5
        assert preserved_uv != saved_uv or saved_uv == (0.123, 0.456)
        assert bpy.ops.pm_vr.check_uv_channels(scope='SELECTED') == {'FINISHED'}
        assert not obj.select_get()
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        assert bpy.ops.pm_vr.activate_simplebake() == {'FINISHED'}
        assert bpy.ops.pm_vr.activate_uvmap() == {'FINISHED'}
        assert bpy.ops.pm_vr.add_texture_suffix() == {'FINISHED'}
        assert bpy.ops.pm_vr.remove_texture_suffix() == {'FINISHED'}
        assert bpy.ops.pm_vr.audit_object_prep(scope='SELECTED') == {'FINISHED'}
        assert len(bpy.context.scene.pm_vr_audit_results) == 0

        obj.data.name = "WrongMeshName"
        obj.data.uv_layers[1].name = "WrongBakeUV"
        obj.select_set(True)
        assert bpy.ops.pm_vr.audit_object_prep(scope='SELECTED') == {'FINISHED'}
        assert len(bpy.context.scene.pm_vr_audit_results) == 1
        assert bpy.context.scene.pm_vr_audit_issue_count == 1
        assert bpy.context.scene.pm_vr_audit_results[0].object == obj
        assert obj.select_get()
        assert bpy.ops.pm_vr.sync_names_from_objects(scope='SELECTED') == {'FINISHED'}
        assert bpy.ops.pm_vr.fix_uv_channels(scope='SELECTED') == {'FINISHED'}
        assert bpy.ops.pm_vr.audit_recheck_active() == {'FINISHED'}
        assert len(bpy.context.scene.pm_vr_audit_results) == 0
        # Optimize must not rename a platform shader or reject valid Runtime
        # role names as old PascalCase/mesh-material name conventions.
        from PM_VR.modules import vr_project_tools
        shader = obj.data.materials[0]
        shader.name = 'RK_Water'
        obj.name = 'Water_Custom.001'
        assert bpy.ops.pm_vr.sync_names_from_objects(scope='SELECTED') == {'FINISHED'}
        assert obj.data.materials[0] == shader and shader.name == 'RK_Water'
        assert not vr_project_tools.naming_issue_keys(obj)
        shader.name = 'Ordinary material'
        layer = bpy.context.scene.pm_vr_project.render_layers.add()
        layer.layer_id, layer.layer_type = 'runtime-operator-test', 'RUNTIME'
        obj.pm_vr_pipeline.render_layer_id = layer.layer_id
        obj.name = 'Zone_Test'
        assert not vr_project_tools.naming_issue_keys(obj)
        print("PM_VR_OPERATOR_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
