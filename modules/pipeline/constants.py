"""Constants shared by the PM VR production pipeline."""

from ..scene_diagnostics import BAKE_UV_NAME, PRIMARY_UV_NAME

SCHEMA_VERSION = 1
GENERATED_COLLECTION = "PMVR_GENERATED"
WORK_COLLECTION = "PMVR_WORK"

LAYER_TYPE_ITEMS = (
    ('UNLIT', "Unlit", "Fully baked unlit-looking renderable content"),
    ('PBR', "PBR", "Baked Base Color with preserved PBR channels"),
    ('ALPHA', "Alpha", "Renderable content using an alpha channel, such as foliage"),
    ('TRANSLUCENT', "Translucent", "Light-transmitting fabrics and curtains"),
    ('GLASS', "Glass", "Original transparent or refractive geometry"),
    ('EMISSIVE', "Emissive", "Original emissive renderable content"),
    ('RUNTIME', "Runtime", "Runtime-driven FX, sound points, probes, UI, navigation, and collisions"),
)

UNLIT_LAYER_TYPES = frozenset({'UNLIT', 'TRANSLUCENT'})
BAKE_LAYER_TYPES = frozenset((*UNLIT_LAYER_TYPES, 'PBR', 'ALPHA'))

ROLE_ITEMS = (
    ('UNASSIGNED', "Unassigned", "Object has not been assigned a pipeline role"),
    ('BAKE', "Bake", "Object is generated from a Beauty bake unit"),
    ('EXPORT_ORIGINAL', "Export Original", "Export the source object unchanged"),
)

RESOLUTION_ITEMS = tuple(
    (str(value), f"{value} × {value}", f"Bake a {value} × {value} shared atlas")
    for value in (256, 512, 1024, 2048, 4096, 8192)
)

STATE_ITEMS = (
    ('DAY', "Day", "Use Day lighting collection and world"),
    ('EVENING', "Evening", "Use Evening lighting collection and world"),
)

MODE_ITEMS = (
    ('BEAUTY', "Beauty", "Production Beauty bake used by semantic export"),
    ('LIGHTMAP', "Lightmap", "Classic Blender-side diffuse lightmap"),
)

DEBUG_MODE_ITEMS = (
    ('BAKE_STATUS', "Bake Status", "Show missing, existing, and session bake results"),
    ('RENDER_LAYERS', "Render Layers", "Color objects by semantic render layer"),
    ('BAKE_UNITS', "Bake Units", "Color objects by their bake unit"),
    ('UV_HEALTH', "UV Health", "Show invalid and missing pipeline UV channels"),
    ('TEXEL_DENSITY', "Texel Density", "Show SimpleBake texel density against the project target"),
    ('UV_CHECKER', "Checker", "Preview the PM VR checker through a selected UV channel"),
    ('SCALE_CHECK', "Scale Check", "Highlight objects with unapplied scale"),
    ('LINKED_MESHES', "Linked Meshes", "Highlight objects that share mesh data"),
)

PIPELINE_DEBUG_MODES = frozenset({'BAKE_STATUS', 'RENDER_LAYERS', 'BAKE_UNITS'})
GENERAL_DEBUG_MODES = frozenset(
    identifier
    for identifier, _label, _description in DEBUG_MODE_ITEMS
    if identifier not in PIPELINE_DEBUG_MODES
)

DEBUG_OVERLAY_ITEMS = (
    ('OFF', "Off", "Disable the PM VR viewport overlay"),
    *DEBUG_MODE_ITEMS,
)

LAYER_COLOR_PALETTE = (
    (0.74, 0.08, 0.92),
    (0.04, 0.86, 0.24),
    (0.12, 0.22, 0.95),
    (0.95, 0.08, 0.05),
    (0.98, 0.55, 0.05),
    (0.02, 0.72, 0.82),
    (0.96, 0.20, 0.55),
    (0.48, 0.76, 0.08),
)

TAG_GENERATED = "pmvr_generated"
TAG_SOURCE_ID = "pmvr_source_id"
TAG_UNIT_ID = "pmvr_unit_id"
TAG_LAYER_ID = "pmvr_layer_id"
TAG_LAYER_TYPE = "pmvr_layer_type"
TAG_MODE = "pmvr_mode"
TAG_STATE = "pmvr_state"
TAG_SCHEMA = "pmvr_schema"
TAG_MATERIAL_SLOT = "pmvr_material_slot"
TAG_NAME = "pmvr_name"
