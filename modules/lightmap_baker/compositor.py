"""Temporary scene-compositor denoise with albedo and normal guides."""

import os

import bpy

from .compositor_io import (
    copy_pixels,
    load_compositor_exr,
    temporary_compositor_inputs,
)
from .images import replace_file


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


COMPOSITOR_SCENE = "__PMVR_COMPOSITOR"
COMPOSITOR_TAG = "pmvr_compositor_scene"


def _compositor_scene(active_scene, width, height):
    """The scene every denoise renders: made once and reused. Blender keeps
    a rendered scene's buffers until it quits, even after the scene is
    removed; a new scene per denoise leaked ~0.6 GB each at 4K, and a night
    of baking ran Blender out of memory (135 GB). Returns (scene, tree,
    group) with the tree emptied; group: the tree is a node group (Blender
    5), whose output is a Group Output node."""
    scene = next((item for item in bpy.data.scenes if item.get(COMPOSITOR_TAG)), None)
    if scene is None:
        scene = bpy.data.scenes.new(COMPOSITOR_SCENE)
        scene[COMPOSITOR_TAG] = True
        for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH'):
            try:
                scene.render.engine = engine
                break
            except (TypeError, ValueError):
                continue
    if scene.camera is None:
        _create_camera(scene, "compositor")
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    _copy_compositor_settings(active_scene, scene)
    tree, _created = _compositor_tree(scene)
    tree.nodes.clear()
    group = getattr(scene, "compositing_node_group", None) is tree
    return scene, tree, group


def release_compositor_scene():
    """Remove the reused compositor scene (end of a bake queue, file load)."""
    for scene in [item for item in bpy.data.scenes if item.get(COMPOSITOR_TAG)]:
        camera = scene.camera
        tree = getattr(scene, "compositing_node_group", None)
        bpy.data.scenes.remove(scene)
        if camera is not None and camera.users == 0:
            data = camera.data
            bpy.data.objects.remove(camera)
            if data is not None and data.users == 0:
                bpy.data.cameras.remove(data)
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)


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


def denoise_image(active_scene, image, albedo_guide=None, normal_guide=None):
    scene, tree, owns_tree = _compositor_scene(active_scene, image.size[0], image.size[1])
    try:
        scene.render.film_transparent = True
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
            guided = albedo_guide is not None and normal_guide is not None
            if guided:
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

            tree.links.new(noisy.outputs["Image"], denoise.inputs["Image"])
            if guided:
                normal_decode = _normal_decode_node(tree.nodes)
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
        tree.nodes.clear()


def denoise_external_beauty(active_scene, image, filepath):
    """Denoise an already color-managed Beauty PNG like SimpleBake."""
    input_image = None
    staging = f"{filepath}.pmvr_denoise_tmp.png"
    scene, tree, owns_tree = _compositor_scene(active_scene, image.size[0], image.size[1])
    try:
        scene.render.film_transparent = False
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGB'
        scene.render.image_settings.color_depth = '8'
        scene.render.use_compositing = True
        scene.render.use_sequencer = False
        scene.view_settings.view_transform = 'Standard'
        try:
            scene.view_settings.look = 'None'
        except (TypeError, ValueError):
            pass
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        try:
            scene.display_settings.display_device = (
                active_scene.display_settings.display_device
            )
        except (AttributeError, TypeError, ValueError):
            pass
        input_image = bpy.data.images.load(filepath, check_existing=False)
        noisy = _image_node(
            tree.nodes,
            input_image,
            "Color-managed Beauty",
            (-420.0, 80.0),
        )
        denoise = tree.nodes.new("CompositorNodeDenoise")
        denoise.location = (-120.0, 80.0)
        denoise.label = "PMVR Beauty Denoise"
        if hasattr(denoise, "use_hdr"):
            denoise.use_hdr = True
        if hasattr(denoise, "prefilter"):
            try:
                denoise.prefilter = 'NONE'
            except (TypeError, ValueError):
                pass
        tree.links.new(noisy.outputs["Image"], denoise.inputs["Image"])
        output = (
            tree.nodes.new("NodeGroupOutput")
            if owns_tree
            else tree.nodes.new("CompositorNodeComposite")
        )
        output.location = (160.0, 80.0)
        tree.links.new(denoise.outputs["Image"], output.inputs["Image"])

        result = bpy.ops.render.render(
            scene=scene.name,
            use_viewport=False,
            write_still=False,
        )
        if 'FINISHED' not in result:
            raise RuntimeError("Beauty compositor denoise was cancelled")
        render_result = bpy.data.images.get("Render Result")
        if not render_result:
            raise RuntimeError("Beauty compositor produced no Render Result")
        render_result.save_render(staging, scene=scene)
        replace_file(staging, filepath)
        image.reload()
    finally:
        tree.nodes.clear()
        if os.path.exists(staging):
            os.remove(staging)
        if input_image and bpy.data.images.get(input_image.name) is input_image:
            bpy.data.images.remove(input_image)
