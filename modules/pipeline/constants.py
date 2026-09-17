"""Constants shared by the PM VR production pipeline."""

SCHEMA_VERSION = 1
BAKE_UV_NAME = "SimpleBake"
PRIMARY_UV_NAME = "UVMap"
GENERATED_COLLECTION = "PMVR_GENERATED"
WORK_COLLECTION = "PMVR_WORK"

PROFILE_ITEMS = (
    ('BEAUTY_SCENE', "Scene / Beauty", "Bake a simple unlit-looking Beauty material"),
    ('BEAUTY_PBR', "PBR / Beauty", "Bake Base Color and preserve original PBR channels"),
    ('BEAUTY_TRANSLUCENT', "Translucent / Beauty", "Bake Base Color and preserve original Alpha"),
    ('EXPORT_ORIGINAL', "Export Original", "Export source objects without baking"),
)

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
TAG_MODE = "pmvr_mode"
TAG_STATE = "pmvr_state"
TAG_SCHEMA = "pmvr_schema"
TAG_MATERIAL_SLOT = "pmvr_material_slot"
