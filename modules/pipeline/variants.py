"""Material variants of a bake unit.

Mac contract (Asset Manager, LevelManifest.json `materialVariants`): each
variant is its own USDZ that holds only the object, named as in the scene,
at Variants/<Object>_<Variant>.usdz, with ..._Evening.usdz for the Evening
look. The default variant is the object in the main scene and has no file.
Only Unlit objects of the main scene; the app swaps the base colour texture
only, so every variant is baked with light, per look, on the unit's UV.
Optional: Variants/<Object>_<Variant>_swatch.jpg, made by hand (export
never writes or replaces swatches; the default one, <Object>_swatch.jpg,
goes into the manifest when present), and an Empty
named VariantMarker in the file for the button.
"""

import os
import re
from pathlib import Path

import bpy
from . import looks

from .bake_scene import same_structure
from .identity import find_layer, new_id, safe_stem, unit_members
from .setup_ops import active_unit


FOLDER = "Variants"
MANIFEST = "materialVariants.json"
MARKER_NAME = "VariantMarker"


def usd_name(name):
    """The prim name Blender's USD export gives an object: characters other
    than letters, digits and _ become _, and a leading digit gets a _ in
    front (4K_Leaf.001 -> _4K_Leaf_001, as seen in UniPlace exports)."""
    name = re.sub(r"[^\w]", "_", name or "") or "_"
    if name[0].isdigit():
        name = "_" + name
    return name


def _slug(text):
    return re.sub(r"[^0-9a-z]+", "_", text.casefold()).strip("_") or "variant"


def variant_stem(variant):
    return safe_stem(variant.title.strip())


def find_variant(unit, variant_id):
    return next((variant for variant in unit.variants if variant.variant_id == variant_id), None)


def variant_problem(project, unit):
    """Why the unit's variants cannot bake or export; "" when they can."""
    layer = find_layer(project, unit.render_layer_id)
    if not layer or layer.layer_type != 'UNLIT':
        return "variants need an Unlit layer: the app swaps the baked colour of the main scene only"
    members = unit_members(unit.unit_id)
    if len(members) != 1:
        return f"variants need a unit with one object; this one has {len(members)}"
    base = unit.variant_material
    if not base:
        return 'choose the material the variants replace ("Changes")'
    if all(slot.material != base for slot in members[0].material_slots):
        return f'"{members[0].name}" does not use "{base.name}"'
    if not unit.variant_default_title.strip():
        return "name the default variant"
    titles = [unit.variant_default_title.strip().casefold()]
    for variant in unit.variants:
        title = variant.title.strip()
        if not title:
            return "every variant needs a name"
        if not variant.material:
            return f'variant "{title}" has no material'
        titles.append(title.casefold())
    stems = [variant_stem(variant).casefold() for variant in unit.variants]
    if len(set(titles)) != len(titles) or len(set(stems)) != len(stems):
        return "variant names must differ"
    return ""


def variant_file(variant, state):
    return looks.variant_file(variant, state)


def set_result(variant, state, path, signature):
    looks.set_variant_result(variant, state, path, signature)


def variant_status(unit, variant, state):
    """'Ready', 'Rebake' (the unit changed since) or '' (not baked)."""
    path = variant_file(variant, state)
    if not path or not os.path.exists(bpy.path.abspath(path)):
        return ""
    signature = looks.variant_signature(variant, state)
    unit_signature = looks.result_value(unit, state, 'signature')
    return "Ready" if signature and unit_signature and same_structure(signature, unit_signature) else "Rebake"


def staging_folder(project):
    """Variants/ inside the USDZ output folder."""
    folder = os.path.join(bpy.path.abspath(project.usdz_output_directory), FOLDER)
    os.makedirs(folder, exist_ok=True)
    return folder


def model_path(entity, variant, state, project=None):
    project = project or bpy.context.scene.pm_vr_project
    suffix = looks.suffix(project, state)
    return f"{FOLDER}/{entity}_{variant_stem(variant)}{suffix}.usdz"


def swatch_path(entity, variant=None):
    return f"{FOLDER}/{entity}_{variant_stem(variant)}_swatch.jpg" if variant else f"{FOLDER}/{entity}_swatch.jpg"


def manifest_entry(unit, entity, staging, project=None):
    """The materialVariants record of one object, or None when fewer than
    two options exist (the Day file of a variant is its model)."""
    options = [{"id": "default", "title": unit.variant_default_title.strip()}]
    if os.path.exists(os.path.join(staging, swatch_path(entity))):
        options[0]["swatch"] = swatch_path(entity)
    ids = {"default"}
    project = project or bpy.context.scene.pm_vr_project
    state = looks.default_id(project)
    for variant in unit.variants:
        model = model_path(entity, variant, state, project)
        if variant_status(unit, variant, state) != 'Ready' or not os.path.exists(os.path.join(staging, model)):
            continue
        option_id = _slug(variant.title)
        while option_id in ids:
            option_id += "_2"
        ids.add(option_id)
        options.append({"id": option_id, "title": variant.title.strip(), "model": model})
    if len(options) < 2:
        return None
    return {"id": _slug(unit.display_name), "title": unit.variant_group_title.strip() or unit.display_name,
            "entity": entity, "options": options}


def write_manifest(staging, entries):
    """Variants/materialVariants.json: the block for LevelManifest.json."""
    ids = set()
    for entry in entries:
        while entry["id"] in ids:
            entry["id"] += "_2"
        ids.add(entry["id"])
    path = os.path.join(staging, FOLDER, MANIFEST)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from .export_description import _atomic_json
    _atomic_json(Path(path), {"materialVariants": entries})
    return path


class PMVR_OT_AddBakeVariant(bpy.types.Operator):
    bl_idname = "pmvr.add_bake_variant"
    bl_label = "Add Variant"
    bl_description = (
        "Add a material variant the app can switch this object to: baked "
        "with the unit, exported to Variants/ (Unlit units with one object)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(active_unit(project) and not project.operation_running)

    def execute(self, context):
        project = context.scene.pm_vr_project
        unit = active_unit(project)
        layer = find_layer(project, unit.render_layer_id)
        members = unit_members(unit.unit_id)
        if not layer or layer.layer_type != 'UNLIT':
            self.report({'ERROR'}, "Variants need an Unlit layer: the app swaps the baked colour of the main scene only")
            return {'CANCELLED'}
        if len(members) != 1:
            self.report({'ERROR'}, f"Variants need a unit with one object; this one has {len(members)}")
            return {'CANCELLED'}
        if not unit.variant_material:
            unit.variant_material = next((slot.material for slot in members[0].material_slots if slot.material), None)
        variant = unit.variants.add()
        variant.variant_id = new_id()
        variant.title = f"Variant {len(unit.variants)}"
        return {'FINISHED'}


class PMVR_OT_RemoveBakeVariant(bpy.types.Operator):
    bl_idname = "pmvr.remove_bake_variant"
    bl_label = "Remove Variant"
    bl_description = "Remove this variant (its baked files stay on disk)"
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        project = context.scene.pm_vr_project
        return bool(active_unit(project) and not project.operation_running)

    def execute(self, context):
        unit = active_unit(context.scene.pm_vr_project)
        if 0 <= self.index < len(unit.variants):
            unit.variants.remove(self.index)
        return {'FINISHED'}


CLASSES = (PMVR_OT_AddBakeVariant, PMVR_OT_RemoveBakeVariant)
