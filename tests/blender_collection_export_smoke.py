"""Smoke-test collection batch export to USDZ and GLB."""

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
        items = bpy.context.scene.pm_vr_export_collections
        bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection
        assert bpy.ops.pm_vr.add_active_export_collection() == {'CANCELLED'}
        added, reason = collection_export.add_collection_to_export_list(
            items,
            bpy.context.scene.collection,
        )
        assert not added
        assert "Scene Collection" in reason
        assert len(items) == 0

        collection = bpy.data.collections.new("Source Collection")
        bpy.context.scene.collection.children.link(collection)
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        obj = bpy.context.object
        obj.name = "Export Object"
        obj.data.name = "Export Mesh"
        for source_collection in list(obj.users_collection):
            source_collection.objects.unlink(obj)
        collection.objects.link(obj)

        added, reason = collection_export.add_collection_to_export_list(items, collection)
        assert added, reason
        item = items[-1]
        item.export_name = "Permanent Custom Name"
        item.export_usdz = True
        item.export_glb = True

        with tempfile.TemporaryDirectory(prefix="pm_vr_usdz_") as usdz_directory:
            with tempfile.TemporaryDirectory(prefix="pm_vr_glb_") as glb_directory:
                bpy.context.scene.pm_vr_usdz_export_directory = usdz_directory
                bpy.context.scene.pm_vr_glb_export_directory = glb_directory
                assert bpy.ops.pm_vr.export_collections(export_format='BOTH') == {'FINISHED'}
                assert (Path(usdz_directory) / "Permanent Custom Name.usdz").is_file()
                assert (Path(glb_directory) / "Permanent Custom Name.glb").is_file()
                assert item.last_usdz_status == "Exported"
                assert item.last_glb_status == "Exported"

        print("PM_VR_COLLECTION_EXPORT_SMOKE_OK")
    finally:
        PM_VR.unregister()


if __name__ == "__main__":
    main()
