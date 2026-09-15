"""Temporary scene-compositor denoise with albedo and normal guides."""

import os
import uuid

import bpy

from .compositor_io import (
    copy_pixels,
    load_compositor_exr,
    temporary_compositor_inputs,
)


def _compositor_tree(scene):
    try:
        scene.use_nodes = True
    except AttributeError:
        pass
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None and hasattr(scene, "node_tree"):
        tree = scene.node_tree
    created = False
    if tree is None and hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(
            name=f"{scene.name}_Compositor",
            type='CompositorNodeTree',
        )
        tree.interface.new_socket(
            name="Image",
            in_out='OUTPUT',
            socket_type='NodeSocketColor',
        )
        scene.compositing_node_group = tree
        created = True
    if tree is None:
        raise RuntimeError("scene compositor node tree is unavailable")
    return tree, created


def _copy_compositor_settings(source_scene, target_scene):
    target_scene.render.use_compositing = True
    target_scene.render.use_sequencer = False
    for owner_name, attribute in (
        ("render", "compositor_device"),
        ("render", "compositor_precision"),
        ("render", "compositor_denoise_device"),
        ("render", "compositor_denoise_preview_quality"),
        ("render", "compositor_denoise_final_quality"),
    ):
        source_owner = getattr(source_scene, owner_name, None)
        target_owner = getattr(target_scene, owner_name, None)
        if (
            source_owner
            and target_owner
            and hasattr(source_owner, attribute)
            and hasattr(target_owner, attribute)
        ):
            try:
                setattr(
                    target_owner,
                    attribute,
                    getattr(source_owner, attribute),
                )
            except (TypeError, ValueError):
                pass


def _create_camera(scene, token):
    camera_data = bpy.data.cameras.new(f"__PM_LM_CAMERA_{token}")
    camera = bpy.data.objects.new(f"__PM_LM_CAMERA_{token}", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = (0.0, 0.0, 10.0)
    return camera, camera_data


def _image_node(nodes, image, label, location):
    node = nodes.new("CompositorNodeImage")
    node.image = image
    node.label = label
    node.location = location
    return node


def _normal_decode_node(nodes):
    last_error = None
    for node_type in ("ShaderNodeVectorMath", "CompositorNodeVecMath"):
        try:
            node = nodes.new(node_type)
            break
        except RuntimeError as exc:
            last_error = exc
    else:
        raise RuntimeError("normal guide decode node is unavailable") from last_error

    node.operation = 'MULTIPLY_ADD'
    node.label = "Decode Object-Space Normal"
    node.location = (-420.0, -220.0)
    node.inputs[1].default_value = (2.0, 2.0, 2.0)
    node.inputs[2].default_value = (-1.0, -1.0, -1.0)
    return node


def denoise_image(active_scene, image, albedo_guide, normal_guide):
    token = uuid.uuid4().hex
    scene = bpy.data.scenes.new(f"__PM_LM_DENOISE_{token}")
    camera = None
    camera_data = None
    tree = None
    owns_tree = False

    try:
        for engine in (
            'BLENDER_EEVEE_NEXT',
            'BLENDER_EEVEE',
            'BLENDER_WORKBENCH',
        ):
            try:
                scene.render.engine = engine
                break
            except (TypeError, ValueError):
                continue
        scene.render.resolution_x = image.size[0]
        scene.render.resolution_y = image.size[1]
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = True
        _copy_compositor_settings(active_scene, scene)
        camera, camera_data = _create_camera(scene, token)

        tree, owns_tree = _compositor_tree(scene)
        tree.nodes.clear()
        with temporary_compositor_inputs(
            image,
            albedo_guide,
            normal_guide,
        ) as (folder, input_images):
            noisy = _image_node(
                tree.nodes,
                input_images[0],
                "Baked Lightmap",
                (-620.0, 140.0),
            )
            albedo = _image_node(
                tree.nodes,
                input_images[1],
                "White Receiver Albedo Guide",
                (-620.0, -40.0),
            )
            normal = _image_node(
                tree.nodes,
                input_images[2],
                "Geometry Normal Guide",
                (-620.0, -220.0),
            )
            denoise = tree.nodes.new("CompositorNodeDenoise")
            denoise.location = (-220.0, 100.0)
            denoise.label = "PM Lightmap Denoise"
            if hasattr(denoise, "use_hdr"):
                denoise.use_hdr = True
            elif denoise.inputs.get("HDR"):
                denoise.inputs["HDR"].default_value = True
            if hasattr(denoise, "prefilter"):
                try:
                    denoise.prefilter = 'NONE'
                except (TypeError, ValueError):
                    pass
            elif denoise.inputs.get("Prefilter"):
                for value in ('NONE', 'None'):
                    try:
                        denoise.inputs["Prefilter"].default_value = value
                        break
                    except (TypeError, ValueError):
                        continue

            normal_decode = _normal_decode_node(tree.nodes)
            tree.links.new(noisy.outputs["Image"], denoise.inputs["Image"])
            tree.links.new(
                normal.outputs["Image"],
                normal_decode.inputs[0],
            )
            tree.links.new(
                normal_decode.outputs["Vector"],
                denoise.inputs["Normal"],
            )
            tree.links.new(albedo.outputs["Image"], denoise.inputs["Albedo"])

            if owns_tree:
                preview_output = tree.nodes.new("NodeGroupOutput")
            else:
                preview_output = tree.nodes.new("CompositorNodeComposite")
            preview_output.location = (140.0, 180.0)
            tree.links.new(
                denoise.outputs["Image"],
                preview_output.inputs["Image"],
            )

            file_output = tree.nodes.new("CompositorNodeOutputFile")
            file_output.location = (140.0, -20.0)
            if hasattr(file_output, "base_path"):
                file_output.base_path = folder
                file_output.file_slots[0].path = "denoised_"
                file_input = file_output.inputs["Image"]
                output_format = file_output.format
            else:
                file_output.directory = folder
                file_output.file_name = "denoised_"
                file_output.use_file_extension = True
                file_output.file_output_items.clear()
                item = file_output.file_output_items.new('RGBA', "Image")
                item.override_node_format = True
                item.save_as_render = False
                file_input = file_output.inputs["Image"]
                output_format = item.format
            output_format.file_format = 'OPEN_EXR'
            output_format.color_mode = 'RGBA'
            output_format.color_depth = '32'
            output_format.exr_codec = 'PIZ'
            if hasattr(file_output, "save_as_render"):
                file_output.save_as_render = False
            tree.links.new(
                denoise.outputs["Image"],
                file_input,
            )

            result = bpy.ops.render.render(
                scene=scene.name,
                use_viewport=False,
                write_still=False,
            )
            if 'FINISHED' not in result:
                raise RuntimeError("compositor render was cancelled")

            output_paths = [
                os.path.join(folder, filename)
                for filename in os.listdir(folder)
                if (
                    filename.lower().endswith(".exr")
                    and filename.startswith("denoised_")
                )
            ]
            if len(output_paths) != 1:
                raise RuntimeError(
                    "compositor did not produce one denoised EXR"
                )

            rendered = load_compositor_exr(output_paths[0], folder)
            try:
                copy_pixels(rendered, image)
            finally:
                bpy.data.images.remove(rendered)
    finally:
        if scene:
            scene.camera = None
        if camera and bpy.data.objects.get(camera.name) is camera:
            bpy.data.objects.remove(camera, do_unlink=True)
        if camera_data and camera_data.users == 0:
            bpy.data.cameras.remove(camera_data)
        if scene and bpy.data.scenes.get(scene.name) is scene:
            bpy.data.scenes.remove(scene)
        if (
            owns_tree
            and tree
            and tree.users == 0
            and bpy.data.node_groups.get(tree.name) is tree
        ):
            bpy.data.node_groups.remove(tree)
