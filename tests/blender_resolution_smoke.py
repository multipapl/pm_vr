"""Bake Resolution, unit resolution at export, and the test resolution.

Run with Blender --background --factory-startup --python this_file.py.

- 8192 is no longer offered; a file saved while it existed (enum value 5)
  reads as 4096 after load (units, default unit resolution, legacy baker).
- A mesh needing more than 4096 at the target texel density is suggested 4096.
- Every unit bakes at the Bake Resolution (its own when larger) and the PNG
  on disk keeps that size; Blender shows it. Export writes each atlas at its
  unit's resolution into the USDZ/GLB, averaged in linear light: the mean
  colour stays as baked, the baked file and the Blender image are unchanged.
- A unit's resolution can go down or up to the baked size without a rebake;
  above it the status and export say so.
- Test resolution 75/50/25% is a share of the Bake Resolution; a reopened
  file is back at 100%.
- The atlas scaler keeps colour for any ratio (3072 to 2048 as well).
"""

import hashlib
import json
import os
import struct
import sys
import tempfile
import zipfile

import bpy
import numpy as np


ADDONS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ADDONS_ROOT not in sys.path:
    sys.path.insert(0, ADDONS_ROOT)

import PM_VR  # noqa: E402
from PM_VR.modules.pipeline import bake, bake_files, ui  # noqa: E402
from PM_VR.modules.pipeline.setup_ops import bake_size  # noqa: E402
from PM_VR.modules.pipeline.state import activate_state  # noqa: E402

PROBLEMS = []


def check(condition, message):
    if not condition:
        PROBLEMS.append(message)


def add_plane(root, name, size):
    bpy.ops.mesh.primitive_plane_add(size=size)
    plane = bpy.context.object
    plane.name = name
    for owner in list(plane.users_collection):
        owner.objects.unlink(plane)
    root.objects.link(plane)
    plane.data.uv_layers[0].name = "UVMap"
    bake_uv = plane.data.uv_layers.new(name="SimpleBake", do_init=True)
    for loop in bake_uv.data:
        loop.uv = (loop.uv[0] * 0.9 + 0.05, loop.uv[1] * 0.9 + 0.05)
    bpy.ops.object.select_all(action='DESELECT')
    plane.select_set(True)
    bpy.context.view_layer.objects.active = plane
    return plane


def build():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    project = scene.pm_vr_project
    output = tempfile.mkdtemp(prefix="pmvr_resolution_")
    for attribute in ("beauty_output_directory", "usdz_output_directory", "glb_output_directory"):
        setattr(project, attribute, output + os.sep)
    root = bpy.data.collections.new("ResRoot")
    scene.collection.children.link(root)
    day, evening = bpy.data.collections.new("ResDay"), bpy.data.collections.new("ResEvening")
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection = day
    project.evening_lighting_collection = evening
    project.day_world = bpy.data.worlds.new("ResDayWorld")
    project.evening_world = bpy.data.worlds.new("ResEveningWorld")
    day.objects.link(bpy.data.objects.new("ResSun", bpy.data.lights.new("ResSun", 'SUN')))
    bpy.ops.pmvr.initialize_project()
    project.cycles_samples = 1
    project.uv_padding = 0.008
    for layer in project.render_layers:
        layer.enabled = layer.display_name == "Unlit"
        layer.export_glb = True
        layer.export_usdz = True
    return project, root, output


def bake_unit(project, unit):
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    assert runtime.prepare() == "READY"
    for index in range(len(runtime.receivers)):
        runtime.select_receiver(index)
        assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
    runtime.finish()


def srgb_to_linear(values):
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def load_rgb(path):
    """(size, encoded RGB as float64) of an image file."""
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return (width, height), pixels.reshape(height, width, 4)[..., :3].astype(np.float64)


def linear_mean(path):
    size, rgb = load_rgb(path)
    return size, srgb_to_linear(rgb).reshape(-1, 3).mean(axis=0)


def usdz_textures(path, folder):
    """Texture files packed in a USDZ, extracted to folder."""
    found = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".png"):
                target = os.path.join(folder, "usdz_" + os.path.basename(name))
                with open(target, "wb") as handle:
                    handle.write(archive.read(name))
                found[os.path.basename(name)] = target
    return found


def glb_textures(path, folder):
    """Images embedded in a GLB, extracted to folder."""
    with open(path, "rb") as handle:
        data = handle.read()
    json_length = struct.unpack("<I", data[12:16])[0]
    document = json.loads(data[20:20 + json_length].decode("utf-8"))
    binary = data[20 + json_length + 8:]
    found = []
    for index, image in enumerate(document.get("images", [])):
        view = document["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        extension = ".webp" if image.get("mimeType") == "image/webp" else ".png"
        target = os.path.join(folder, f"glb_{index}{extension}")
        with open(target, "wb") as handle:
            handle.write(binary[start:start + view["byteLength"]])
        found.append(target)
    return found


def file_hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()


def write_png(path, rgb):
    """rgb: (h, w, 3) encoded values in 0..1, written as an 8-bit PNG."""
    height, width, _ = rgb.shape
    image = bpy.data.images.new("__test_png", width, height, alpha=False)
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., :3] = rgb
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw = path
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)


def check_scaler(folder):
    # Flat colour stays exactly the same code at a non-integer ratio.
    flat = np.full((96, 96, 3), 200 / 255.0)
    write_png(os.path.join(folder, "flat.png"), flat)
    bake_files.scale_atlas(os.path.join(folder, "flat.png"), os.path.join(folder, "flat_64.png"), 64)
    size, rgb = load_rgb(os.path.join(folder, "flat_64.png"))
    codes = np.rint(rgb * 255)
    check(size == (64, 64) and codes.min() == 200 and codes.max() == 200,
          f"flat 200 scaled 96->64 became {codes.min()}..{codes.max()} at {size}")

    # Black/white pixels average to half the light: code 188, not 128.
    checker = np.indices((64, 64)).sum(axis=0) % 2
    write_png(os.path.join(folder, "checker.png"), np.repeat(checker[..., None], 3, axis=2).astype(np.float64))
    bake_files.scale_atlas(os.path.join(folder, "checker.png"), os.path.join(folder, "checker_32.png"), 32)
    _size, rgb = load_rgb(os.path.join(folder, "checker_32.png"))
    codes = np.unique(np.rint(rgb * 255))
    check(list(codes) == [188.0], f"black/white checker scaled to {codes}, expected 188")

    # Noisy colour at 3:2 keeps its mean light within 8-bit rounding.
    rng = np.random.default_rng(7)
    noisy = rng.integers(0, 256, size=(96, 96, 3)) / 255.0
    write_png(os.path.join(folder, "noisy.png"), noisy)
    bake_files.scale_atlas(os.path.join(folder, "noisy.png"), os.path.join(folder, "noisy_64.png"), 64)
    _size, before = linear_mean(os.path.join(folder, "noisy.png"))
    _size, after = linear_mean(os.path.join(folder, "noisy_64.png"))
    check(np.abs(after - before).max() < 0.0015, f"3:2 scale moved the mean light {before} -> {after}")
    print(f"resolution: scaler flat/checker/3:2 OK, mean light {before.round(4)} -> {after.round(4)}")


def main():
    PM_VR.register()
    project, root, output = build()
    project.active_render_layer_index = 0
    check(project.bake_resolution == '4096', f"Bake Resolution defaults to {project.bake_resolution}")

    offered = [item.identifier for item in project.bl_rna.properties["default_unit_resolution"].enum_items]
    check("8192" not in offered, f"8192 still offered: {offered}")

    # A 40 m plane at 5 px/cm needs far more than 4096.
    add_plane(root, "Huge", 40.0)
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    huge = project.bake_units[-1]
    check(huge.resolution == '4096', f"large mesh suggested {huge.resolution}")
    log_text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check('for "Huge"' in log_text and "texel density stays below target" in log_text,
          "resolution log does not name the object or the 4096 cap")

    # Bake size: the Bake Resolution, the unit's own when larger, times the test share.
    tile_plane = add_plane(root, "Tile", 1.0)
    # Above the 40 m plane, so the sun reaches it.
    tile_plane.location.z = 1.0
    assert bpy.ops.pmvr.add_bake_unit() == {'FINISHED'}
    tile = project.bake_units[-1]
    tile.resolution = '256'
    for choice, size in {'100': 4096, '75': 3072, '50': 2048, '25': 1024}.items():
        project.test_resolution = choice
        check(bake_size(project, tile) == size, f"{choice}% bakes a 256 unit at {bake_size(project, tile)}")
        check(tile.resolution == '256', f"{choice}% changed the Setup resolution to {tile.resolution}")
    project.test_resolution = '100'
    project.bake_resolution = '1024'
    check(bake_size(project, huge) == 4096, f"a 4096 unit under Bake Resolution 1024 bakes at {bake_size(project, huge)}")
    check(bake_size(project, tile) == 1024, f"a 256 unit under Bake Resolution 1024 bakes at {bake_size(project, tile)}")

    huge_id = huge.unit_id
    project.active_bake_unit_index = list(project.bake_units).index(huge)
    assert bpy.ops.pmvr.remove_bake_unit() == {'FINISHED'}
    tile = next(u for u in project.bake_units if u.unit_id != huge_id)
    activate_state(bpy.context, 'DAY')

    # A full bake keeps the file at the Bake Resolution.
    project.cycles_samples = 8
    bake_unit(project, tile)
    png = os.path.join(output, "Unlit_Tile_Beauty.png")
    image = bpy.data.images[tile.day_beauty_image]
    check(bake_files.png_size(png) == (1024, 1024), f"bake wrote {bake_files.png_size(png)}")
    check(tile.day_baked_resolution == 1024, f"recorded {tile.day_baked_resolution}")
    check(tuple(image.size) == (1024, 1024), f"Blender shows {tuple(image.size)}")
    check(ui.beauty_status(tile, 'DAY') == "Ready", ui.beauty_status(tile, 'DAY'))
    log_text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check("1024px, exports at 256px" in log_text, "bake log misses the bake and export sizes")

    # Export writes the unit's resolution; colour as baked; nothing else changes.
    master_hash = file_hash(png)
    master_path = image.filepath
    _size, baked_light = linear_mean(png)
    extracted = tempfile.mkdtemp(prefix="pmvr_resolution_extract_")

    def export_both(expected):
        assert bpy.ops.pmvr.export_semantic_layers(export_format='BOTH') == {'FINISHED'}, \
            project.last_operation_summary
        textures = usdz_textures(os.path.join(output, "Unlit.usdz"), extracted)
        check(list(textures) == ["Unlit_Tile_Beauty.png"], f"USDZ textures {list(textures)}")
        usdz_size, usdz_light = linear_mean(textures["Unlit_Tile_Beauty.png"])
        check(usdz_size == (expected, expected), f"USDZ atlas {usdz_size}, expected {expected}")
        glb = glb_textures(os.path.join(output, "Unlit.glb"), extracted)
        check(len(glb) == 1, f"GLB images {glb}")
        glb_size, glb_light = linear_mean(glb[0])
        check(glb_size == (expected, expected), f"GLB atlas {glb_size}, expected {expected}")
        check(file_hash(png) == master_hash, "export changed the baked file")
        check(image.filepath == master_path, f"image left pointing at {image.filepath}")
        check(tuple(image.size) == (1024, 1024), f"Blender image after export {tuple(image.size)}")
        return usdz_light, glb_light

    usdz_light, glb_light = export_both(256)
    check(np.abs(usdz_light - baked_light).max() < 0.0015,
          f"USDZ atlas light {usdz_light} differs from baked {baked_light}")
    check(np.abs(glb_light - baked_light).max() < 0.01,
          f"GLB (WebP) atlas light {glb_light} differs from baked {baked_light}")
    print(
        "resolution: mean light baked 1024 "
        f"{baked_light.round(4)}, USDZ 256 {usdz_light.round(4)}, GLB 256 {glb_light.round(4)}"
    )
    log_text = bpy.data.texts["PMVR Pipeline Log"].as_string()
    check("Atlases scaled to unit resolution: 1 x 1024 to 256" in log_text, "export log misses the scaled atlases")
    check("below Setup" not in project.last_operation_summary, project.last_operation_summary)

    # Unit resolution changes without a rebake: up to the baked size...
    tile.resolution = '512'
    check(ui.beauty_status(tile, 'DAY') == "Ready", ui.beauty_status(tile, 'DAY'))
    export_both(512)
    tile.resolution = '1024'
    export_both(1024)
    # ...above it the unit needs a rebake, and says so.
    tile.resolution = '2048'
    check(ui.beauty_status(tile, 'DAY') == "Ready 1K (Setup 2K)", ui.beauty_status(tile, 'DAY'))
    export_both(1024)
    check("baked below Setup resolution" in project.last_operation_summary, project.last_operation_summary)

    # A test bake is a share of the Bake Resolution.
    tile.resolution = '512'
    project.test_resolution = '25'
    bake_unit(project, tile)
    check(bake_files.png_size(png) == (256, 256), f"25% bake wrote {bake_files.png_size(png)}")
    check(tile.resolution == '512' and tile.day_baked_resolution == 256, "test bake changed Setup or lost its size")
    check(ui.beauty_status(tile, 'DAY') == "Ready 256 (Setup 512)", ui.beauty_status(tile, 'DAY'))
    project.test_resolution = '100'
    bake_unit(project, tile)
    check(bake_files.png_size(png) == (1024, 1024), f"100% bake wrote {bake_files.png_size(png)}")
    check(ui.beauty_status(tile, 'DAY') == "Ready", ui.beauty_status(tile, 'DAY'))

    check_scaler(extracted)

    # An output folder on another drive keeps an absolute path.
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(output, "resolution.blend"))
    other_drive = ("Z:" if not bpy.data.filepath.upper().startswith("Z:") else "Y:") + "/pmvr/atlas.png"
    check(bake_files._blend_relative(other_drive) == other_drive, "other-drive path not kept absolute")
    check(bake_files._blend_relative(png).startswith("//"), "same-drive path not relative")

    # A file from before: 8192 stored as enum value 5; a saved test choice.
    tile["resolution"] = 5
    project["default_unit_resolution"] = 5
    bpy.context.scene.pm_lightmap_settings["resolution"] = 5
    project.test_resolution = '50'
    path = os.path.join(output, "resolution.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    project = bpy.context.scene.pm_vr_project
    values = (
        project.bake_units[-1].resolution,
        project.default_unit_resolution,
        bpy.context.scene.pm_lightmap_settings.resolution,
    )
    check(values == ('4096', '4096', '4096'), f"stored 8192 read back as {values}")
    check(project.test_resolution == '100', f"reopened file kept test resolution {project.test_resolution}")
    check(project.bake_resolution == '1024', f"Bake Resolution not kept: {project.bake_resolution}")

    for problem in PROBLEMS:
        print(f"[PM VR] FAIL: {problem}")
    print("PM_VR_RESOLUTION_SMOKE_FAILED" if PROBLEMS else "PM_VR_RESOLUTION_SMOKE_OK")
    PM_VR.unregister()


if __name__ == "__main__":
    main()
