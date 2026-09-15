"""Ownership, collision checks, and transactional generated assets."""

from dataclasses import dataclass
import os
import uuid

import bpy

from .constants import (
    ASSET_IMAGE,
    ASSET_MATERIAL,
    ASSET_MESH,
    ASSET_OBJECT,
    TAG_EXPORT_PATH,
)
from .material import add_lightmap_nodes
from .ownership import (
    collect_owned_assets,
    get_output_collection,
    result_name,
    tag_asset,
    validate_name_collisions,
)
from .state import (
    ObjectVisibilityState,
    set_hidden,
    set_result_visible,
    set_visible_for_bake,
)


@dataclass
class GeneratedBundle:
    source: object
    obj: object
    mesh: object
    material: object
    image: object
    operation_id: str
    material_connected: bool

    def cleanup(self):
        try:
            if self.obj and bpy.data.objects.get(self.obj.name) is self.obj:
                bpy.data.objects.remove(self.obj, do_unlink=True)
        except ReferenceError:
            pass
        self.obj = None

        try:
            if self.mesh and self.mesh.users == 0:
                bpy.data.meshes.remove(self.mesh)
        except ReferenceError:
            pass
        self.mesh = None

        try:
            if self.material and self.material.users == 0:
                bpy.data.materials.remove(self.material)
        except ReferenceError:
            pass
        self.material = None

        try:
            if self.image and self.image.users == 0:
                bpy.data.images.remove(self.image)
        except ReferenceError:
            pass
        self.image = None


def create_generated_bundle(context, source, image, operation_id):
    temporary_name = f"__PM_LM_NEW_{uuid.uuid4().hex}"
    source_material = source.material_slots[0].material
    source_used_nodes = bool(source_material.use_nodes)

    mesh = None
    material = None
    obj = None
    bundle = None
    try:
        mesh = source.data.copy()
        mesh.name = f"{temporary_name}_Mesh"
        material = source_material.copy()
        material.name = f"{temporary_name}_Material"

        if len(mesh.materials):
            mesh.materials[0] = material
        else:
            mesh.materials.append(material)

        obj = source.copy()
        obj.data = mesh
        obj.name = temporary_name
        collection = get_output_collection(context.scene)
        collection.objects.link(obj)
        obj.material_slots[0].material = material
        set_hidden(context, obj)

        bundle = GeneratedBundle(
            source=source,
            obj=obj,
            mesh=mesh,
            material=material,
            image=image,
            operation_id=operation_id,
            material_connected=False,
        )
        bundle.material_connected = add_lightmap_nodes(
            material,
            image,
            source_used_nodes=source_used_nodes,
        )

        tag_asset(obj, source, ASSET_OBJECT, operation_id)
        tag_asset(mesh, source, ASSET_MESH, operation_id)
        tag_asset(material, source, ASSET_MATERIAL, operation_id)
        tag_asset(image, source, ASSET_IMAGE, operation_id)
        return bundle
    except Exception:
        if bundle:
            bundle.cleanup()
        else:
            if obj and bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            if material and material.users == 0:
                bpy.data.materials.remove(material)
        raise


def _remove_owned_assets(assets, logger):
    for obj in list(assets.objects):
        try:
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            pass

    for mesh in list(assets.meshes):
        try:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
                continue
            logger.warning(
                f'Kept old generated mesh "{mesh.name}" because it still has users'
            )
        except ReferenceError:
            pass

    for material in list(assets.materials):
        try:
            if material.users == 0:
                bpy.data.materials.remove(material)
                continue
            logger.warning(
                f'Kept old generated material "{material.name}" because it still has users'
            )
        except ReferenceError:
            pass

    for image in list(assets.images):
        try:
            if image.users == 0:
                bpy.data.images.remove(image)
                continue
            logger.warning(
                f'Kept old generated image "{image.name}" because it still has users'
            )
        except ReferenceError:
            pass


class AssetTransaction:
    def __init__(self, context, source, logger):
        self.context = context
        self.source = source
        self.logger = logger
        self.old_assets = collect_owned_assets(source)
        self.source_visibility = ObjectVisibilityState(context, source)
        self.old_visibility = [
            ObjectVisibilityState(context, obj)
            for obj in self.old_assets.objects
        ]
        self.bundle = None
        self._committed = False
        self._old_names = []
        self._old_export_paths = [
            image.get(TAG_EXPORT_PATH, "")
            for image in self.old_assets.images
            if image.get(TAG_EXPORT_PATH, "")
        ]

    @property
    def committed(self):
        return self._committed

    def prepare_scene(self):
        set_visible_for_bake(self.context, self.source)
        for obj in self.old_assets.objects:
            set_hidden(self.context, obj)

    def restore_previous_visibility(self):
        self.source_visibility.restore()
        for state in self.old_visibility:
            state.restore()

    def _reserve_final_names(self):
        marker = uuid.uuid4().hex
        for index, id_block in enumerate(self.old_assets.all_id_blocks()):
            self._old_names.append((id_block, id_block.name))
            id_block.name = f"__PM_LM_OLD_{marker}_{index}"

    def _restore_old_names(self):
        for id_block, name in self._old_names:
            try:
                id_block.name = name
            except ReferenceError:
                pass

    def commit(self, bundle, staged_export=None):
        validate_name_collisions(self.source)
        final_name = result_name(self.source)
        self.bundle = bundle
        file_committed = False

        try:
            self._reserve_final_names()
            bundle.obj.name = final_name
            bundle.mesh.name = final_name
            bundle.material.name = final_name
            bundle.image.name = final_name

            if any(
                id_block.name != final_name
                for id_block in (
                    bundle.obj,
                    bundle.mesh,
                    bundle.material,
                    bundle.image,
                )
            ):
                raise RuntimeError("could not reserve final _LM datablock names")

            if staged_export:
                staged_export.commit()
                file_committed = True
                staged_export.assign_to_image(bundle.image)
                bundle.image[TAG_EXPORT_PATH] = staged_export.final_path
        except Exception:
            bundle.cleanup()
            if file_committed and staged_export:
                staged_export.rollback()
            self._restore_old_names()
            raise

        self._committed = True
        _remove_owned_assets(self.old_assets, self.logger)
        if staged_export:
            staged_export.finalize()
        self._remove_superseded_export(staged_export)

    def _remove_superseded_export(self, staged_export):
        final_path = (
            os.path.normcase(os.path.abspath(staged_export.final_path))
            if staged_export
            else ""
        )
        for old_path in self._old_export_paths:
            normalized = os.path.normcase(os.path.abspath(old_path))
            if normalized == final_path:
                continue
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except OSError as exc:
                self.logger.warning(
                    f'Could not remove superseded EXR "{old_path}": {exc}'
                )

    def finalize_visibility(self):
        if not self._committed or not self.bundle:
            return
        set_hidden(self.context, self.source)
        set_result_visible(self.context, self.bundle.obj)
