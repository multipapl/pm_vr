"""Smoke-test PM VR registration and module UI wiring with Blender 5.2."""

from pathlib import Path
import sys

import bpy


ADDONS_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDONS_DIRECTORY))

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import scene_debug, viewport_overlay  # noqa: E402


def _enum_identifiers(function_name, parameter_name):
    parameter = bpy.types.UILayout.bl_rna.functions[function_name].parameters[parameter_name]
    return {item.identifier for item in parameter.enum_items}


VALID_ICONS = _enum_identifiers("label", "icon")


class _OperatorProxy:
    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


class _LayoutRecorder:
    def _check_icon(self, kwargs):
        icon = kwargs.get("icon", "NONE")
        assert icon in VALID_ICONS, f"Unknown Blender 5.2 UI icon: {icon}"

    def box(self):
        return self

    def panel(self, _identifier, **_kwargs):
        return self, self

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def separator(self, **_kwargs):
        return None

    def label(self, **kwargs):
        self._check_icon(kwargs)

    def prop(self, data, property_name, **kwargs):
        self._check_icon(kwargs)
        assert hasattr(data, property_name)

    def operator(self, operator_id, **kwargs):
        self._check_icon(kwargs)
        namespace, name = operator_id.split(".", 1)
        getattr(getattr(bpy.ops, namespace), name).get_rna_type()
        return _OperatorProxy()

    def operator_menu_enum(self, operator_id, _property_name, **kwargs):
        return self.operator(operator_id, **kwargs)

    def template_list(self, *args, **kwargs):
        self._check_icon(kwargs)


def main():
    PM_VR.register()
    try:
        layout = _LayoutRecorder()
        project = bpy.context.scene.pm_vr_project
        available = scene_debug._available_modes(project)
        assert 'UV_HEALTH' in available
        assert 'TEXEL_DENSITY' in available
        assert 'BAKE_STATUS' not in available
        assert scene_debug._set_mode(bpy.context, 'UV_HEALTH')
        assert not scene_debug._set_mode(bpy.context, 'BAKE_STATUS')
        for module in PM_VR.MODULES:
            draw_ui = getattr(module, "draw_ui", None)
            if draw_ui is not None:
                draw_ui(layout, bpy.context)
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object.name = "invalid_name.001"
        assert bpy.context.object in viewport_overlay._source_objects(
            bpy.context.scene,
            project,
        )
        assert bpy.ops.pm_vr.audit_object_prep(scope='SELECTED') == {'FINISHED'}
        PM_VR.vr_project_tools.draw_ui(layout, bpy.context)
        print("PM_VR_UI_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
