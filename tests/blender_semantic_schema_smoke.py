"""Check unified layer defaults and Runtime camera export."""

from pathlib import Path
import sys
import tempfile

import bpy


ADDONS_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ADDONS_DIRECTORY))

import PM_VR  # noqa: E402
from PM_VR.modules import collection_export  # noqa: E402


def main():
    PM_VR.register()
    try:
        project = bpy.context.scene.pm_vr_project
        assert bpy.ops.pmvr.initialize_project() == {'FINISHED'}
        expected = {
            "Unlit": "UNLIT",
            "PBR": "PBR",
            "Alpha": "ALPHA",
            "Translucent": "TRANSLUCENT",
            "Glass": "GLASS",
            "Emissive": "EMISSIVE",
            "Runtime": "RUNTIME",
        }
        assert project.schema_version == 1
        assert {
            layer.display_name: layer.layer_type
            for layer in project.render_layers
        } == expected

        runtime = next(layer for layer in project.render_layers if layer.layer_type == 'RUNTIME')
        project.active_render_layer_index = list(project.render_layers).index(runtime)
        camera_data = bpy.data.cameras.new("Probe Camera Data")
        camera = bpy.data.objects.new("Probe Camera", camera_data)
        bpy.context.scene.collection.objects.link(camera)
        bpy.ops.object.select_all(action='DESELECT')
        camera.select_set(True)
        bpy.context.view_layer.objects.active = camera
        assert bpy.ops.pmvr.assign_selected_to_layer(role='BAKE') == {'FINISHED'}
        assert camera.pm_vr_pipeline.processing_role == 'EXPORT_ORIGINAL'

        collection = bpy.data.collections.new("Runtime Export")
        bpy.context.scene.collection.children.link(collection)
        collection.objects.link(camera)
        with tempfile.TemporaryDirectory(prefix="pmvr_runtime_camera_") as directory:
            path = Path(directory) / "Runtime.usda"
            result = collection_export.export_usdz(collection, str(path))
            assert result == {'FINISHED'}
            assert 'def Camera' in path.read_text(encoding='utf-8')

        print("PM_VR_LAYER_SCHEMA_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
