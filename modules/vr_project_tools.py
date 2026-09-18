import math
import os
import re
import shutil

import bpy
from bpy.app.handlers import persistent

from ..selection_targets import get_selected_target_objects
from . import viewport_notice
from .scene_diagnostics import (
    BAKE_UV_NAME as SIMPLE_BAKE_UV_NAME,
    PRIMARY_UV_NAME,
    TARGET_TD_PX_PER_CM,
    get_target_td,
    has_applied_scale,
    has_pipeline_uvs,
    measure_texel_areas,
)

UI_CATEGORY = "OPTIMIZATION"

EXTERNAL_TEXTURE_FOLDER_NAME = "PM_Selected_Textures"
TEXTURE_FILE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".exr",
    ".bmp",
    ".tga",
    ".hdr",
    ".webp",
)
BLENDER_DUPLICATE_SUFFIX_PATTERN = re.compile(r"^(.*)\.(\d{3})$")
PASCAL_CASE_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")

AUDIT_ISSUE_BITS = {
    "bad_object_names": 1 << 0,
    "shared_mesh_data": 1 << 1,
    "mesh_name_mismatch": 1 << 2,
    "material_count": 1 << 3,
    "material_name_mismatch": 1 << 4,
    "shared_materials": 1 << 5,
    "uv_channels": 1 << 6,
    "unapplied_scale": 1 << 7,
}


def iter_scope_objects(context, scope):
    def is_source_mesh(obj):
        return bool(
            obj.type == 'MESH'
            and not obj.get("pmvr_generated")
            and not obj.get("pm_lightmap_generated")
            and not obj.get("pm_vr_material_rebuild_generated")
        )

    if scope == 'SELECTED':
        return [obj for obj in get_selected_target_objects(context) if is_source_mesh(obj)]

    return [obj for obj in context.scene.objects if is_source_mesh(obj)]


def count_material_object_users(material):
    if not material:
        return 0

    users = 0
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not obj.data:
            continue
        if any(slot.material == material for slot in obj.material_slots):
            users += 1
    return users


def get_material_object_users(material):
    if not material:
        return []

    users = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not obj.data:
            continue
        if any(slot.material == material for slot in obj.material_slots):
            users.append(obj)
    return users


def strip_blender_duplicate_suffix(name):
    match = BLENDER_DUPLICATE_SUFFIX_PATTERN.match(name or "")
    if match:
        return match.group(1), match.group(2)
    return name or "", None


def is_pascal_case_name(name):
    return bool(PASCAL_CASE_PATTERN.fullmatch(name or ""))


def remove_empty_material_slots(obj):
    removed = 0
    mesh = obj.data
    for index in reversed(range(len(mesh.materials))):
        if mesh.materials[index] is None:
            mesh.materials.pop(index=index)
            removed += 1
    return removed


def get_non_empty_materials(obj):
    return [slot.material for slot in obj.material_slots if slot.material]


def iter_object_materials(obj):
    seen = set()
    for material in get_non_empty_materials(obj):
        key = material.as_pointer()
        if key in seen:
            continue
        seen.add(key)
        yield material


def material_uses_displacement(material):
    if not material or not material.use_nodes or not material.node_tree:
        return False
    outputs = [
        node for node in material.node_tree.nodes
        if node.type == 'OUTPUT_MATERIAL'
    ]
    active_outputs = [
        node for node in outputs
        if getattr(node, "is_active_output", False)
    ]
    for output in active_outputs or outputs:
        displacement = output.inputs.get("Displacement")
        if displacement and displacement.is_linked:
            return True
    return False


def iter_material_image_nodes(material):
    if not material or not material.use_nodes or not material.node_tree:
        return

    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and getattr(node, "image", None):
            yield node, node.image


def iter_material_base_color_image_nodes(material):
    if not material or not material.use_nodes or not material.node_tree:
        return

    seen_nodes = set()
    for node in material.node_tree.nodes:
        if node.type != 'BSDF_PRINCIPLED':
            continue

        base_color_input = node.inputs.get("Base Color")
        if not base_color_input or not base_color_input.is_linked:
            continue

        for link in base_color_input.links:
            from_node = link.from_node
            if from_node and from_node.type == 'TEX_IMAGE' and getattr(from_node, "image", None):
                key = from_node.as_pointer()
                if key in seen_nodes:
                    continue
                seen_nodes.add(key)
                yield from_node, from_node.image


def get_image_extension(image):
    filepath = getattr(image, "filepath", "") or ""
    extension = os.path.splitext(filepath)[1].lower()
    if extension:
        return extension

    file_format = getattr(image, "file_format", "") or ""
    if file_format:
        return f".{file_format.lower()}"
    return ""


def get_image_file_basename(image):
    filepath = getattr(image, "filepath", "") or ""
    if filepath:
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        if base_name:
            return base_name

    image_name = getattr(image, "name", "") or ""
    base_name = os.path.splitext(image_name)[0]
    return base_name or image_name


def sanitize_filename_component(value):
    sanitized = re.sub(r'[\\/:*?"<>|]+', "_", value or "")
    sanitized = sanitized.strip(" ._")
    return sanitized or "Texture"


def make_image_filepath_for_blender(absolute_path):
    if bpy.data.filepath:
        try:
            return bpy.path.relpath(absolute_path)
        except ValueError:
            return absolute_path
    return absolute_path


def get_image_absolute_path(image):
    filepath = getattr(image, "filepath", "") or ""
    if not filepath:
        return ""
    return bpy.path.abspath(filepath, library=getattr(image, "library", None))


def build_texture_directory_index(directory):
    texture_paths = {}
    duplicates = set()

    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue

        base_name, extension = os.path.splitext(name)
        if extension.lower() not in TEXTURE_FILE_EXTENSIONS:
            continue

        key = base_name.lower()
        if key in texture_paths:
            duplicates.add(key)
            continue
        texture_paths[key] = path

    for key in duplicates:
        texture_paths.pop(key, None)

    return texture_paths, duplicates


def get_matching_texture_path(image, texture_paths):
    key = get_image_file_basename(image).lower()
    return texture_paths.get(key)


def load_image_replacement(filepath, color_space):
    try:
        image = bpy.data.images.load(filepath, check_existing=False)
    except TypeError:
        image = bpy.data.images.load(filepath)

    image.colorspace_settings.name = color_space
    image.filepath = make_image_filepath_for_blender(filepath)
    return image


def get_selected_texture_export_directory():
    if not bpy.data.filepath:
        return ""
    return os.path.join(os.path.dirname(bpy.data.filepath), EXTERNAL_TEXTURE_FOLDER_NAME)


def get_image_output_extension(image):
    extension = get_image_extension(image).lower()
    if extension:
        return ".jpg" if extension == ".jpeg" else extension

    file_format = getattr(image, "file_format", "") or ""
    format_extensions = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "TIFF": ".tif",
        "OPEN_EXR": ".exr",
        "BMP": ".bmp",
        "TARGA": ".tga",
        "HDR": ".hdr",
    }
    return format_extensions.get(file_format, ".png")


def build_unique_texture_output_path(image, output_dir, used_paths):
    base_name = sanitize_filename_component(get_image_file_basename(image))
    extension = get_image_output_extension(image)
    target_path = os.path.join(output_dir, f"{base_name}{extension}")

    suffix = 1
    while target_path.lower() in used_paths or os.path.exists(target_path):
        suffix += 1
        target_path = os.path.join(output_dir, f"{base_name}_{suffix}{extension}")

    used_paths.add(target_path.lower())
    return target_path


def write_packed_image_file(image, target_path):
    packed_file = getattr(image, "packed_file", None)
    packed_data = getattr(packed_file, "data", None)
    if not packed_data:
        return False

    with open(target_path, "wb") as output_file:
        output_file.write(bytes(packed_data))
    return True


def externalize_image_file(image, target_path):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    source_path = get_image_absolute_path(image)
    if source_path and os.path.exists(source_path):
        if os.path.abspath(source_path) != os.path.abspath(target_path):
            shutil.copy2(source_path, target_path)
        return

    if write_packed_image_file(image, target_path):
        return

    image.save(filepath=target_path, save_copy=True)


def localize_materials_for_selected_objects(selected_objects, materials_to_localize):
    selected_keys = {obj.as_pointer() for obj in selected_objects}
    remap = {}

    for material in materials_to_localize:
        users = get_material_object_users(material)
        if any(obj.as_pointer() not in selected_keys for obj in users):
            remap[material.as_pointer()] = material.copy()

    if not remap:
        return 0

    for obj in selected_objects:
        for slot in obj.material_slots:
            material = slot.material
            if not material:
                continue
            replacement = remap.get(material.as_pointer())
            if replacement:
                slot.material = replacement

    return len(remap)


def ensure_single_material_from_object(obj):
    removed_empty = remove_empty_material_slots(obj)
    materials = get_non_empty_materials(obj)

    if len(materials) > 1:
        return False, "multiple_materials", removed_empty

    if not materials:
        material = bpy.data.materials.new(name=obj.name)
        obj.data.materials.append(material)
        return True, "created", removed_empty

    material = materials[0]
    if count_material_object_users(material) > 1:
        material = material.copy()
        obj.material_slots[0].material = material

    blocker = bpy.data.materials.get(obj.name)
    if blocker and blocker != material:
        return False, "material_name_collision", removed_empty

    material.name = obj.name
    return True, "synced", removed_empty


def sync_names_from_objects(objects):
    made_mesh_single_user = 0
    synced_meshes = 0
    synced_materials = 0
    skipped_multiple_materials = []
    skipped_name_collisions = []
    removed_empty_slots = 0

    for obj in objects:
        if obj.data and obj.data.users > 1:
            obj.data = obj.data.copy()
            made_mesh_single_user += 1

        if obj.data and obj.data.name != obj.name:
            blocker = bpy.data.meshes.get(obj.name)
            if blocker and blocker != obj.data:
                skipped_name_collisions.append(obj.name)
                continue
            obj.data.name = obj.name
            synced_meshes += 1

        ok, _status, removed = ensure_single_material_from_object(obj)
        removed_empty_slots += removed
        if not ok:
            if _status == "multiple_materials":
                skipped_multiple_materials.append(obj.name)
            elif _status == "material_name_collision":
                skipped_name_collisions.append(obj.name)
            continue

        synced_materials += 1

    return {
        "made_mesh_single_user": made_mesh_single_user,
        "synced_meshes": synced_meshes,
        "synced_materials": synced_materials,
        "skipped_multiple_materials": skipped_multiple_materials,
        "skipped_name_collisions": skipped_name_collisions,
        "removed_empty_slots": removed_empty_slots,
    }


def fix_uv_channels(mesh):
    """Ensure the first two UV names, copying UV data only when channel two is new."""
    layers = mesh.uv_layers
    created_second = False
    if len(layers) == 0:
        primary = layers.new(name=PRIMARY_UV_NAME, do_init=True)
    else:
        primary = layers[0]

    if len(layers) == 1:
        simple_bake = layers.new(name=SIMPLE_BAKE_UV_NAME, do_init=True)
        created_second = True
    else:
        simple_bake = layers[1]

    # Free the two reserved convention names without deleting or reordering any
    # additional UV layers.
    for index, layer in enumerate(layers):
        if layer not in (primary, simple_bake) and layer.name in {
            PRIMARY_UV_NAME,
            SIMPLE_BAKE_UV_NAME,
        }:
            layer.name = f"{layer.name}_Extra_{index + 1}"

    primary.name = "__PM_TMP_PRIMARY_UV__"
    simple_bake.name = "__PM_TMP_SIMPLE_BAKE_UV__"
    primary.name = PRIMARY_UV_NAME
    simple_bake.name = SIMPLE_BAKE_UV_NAME

    if created_second:
        for source_data, target_data in zip(primary.data, simple_bake.data):
            target_data.uv = source_data.uv

        layers.active = primary
        for layer in layers:
            layer.active_render = layer == simple_bake
    return primary, simple_bake


def fix_uv_channels_for_objects(objects):
    checked = 0
    changed_meshes = 0
    seen_meshes = set()

    for obj in objects:
        mesh = obj.data
        if not mesh:
            continue

        mesh_key = mesh.as_pointer()
        if mesh_key in seen_meshes:
            continue
        seen_meshes.add(mesh_key)

        before = [layer.name for layer in mesh.uv_layers]
        fix_uv_channels(mesh)
        after = [layer.name for layer in mesh.uv_layers]
        checked += 1
        if before != after:
            changed_meshes += 1

    return checked, changed_meshes


def ensure_uv_channels(mesh):
    """Compatibility alias for scripts using the former mutating helper."""
    return fix_uv_channels(mesh)


def ensure_uv_channels_for_objects(objects):
    """Compatibility alias for the former mutating operator backend."""
    return fix_uv_channels_for_objects(objects)


def has_valid_uv_channels(mesh):
    """Compatibility name for the shared pipeline UV predicate."""
    return has_pipeline_uvs(mesh)


def objects_with_invalid_uv_channels(objects):
    return [obj for obj in objects if not has_valid_uv_channels(obj.data)]


def naming_issue_keys(obj):
    issues = []
    if not is_pascal_case_name(obj.name) or BLENDER_DUPLICATE_SUFFIX_PATTERN.match(obj.name):
        issues.append("bad_object_names")
    if obj.data and obj.data.name != obj.name:
        issues.append("mesh_name_mismatch")
    materials = get_non_empty_materials(obj)
    if len(materials) == 1:
        material = materials[0]
        if material.name != obj.name:
            issues.append("material_name_mismatch")
    return issues


def audit_issue_keys(obj):
    issues = []
    if obj.data and obj.data.users > 1:
        issues.append("shared_mesh_data")
    if obj.data and not has_valid_uv_channels(obj.data):
        issues.append("uv_channels")
    if not has_applied_scale(obj):
        issues.append("unapplied_scale")
    return issues


def audit_flags(obj):
    return sum(AUDIT_ISSUE_BITS[key] for key in audit_issue_keys(obj))


def audit_issue_count(flags):
    return sum(bool(flags & bit) for bit in AUDIT_ISSUE_BITS.values())


def audit_issue_details(obj, flags):
    details = []
    if flags & AUDIT_ISSUE_BITS["bad_object_names"]:
        details.append("Object name must be PascalCase without a .001 suffix")
    if flags & AUDIT_ISSUE_BITS["shared_mesh_data"]:
        details.append(f'Mesh data is shared by {obj.data.users} objects')
    if flags & AUDIT_ISSUE_BITS["mesh_name_mismatch"]:
        details.append(f'Mesh is "{obj.data.name}"; expected "{obj.name}"')
    if flags & AUDIT_ISSUE_BITS["material_count"]:
        non_empty = len(get_non_empty_materials(obj))
        details.append(f"Expected one material; found {non_empty}")
    if flags & AUDIT_ISSUE_BITS["material_name_mismatch"]:
        material = get_non_empty_materials(obj)[0]
        details.append(f'Material is "{material.name}"; expected "{obj.name}"')
    if flags & AUDIT_ISSUE_BITS["shared_materials"]:
        material = get_non_empty_materials(obj)[0]
        details.append(
            f'Material "{material.name}" is shared by '
            f'{count_material_object_users(material)} objects'
        )
    if flags & AUDIT_ISSUE_BITS["uv_channels"]:
        names = [layer.name for layer in obj.data.uv_layers]
        current = ", ".join(names[:3]) if names else "none"
        if len(names) > 3:
            current += ", ..."
        details.append(f'UV channels are [{current}]; expected UVMap, SimpleBake')
    if flags & AUDIT_ISSUE_BITS["unapplied_scale"]:
        values = ", ".join(f"{value:.3g}" for value in obj.scale)
        details.append(f"Scale is [{values}]; expected 1, 1, 1")
    return details


def audit_objects(objects):
    issues = {
        "bad_object_names": [],
        "shared_mesh_data": [],
        "mesh_name_mismatch": [],
        "material_count": [],
        "material_name_mismatch": [],
        "shared_materials": [],
        "uv_channels": [],
        "unapplied_scale": [],
    }

    for obj in objects:
        for issue_key in audit_issue_keys(obj):
            issues[issue_key].append(obj.name)

    return issues


class PMVR_AuditResultItem(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)  # type: ignore[reportInvalidTypeForm]
    issue_flags: bpy.props.IntProperty(default=0)  # type: ignore[reportInvalidTypeForm]


def _prune_missing_audit_results(scene):
    for index in reversed(range(len(scene.pm_vr_audit_results))):
        if scene.pm_vr_audit_results[index].object is None:
            scene.pm_vr_audit_results.remove(index)
    scene.pm_vr_audit_index = min(
        scene.pm_vr_audit_index,
        max(0, len(scene.pm_vr_audit_results) - 1),
    )


def _active_audit_result(scene):
    _prune_missing_audit_results(scene)
    if not scene.pm_vr_audit_results:
        return None
    return scene.pm_vr_audit_results[scene.pm_vr_audit_index]


def _recount_audit_issues(scene):
    scene.pm_vr_audit_issue_count = sum(
        audit_issue_count(item.issue_flags)
        for item in scene.pm_vr_audit_results
    )


def _select_only_audit_object(context, obj):
    if obj is None or context.view_layer.objects.get(obj.name) is not obj:
        return False
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return True


def _store_audit_results(scene, objects, scope_label):
    scene.pm_vr_audit_results.clear()
    scene.pm_vr_audit_checked_count = len(objects)
    scene.pm_vr_audit_scope_label = scope_label
    for obj in objects:
        flags = audit_flags(obj)
        if not flags:
            continue
        item = scene.pm_vr_audit_results.add()
        item.object = obj
        item.issue_flags = flags
    scene.pm_vr_audit_index = 0
    _recount_audit_issues(scene)


def _audit_group_counts(scene):
    data_bits = AUDIT_ISSUE_BITS["shared_mesh_data"]
    uv_bit = AUDIT_ISSUE_BITS["uv_channels"]
    scale_bit = AUDIT_ISSUE_BITS["unapplied_scale"]
    counts = {"Data": 0, "UV Channels": 0, "Scale": 0}
    for item in scene.pm_vr_audit_results:
        counts["Data"] += audit_issue_count(item.issue_flags & data_bits)
        counts["UV Channels"] += audit_issue_count(item.issue_flags & uv_bit)
        counts["Scale"] += audit_issue_count(item.issue_flags & scale_bit)
    return counts


def _show_audit_summary(scene, first_selected=False):
    problem_count = len(scene.pm_vr_audit_results)
    issue_count = scene.pm_vr_audit_issue_count
    if not problem_count:
        viewport_notice.show(
            "PM VR AUDIT",
            f"Checked {scene.pm_vr_audit_checked_count} object(s)",
            (("INFO", "No problems found"),),
            level='SUCCESS',
        )
        return
    counts = _audit_group_counts(scene)
    category_line = "  ·  ".join(
        f"{name}: {count}" for name, count in counts.items() if count
    )
    viewport_notice.show(
        "PM VR AUDIT",
        f"{scene.pm_vr_audit_checked_count} checked  ·  {problem_count} problem objects  ·  {issue_count} issues",
        (
            ("WARNING", category_line),
            (
                "STATUS",
                "First problem selected — fix it, then Recheck & Next"
                if first_selected
                else "Problems found outside the active View Layer",
            ),
        ),
        level='WARNING',
    )


def get_objects_from_issue_names(issue_names):
    objects = []
    for name in issue_names:
        obj = bpy.data.objects.get(name)
        if obj:
            objects.append(obj)
    return objects


def get_problem_objects(issues):
    names = set()
    for issue_names in issues.values():
        names.update(issue_names)
    return get_objects_from_issue_names(sorted(names))


def select_scene_objects(context, objects):
    view_objects = {obj.as_pointer(): obj for obj in context.view_layer.objects}
    selectable = [view_objects[obj.as_pointer()] for obj in objects if obj.as_pointer() in view_objects]

    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    bpy.ops.object.select_all(action='DESELECT')
    for obj in selectable:
        obj.select_set(True)

    if selectable:
        context.view_layer.objects.active = selectable[0]

    return len(selectable), len(objects) - len(selectable)


def format_issue_sample(names):
    if len(names) <= 6:
        return ", ".join(names)
    return f"{', '.join(names[:6])}, ..."


class PM_OT_VR_AuditObjectPrep(bpy.types.Operator):
    bl_idname = "pm_vr.audit_object_prep"
    bl_label = "Audit Object Prep"
    bl_description = "Check shared mesh data, UV channels, and unapplied scale"
    bl_options = {'REGISTER'}

    scope: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Scope",
        items=(
            ('SELECTED', "Selected", "Only selected mesh targets"),
            ('ALL', "Scene", "All source mesh objects in the current scene"),
        ),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        if not objects:
            self.report({'WARNING'}, "No mesh objects found in scope")
            viewport_notice.show(
                "PM VR AUDIT",
                "Nothing to check",
                (("WARNING", "Select one or more mesh objects"),),
                level='WARNING',
            )
            return {'CANCELLED'}

        scene = context.scene
        scope_label = "Selected" if self.scope == 'SELECTED' else "Scene"
        _store_audit_results(scene, objects, scope_label)
        if not scene.pm_vr_audit_results:
            _show_audit_summary(scene)
            self.report({'INFO'}, f"Audit passed for {len(objects)} object(s)")
            return {'FINISHED'}

        selected = False
        for index, result in enumerate(scene.pm_vr_audit_results):
            if _select_only_audit_object(context, result.object):
                scene.pm_vr_audit_index = index
                selected = True
                break
        _show_audit_summary(scene, first_selected=selected)
        hidden_note = " First problem is outside the active View Layer." if not selected else ""
        self.report(
            {'WARNING'},
            f"Audit found {scene.pm_vr_audit_issue_count} issue(s) on "
            f"{len(scene.pm_vr_audit_results)} object(s).{hidden_note}",
        )
        return {'FINISHED'}


class PM_OT_VR_AuditNavigate(bpy.types.Operator):
    bl_idname = "pm_vr.audit_navigate"
    bl_label = "Previous/Next Audit Problem"

    direction: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        items=(('PREVIOUS', "Previous", ""), ('NEXT', "Next", "")),
        default='NEXT',
    )

    def execute(self, context):
        scene = context.scene
        _prune_missing_audit_results(scene)
        count = len(scene.pm_vr_audit_results)
        if not count:
            return {'CANCELLED'}
        offset = -1 if self.direction == 'PREVIOUS' else 1
        scene.pm_vr_audit_index = (scene.pm_vr_audit_index + offset) % count
        item = scene.pm_vr_audit_results[scene.pm_vr_audit_index]
        if not _select_only_audit_object(context, item.object):
            self.report({'WARNING'}, "Object is outside the active View Layer")
        return {'FINISHED'}


class PM_OT_VR_AuditSelectActive(bpy.types.Operator):
    bl_idname = "pm_vr.audit_select_active"
    bl_label = "Select Audit Object"

    def execute(self, context):
        item = _active_audit_result(context.scene)
        if not item or not _select_only_audit_object(context, item.object):
            self.report({'WARNING'}, "Audit object is unavailable in the active View Layer")
            return {'CANCELLED'}
        return {'FINISHED'}


class PM_OT_VR_AuditSelectAll(bpy.types.Operator):
    bl_idname = "pm_vr.audit_select_all"
    bl_label = "Select All Problems"

    def execute(self, context):
        scene = context.scene
        _prune_missing_audit_results(scene)
        objects = [item.object for item in scene.pm_vr_audit_results if item.object]
        selected, hidden = select_scene_objects(context, objects)
        item = _active_audit_result(scene)
        if item and item.object and context.view_layer.objects.get(item.object.name) is item.object:
            context.view_layer.objects.active = item.object
        message = f"Selected {selected} problem object(s)"
        if hidden:
            message += f", {hidden} outside the active View Layer"
        self.report({'WARNING'} if hidden else {'INFO'}, message)
        return {'FINISHED'} if selected else {'CANCELLED'}


class PM_OT_VR_AuditRecheckActive(bpy.types.Operator):
    bl_idname = "pm_vr.audit_recheck_active"
    bl_label = "Recheck & Next"
    bl_description = "Recheck only the current audit object; remove it and advance when clean"

    def execute(self, context):
        scene = context.scene
        item = _active_audit_result(scene)
        if not item:
            return {'CANCELLED'}
        obj = item.object
        index = scene.pm_vr_audit_index
        if obj is None:
            scene.pm_vr_audit_results.remove(index)
        else:
            flags = audit_flags(obj)
            if flags:
                item.issue_flags = flags
                _recount_audit_issues(scene)
                remaining = audit_issue_count(flags)
                _select_only_audit_object(context, obj)
                viewport_notice.show(
                    "PM VR AUDIT",
                    f"{obj.name} still has {remaining} issue(s)",
                    tuple(("WARNING", text) for text in audit_issue_details(obj, flags)[:4]),
                    level='WARNING',
                )
                self.report({'WARNING'}, f"{obj.name}: {remaining} issue(s) remain")
                return {'FINISHED'}
            scene.pm_vr_audit_results.remove(index)

        scene.pm_vr_audit_index = min(index, max(0, len(scene.pm_vr_audit_results) - 1))
        _recount_audit_issues(scene)
        if not scene.pm_vr_audit_results:
            viewport_notice.show(
                "PM VR AUDIT",
                "All audited problems are resolved",
                (("INFO", "The working stack is empty"),),
                level='SUCCESS',
            )
            self.report({'INFO'}, "All audited problems are resolved")
            return {'FINISHED'}

        next_item = scene.pm_vr_audit_results[scene.pm_vr_audit_index]
        _select_only_audit_object(context, next_item.object)
        viewport_notice.show(
            "PM VR AUDIT",
            f"Fixed — {len(scene.pm_vr_audit_results)} problem object(s) remain",
            (("STATUS", f"Next: {next_item.object.name}"),),
            level='SUCCESS',
        )
        return {'FINISHED'}


class PM_OT_VR_AuditClear(bpy.types.Operator):
    bl_idname = "pm_vr.audit_clear"
    bl_label = "Clear Audit Results"

    def execute(self, context):
        scene = context.scene
        scene.pm_vr_audit_results.clear()
        scene.pm_vr_audit_index = 0
        scene.pm_vr_audit_checked_count = 0
        scene.pm_vr_audit_issue_count = 0
        scene.pm_vr_audit_scope_label = ""
        return {'FINISHED'}


class PM_OT_VR_CheckNames(bpy.types.Operator):
    bl_idname = "pm_vr.check_names"
    bl_label = "Check Names"
    bl_description = "Select objects that do not follow the legacy customer naming rules"
    bl_options = {'REGISTER'}

    scope: bpy.props.EnumProperty(
        name="Scope",
        items=(
            ('SELECTED', "Selected", "Only selected mesh targets"),
            ('ALL', "Scene", "All source mesh objects in the current scene"),
        ),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        problems = [obj for obj in objects if naming_issue_keys(obj)]
        selected, hidden = select_scene_objects(context, problems)
        message = f"Name check: {len(problems)} problem object(s)"
        if hidden:
            message += f", {hidden} outside the active View Layer"
        self.report({'WARNING'} if problems else {'INFO'}, message)
        return {'FINISHED'}


class PM_OT_VR_SyncNamesFromObjects(bpy.types.Operator):
    bl_idname = "pm_vr.sync_names_from_objects"
    bl_label = "Sync Names From Objects"
    bl_description = "Copy each object name to its mesh data-block and single material"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Scope",
        items=(
            ('SELECTED', "Selected", "Only selected mesh targets"),
            ('ALL', "Scene", "All source mesh objects in the current scene"),
        ),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        if not objects:
            self.report({'WARNING'}, "No mesh objects found in scope")
            return {'CANCELLED'}

        result = sync_names_from_objects(objects)
        skipped = result["skipped_multiple_materials"]
        if skipped:
            print("[PM VR][VR Project] Skipped objects with multiple material slots:")
            print(f"  {format_issue_sample(skipped)}")

        collisions = result["skipped_name_collisions"]
        if collisions:
            print("[PM VR][VR Project] Skipped objects with occupied mesh/material names:")
            print(f"  {format_issue_sample(collisions)}")

        message = (
            f"Single-user meshes {result['made_mesh_single_user']}, "
            f"synced {result['synced_meshes']} mesh names, "
            f"{result['synced_materials']} materials"
        )
        skipped_total = len(skipped) + len(collisions)
        if skipped_total:
            self.report({'WARNING'}, f"{message}; skipped {skipped_total} object(s)")
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


class PM_OT_VR_CheckUVChannels(bpy.types.Operator):
    bl_idname = "pm_vr.check_uv_channels"
    bl_label = "Check UV Channels"
    bl_description = "Select only objects whose first two UV channels are not UVMap and SimpleBake; change no data"
    bl_options = {'REGISTER'}

    scope: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Scope",
        items=(
            ('SELECTED', "Selected", "Only selected mesh targets"),
            ('ALL', "Scene", "All source mesh objects in the current scene"),
        ),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        if not objects:
            self.report({'WARNING'}, "No mesh objects found in scope")
            return {'CANCELLED'}

        invalid = objects_with_invalid_uv_channels(objects)
        selected, hidden = select_scene_objects(context, invalid)
        if not invalid:
            self.report({'INFO'}, f"UV check passed for {len(objects)} object(s)")
            return {'FINISHED'}
        hidden_note = f", {hidden} not visible in current view layer" if hidden else ""
        self.report(
            {'WARNING'},
            f"Found {len(invalid)} object(s) with invalid UV channels; selected {selected}{hidden_note}",
        )
        return {'FINISHED'}


class PM_OT_VR_FixUVChannels(bpy.types.Operator):
    bl_idname = "pm_vr.fix_uv_channels"
    bl_label = "Fix UV Channels"
    bl_description = (
        "Rename the first two UV channels to UVMap and SimpleBake; create and copy "
        "UVMap only when the second channel does not exist"
    )
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        name="Scope",
        items=(
            ('SELECTED', "Selected", "Only selected mesh targets"),
            ('ALL', "Scene", "All source mesh objects in the current scene"),
        ),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        if not objects:
            self.report({'WARNING'}, "No mesh objects found in scope")
            return {'CANCELLED'}

        checked, changed = fix_uv_channels_for_objects(objects)
        self.report(
            {'INFO'},
            f"UV channels fixed: processed {checked}, changed {changed} mesh data-block(s)",
        )
        return {'FINISHED'}


class PM_OT_VR_EnsureUVChannelsCompatibility(bpy.types.Operator):
    """Hidden compatibility endpoint for old scripts and saved operator searches."""

    bl_idname = "pm_vr.ensure_uv_channels"
    bl_label = "Fix UV Channels (Legacy ID)"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    scope: bpy.props.EnumProperty(  # type: ignore[reportInvalidTypeForm]
        items=(('SELECTED', "Selected", ""), ('ALL', "Scene", "")),
        default='ALL',
    )

    def execute(self, context):
        objects = iter_scope_objects(context, self.scope)
        if not objects:
            return {'CANCELLED'}
        fix_uv_channels_for_objects(objects)
        return {'FINISHED'}


class PM_OT_VR_RelinkSelectedTexturesFromFolder(bpy.types.Operator):
    bl_idname = "pm_vr.relink_selected_textures_from_folder"
    bl_label = "Relink Selected Textures"
    bl_description = (
        "Choose a folder and relink selected objects' image texture nodes by matching file names"
    )
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(  # type: ignore[reportInvalidTypeForm]
        name="Texture Directory",
        subtype='DIR_PATH',
    )
    filter_folder: bpy.props.BoolProperty(  # type: ignore[reportInvalidTypeForm]
        default=True,
        options={'HIDDEN'},
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        texture_directory = bpy.path.abspath(self.directory)
        if not texture_directory or not os.path.isdir(texture_directory):
            self.report({'WARNING'}, "Choose a valid texture directory")
            return {'CANCELLED'}

        try:
            texture_paths, duplicate_keys = build_texture_directory_index(texture_directory)
        except OSError as exc:
            self.report({'ERROR'}, f"Cannot read texture directory: {exc}")
            return {'CANCELLED'}

        if not texture_paths:
            self.report({'WARNING'}, "No supported texture files found in selected directory")
            return {'CANCELLED'}

        selected_objects = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected_objects:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        materials_to_update = []
        material_keys = set()
        missing_matches = 0
        duplicate_matches = 0
        for obj in selected_objects:
            for material in iter_object_materials(obj):
                has_matching_texture = False
                for _node, image in iter_material_image_nodes(material):
                    image_key = get_image_file_basename(image).lower()
                    if image_key in duplicate_keys:
                        duplicate_matches += 1
                        continue
                    if image_key not in texture_paths:
                        missing_matches += 1
                        continue
                    has_matching_texture = True

                if has_matching_texture:
                    key = material.as_pointer()
                    if key in material_keys:
                        continue
                    material_keys.add(key)
                    materials_to_update.append(material)

        if not materials_to_update:
            self.report({'WARNING'}, "No matching texture files found for selected objects")
            return {'CANCELLED'}

        localized_materials = localize_materials_for_selected_objects(selected_objects, materials_to_update)

        processed_materials = set()
        loaded_images = {}
        loaded_count = 0
        relinked_nodes = 0
        failed_loads = 0

        for obj in selected_objects:
            for material in iter_object_materials(obj):
                material_key = material.as_pointer()
                if material_key in processed_materials:
                    continue
                processed_materials.add(material_key)

                for node, image in iter_material_image_nodes(material):
                    target_path = get_matching_texture_path(image, texture_paths)
                    if not target_path:
                        continue

                    color_space = image.colorspace_settings.name
                    image_key = (target_path.lower(), color_space)
                    replacement_image = loaded_images.get(image_key)
                    if replacement_image is None:
                        try:
                            replacement_image = load_image_replacement(target_path, color_space)
                        except Exception as exc:
                            failed_loads += 1
                            print(f"[PM VR][VR Project] Texture load failed for {target_path}: {exc}")
                            continue

                        loaded_images[image_key] = replacement_image
                        loaded_count += 1

                    node.image = replacement_image
                    relinked_nodes += 1

        if relinked_nodes == 0:
            self.report({'WARNING'}, "Nothing was relinked; matching texture files could not be loaded")
            return {'CANCELLED'}

        message = (
            f"Loaded {loaded_count} texture(s), relinked {relinked_nodes} node(s)"
        )
        if localized_materials:
            message += f", localized {localized_materials} material(s)"
        if missing_matches:
            message += f", missing {missing_matches} match(es)"
        if duplicate_matches:
            message += f", skipped {duplicate_matches} duplicate-name match(es)"
        if failed_loads:
            message += f", failed to load {failed_loads} texture(s)"
        self.report({'INFO'}, message)
        return {'FINISHED'}


class PM_OT_VR_ExternalizeSelectedTextures(bpy.types.Operator):
    bl_idname = "pm_vr.externalize_selected_textures"
    bl_label = "Unpack Selected Textures"
    bl_description = (
        "Save or copy selected objects' material textures to a folder near the blend file and relink them"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected_objects = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected_objects:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        output_dir = get_selected_texture_export_directory()
        if not output_dir:
            self.report({'WARNING'}, "Save the blend file before unpacking selected textures")
            return {'CANCELLED'}

        materials_to_update = []
        material_keys = set()
        for obj in selected_objects:
            for material in iter_object_materials(obj):
                if not any(True for _node, _image in iter_material_image_nodes(material)):
                    continue

                key = material.as_pointer()
                if key in material_keys:
                    continue
                material_keys.add(key)
                materials_to_update.append(material)

        if not materials_to_update:
            self.report({'WARNING'}, "No image texture nodes found on selected objects")
            return {'CANCELLED'}

        os.makedirs(output_dir, exist_ok=True)
        localized_materials = localize_materials_for_selected_objects(selected_objects, materials_to_update)

        if getattr(bpy.data, "use_autopack", False):
            bpy.data.use_autopack = False
            disabled_autopack = True
        else:
            disabled_autopack = False

        processed_materials = set()
        externalized_images = {}
        used_target_paths = set()
        externalized_count = 0
        relinked_nodes = 0
        failed_exports = 0

        for obj in selected_objects:
            for material in iter_object_materials(obj):
                material_key = material.as_pointer()
                if material_key in processed_materials:
                    continue
                processed_materials.add(material_key)

                for node, image in iter_material_image_nodes(material):
                    image_key = image.as_pointer()
                    replacement_image = externalized_images.get(image_key)
                    if replacement_image is None:
                        source_path = get_image_absolute_path(image)
                        if (
                            source_path
                            and os.path.exists(source_path)
                            and os.path.abspath(os.path.dirname(source_path)) == os.path.abspath(output_dir)
                        ):
                            target_path = source_path
                            used_target_paths.add(target_path.lower())
                        else:
                            target_path = build_unique_texture_output_path(image, output_dir, used_target_paths)

                        try:
                            externalize_image_file(image, target_path)
                            replacement_image = load_image_replacement(
                                target_path,
                                image.colorspace_settings.name,
                            )
                        except Exception as exc:
                            failed_exports += 1
                            print(f"[PM VR][VR Project] Texture externalize failed for {image.name}: {exc}")
                            continue

                        externalized_images[image_key] = replacement_image
                        externalized_count += 1

                    node.image = replacement_image
                    relinked_nodes += 1

        if relinked_nodes == 0:
            self.report({'WARNING'}, "Nothing was relinked; selected textures could not be externalized")
            return {'CANCELLED'}

        message = (
            f"Externalized {externalized_count} texture(s), relinked {relinked_nodes} node(s)"
        )
        if localized_materials:
            message += f", localized {localized_materials} material(s)"
        if disabled_autopack:
            message += ", disabled Auto Pack"
        if failed_exports:
            message += f", failed {failed_exports} texture(s)"
        self.report({'INFO'}, message)
        return {'FINISHED'}


_TD_DEFAULT_VERSION_KEY = "pm_vr_target_td_default_version"
TEXTURE_OPTIONS = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
}

TD_LABEL_TEXT_VARIANTS = (
    "_1K",
    "_2K",
    "_4K",
    "1K_",
    "2K_",
    "4K_",
    "_1\u041a",
    "_2\u041a",
    "_4\u041a",
    "1\u041a_",
    "2\u041a_",
    "4\u041a_",
)


def remove_td_suffix(name):
    new_name = name or ""
    for variant in TD_LABEL_TEXT_VARIANTS:
        new_name = new_name.replace(variant, "")
    return new_name


def build_td_name(base_name, suffix, use_prefix):
    if not base_name:
        return suffix
    if use_prefix:
        return f"{suffix}_{base_name}"
    return f"{base_name}_{suffix}"


def get_mesh_areas(obj):
    """Compatibility wrapper around the canonical pipeline measurement."""
    return measure_texel_areas(bpy.context, obj)


# LEGACY TEXEL-LABEL WORKFLOW
# Hidden from the Optimize UI since bake-unit resolution superseded object-name
# 1K/2K/4K labels. Keep the helpers, operators, and Scene properties registered
# for old files, scripts, and emergency manual use. See docs/LEGACY.md.
def _migrate_target_td_defaults():
    scenes = getattr(bpy.data, "scenes", ())
    for scene in scenes:
        if scene.get(_TD_DEFAULT_VERSION_KEY, 0) >= 1:
            continue
        if abs(float(scene.pm_vr_target_td) - 10.0) < 1.0e-6:
            scene.pm_vr_target_td = TARGET_TD_PX_PER_CM
        scene[_TD_DEFAULT_VERSION_KEY] = 1


@persistent
def _migrate_target_td_load_post(_filepath):
    _migrate_target_td_defaults()


def get_use_texture_prefix(context):
    return getattr(context.scene, "pm_vr_use_texture_prefix", False)


def choose_texture_suffix(uv_area, mesh_area_cm2, target_td):
    if mesh_area_cm2 <= 0.0 or uv_area <= 0.0:
        return "4K", {}

    td_results = {}
    for suffix, size in TEXTURE_OPTIONS.items():
        td_results[suffix] = size * math.sqrt(uv_area / mesh_area_cm2)

    for suffix, size in sorted(TEXTURE_OPTIONS.items(), key=lambda item: item[1]):
        if td_results[suffix] >= target_td:
            return suffix, td_results

    return "4K", td_results


class PM_OT_VR_AddTextureSuffix(bpy.types.Operator):
    bl_idname = "pm_vr.add_texture_suffix"
    bl_label = "Add Texture Label"
    bl_description = (
        "Add 1K/2K/4K prefix or suffix based on the current texel density target "
        f"(UV: {SIMPLE_BAKE_UV_NAME})"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        renamed = 0
        skipped = 0
        target_td = get_target_td(context)
        use_prefix = get_use_texture_prefix(context)

        for obj in selected:
            mesh_area_cm2, uv_area, error = get_mesh_areas(obj)
            if error:
                skipped += 1
                print(f"[PM VR][VR Project] SKIPPED {obj.name}: {error}")
                continue

            suffix, td_results = choose_texture_suffix(uv_area, mesh_area_cm2, target_td)
            base_name = remove_td_suffix(obj.name)
            new_name = build_td_name(base_name, suffix, use_prefix)
            obj.name = new_name
            renamed += 1

            print(
                f"[PM VR][VR Project] {base_name} -> {new_name} | "
                f"Area: {mesh_area_cm2:.1f} cm\u00b2 | "
                f"TD 1K: {td_results.get('1K', 0):.1f}  "
                f"2K: {td_results.get('2K', 0):.1f}  "
                f"4K: {td_results.get('4K', 0):.1f} px/cm | "
                f"Target: {target_td:.1f} px/cm"
            )

        msg = f"Renamed {renamed} object(s)"
        if skipped:
            msg += f", skipped {skipped}"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PM_OT_VR_RemoveTextureSuffix(bpy.types.Operator):
    bl_idname = "pm_vr.remove_texture_suffix"
    bl_label = "Remove Texture Label"
    bl_description = "Remove 1K/2K/4K prefix or suffix tokens from selected mesh object names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        renamed = 0
        skipped = 0

        for obj in selected:
            new_name = remove_td_suffix(obj.name)
            if new_name == obj.name:
                skipped += 1
                continue

            obj.name = new_name
            renamed += 1

        msg = f"Removed suffix from {renamed} object(s)"
        if skipped:
            msg += f", skipped {skipped}"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PM_OT_VR_ActivateUVMap(bpy.types.Operator):
    bl_idname = "pm_vr.activate_uvmap"
    bl_label = "Activate UVMap"
    bl_description = "Set first UV layer (UVMap) as active and renderable on selected meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        count = 0
        for obj in selected:
            if not obj.data or not obj.data.uv_layers:
                continue
            layers = obj.data.uv_layers
            target = layers.get(PRIMARY_UV_NAME) or layers[0]
            layers.active = target
            for layer in layers:
                layer.active_render = (layer == target)
            count += 1

        self.report({'INFO'}, f"Activated UVMap on {count} object(s)")
        return {'FINISHED'}


class PM_OT_VR_ActivateSimpleBake(bpy.types.Operator):
    bl_idname = "pm_vr.activate_simplebake"
    bl_label = "Activate SimpleBake"
    bl_description = "Set SimpleBake UV layer as active and renderable on selected meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [
            obj for obj in get_selected_target_objects(context)
            if obj.type == 'MESH'
        ]
        if not selected:
            self.report({'WARNING'}, "No selected mesh objects found")
            return {'CANCELLED'}

        count = 0
        missing = 0
        for obj in selected:
            if not obj.data or not obj.data.uv_layers:
                continue
            layers = obj.data.uv_layers
            target = layers.get(SIMPLE_BAKE_UV_NAME)
            if not target:
                missing += 1
                continue
            layers.active = target
            for layer in layers:
                layer.active_render = (layer == target)
            count += 1

        msg = f"Activated SimpleBake on {count} object(s)"
        if missing:
            msg += f", {missing} without SimpleBake layer"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class PM_OT_VR_SelectDisplacementObjects(bpy.types.Operator):
    bl_idname = "pm_vr.select_displacement_objects"
    bl_label = "Select Displacement Objects"
    bl_description = (
        "Select scene objects whose active Material Output has a linked "
        "Displacement input"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        matches = [
            obj for obj in context.scene.objects
            if any(
                material_uses_displacement(material)
                for material in iter_object_materials(obj)
            )
        ]

        selectable = {
            obj.as_pointer(): obj for obj in context.view_layer.objects
        }
        bpy.ops.object.select_all(action='DESELECT')
        selected = []
        unavailable = 0
        for obj in matches:
            view_object = selectable.get(obj.as_pointer())
            if not view_object or view_object.hide_select:
                unavailable += 1
                continue
            try:
                view_object.select_set(True)
                selected.append(view_object)
            except (ReferenceError, RuntimeError):
                unavailable += 1

        if selected:
            context.view_layer.objects.active = selected[0]
        message = f"Selected {len(selected)} displacement object(s)"
        if unavailable:
            message += f", {unavailable} unavailable in this View Layer"
        self.report({'INFO'} if selected else {'WARNING'}, message)
        return {'FINISHED'}


def draw_ui(layout, context):
    box = layout.box()
    scene = context.scene

    audit_row = box.row(align=True)
    op = audit_row.operator(
        PM_OT_VR_AuditObjectPrep.bl_idname,
        text="Audit Selected",
        icon='CHECKMARK',
    )
    op.scope = 'SELECTED'
    op = audit_row.operator(
        PM_OT_VR_AuditObjectPrep.bl_idname,
        text="Scene",
        icon='SCENE_DATA',
    )
    op.scope = 'ALL'

    if scene.pm_vr_audit_results:
        index = min(scene.pm_vr_audit_index, len(scene.pm_vr_audit_results) - 1)
        item = scene.pm_vr_audit_results[index]
        audit_results = box.box()
        summary = audit_results.row(align=True)
        summary.label(
            text=(
                f"{len(scene.pm_vr_audit_results)} problem object(s)  ·  "
                f"{scene.pm_vr_audit_issue_count} issue(s)"
            ),
            icon='ERROR',
        )
        summary.operator(PM_OT_VR_AuditClear.bl_idname, text="", icon='X')

        nav = audit_results.row(align=True)
        op = nav.operator(PM_OT_VR_AuditNavigate.bl_idname, text="", icon='TRIA_LEFT')
        op.direction = 'PREVIOUS'
        object_name = item.object.name if item.object else "Missing object"
        nav.label(text=f"{index + 1} / {len(scene.pm_vr_audit_results)}   {object_name}")
        op = nav.operator(PM_OT_VR_AuditNavigate.bl_idname, text="", icon='TRIA_RIGHT')
        op.direction = 'NEXT'

        if item.object:
            for detail in audit_issue_details(item.object, item.issue_flags):
                audit_results.label(text=detail, icon='DOT')
        else:
            audit_results.label(text="Object was removed from the file", icon='ERROR')

        actions = audit_results.row(align=True)
        actions.operator(PM_OT_VR_AuditSelectActive.bl_idname, text="Select Object", icon='RESTRICT_SELECT_OFF')
        actions.operator(PM_OT_VR_AuditRecheckActive.bl_idname, icon='FILE_REFRESH')
        footer = audit_results.row(align=True)
        footer.operator(PM_OT_VR_AuditSelectAll.bl_idname, icon='GROUP')
        footer.label(text="Snapshot · recheck after edits", icon='INFO')

    box.separator()

    name_col = box.column(align=True)
    row = name_col.row(align=True)
    op = row.operator(PM_OT_VR_CheckNames.bl_idname, text="Check Names", icon='VIEWZOOM')
    op.scope = 'SELECTED'
    op = row.operator(PM_OT_VR_SyncNamesFromObjects.bl_idname, text="Sync Names", icon='OUTLINER_OB_MESH')
    op.scope = 'SELECTED'

    box.separator()

    uv_col = box.column(align=True)
    uv_col.label(text="UV Channels:")
    row = uv_col.row(align=True)
    op = row.operator(PM_OT_VR_CheckUVChannels.bl_idname, text="Check UV Channels", icon='VIEWZOOM')
    op.scope = 'SELECTED'
    op = row.operator(PM_OT_VR_FixUVChannels.bl_idname, text="Fix UV Channels", icon='TOOL_SETTINGS')
    op.scope = 'SELECTED'
    row = uv_col.row(align=True)
    row.operator(PM_OT_VR_ActivateUVMap.bl_idname, text="Activate UVMap", icon='GROUP_UVS')
    row.operator(PM_OT_VR_ActivateSimpleBake.bl_idname, text="Activate SimpleBake", icon='GROUP_UVS')

    selection_col = box.column(align=True)
    selection_col.label(text="Scene Selection:")
    selection_col.operator(
        PM_OT_VR_SelectDisplacementObjects.bl_idname,
        text="Select Displacement",
        icon='MOD_DISPLACE',
    )

    box.separator()

    texture_file_col = box.column(align=True)
    texture_file_col.label(text="Texture Files:")
    texture_file_col.operator(
        PM_OT_VR_RelinkSelectedTexturesFromFolder.bl_idname,
        text="Relink Selected Textures",
        icon='FILE_FOLDER',
    )
    texture_file_col.operator(
        PM_OT_VR_ExternalizeSelectedTextures.bl_idname,
        text="Unpack Selected Textures",
        icon='PACKAGE',
    )


classes = (
    PMVR_AuditResultItem,
    PM_OT_VR_AuditObjectPrep,
    PM_OT_VR_AuditNavigate,
    PM_OT_VR_AuditSelectActive,
    PM_OT_VR_AuditSelectAll,
    PM_OT_VR_AuditRecheckActive,
    PM_OT_VR_AuditClear,
    PM_OT_VR_CheckNames,
    PM_OT_VR_SyncNamesFromObjects,
    PM_OT_VR_CheckUVChannels,
    PM_OT_VR_FixUVChannels,
    PM_OT_VR_EnsureUVChannelsCompatibility,
    PM_OT_VR_RelinkSelectedTexturesFromFolder,
    PM_OT_VR_ExternalizeSelectedTextures,
    PM_OT_VR_AddTextureSuffix,
    PM_OT_VR_RemoveTextureSuffix,
    PM_OT_VR_ActivateUVMap,
    PM_OT_VR_ActivateSimpleBake,
    PM_OT_VR_SelectDisplacementObjects,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # Legacy texel-label settings intentionally remain registered even though
    # their controls are no longer drawn in the Optimize panel.
    bpy.types.Scene.pm_vr_target_td = bpy.props.FloatProperty(
        name="Target Texel Density",
        description="Pipeline texel density target in pixels per centimeter",
        default=TARGET_TD_PX_PER_CM,
        min=0.1,
        soft_min=1.0,
        soft_max=20.0,
        precision=1,
        unit='NONE',
    )
    bpy.types.Scene.pm_vr_use_texture_prefix = bpy.props.BoolProperty(
        name="Use Prefix",
        description="Put 1K/2K/4K before the object name instead of after it",
        default=False,
    )
    bpy.types.Scene.pm_vr_audit_results = bpy.props.CollectionProperty(
        type=PMVR_AuditResultItem,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_audit_index = bpy.props.IntProperty(
        default=0,
        min=0,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_audit_checked_count = bpy.props.IntProperty(
        default=0,
        min=0,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_audit_issue_count = bpy.props.IntProperty(
        default=0,
        min=0,
        options={'SKIP_SAVE'},
    )
    bpy.types.Scene.pm_vr_audit_scope_label = bpy.props.StringProperty(
        options={'SKIP_SAVE'},
    )
    if _migrate_target_td_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_migrate_target_td_load_post)
    _migrate_target_td_defaults()


def unregister():
    if _migrate_target_td_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_migrate_target_td_load_post)
    viewport_notice.shutdown()
    for property_name in (
        "pm_vr_audit_scope_label",
        "pm_vr_audit_issue_count",
        "pm_vr_audit_checked_count",
        "pm_vr_audit_index",
        "pm_vr_audit_results",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    if hasattr(bpy.types.Scene, "pm_vr_use_texture_prefix"):
        del bpy.types.Scene.pm_vr_use_texture_prefix
    if hasattr(bpy.types.Scene, "pm_vr_target_td"):
        del bpy.types.Scene.pm_vr_target_td
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
