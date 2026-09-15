"""Shared constants for PM Lightmap Baker."""

UI_CATEGORY = "LIGHTMAP_BAKER"

BAKER_VERSION = 1
BAKE_UV_NAME = "SimpleBake"
OUTPUT_COLLECTION_NAME = "PM_Lightmap_Bakes"
RESULT_SUFFIX = "_LM"

RESOLUTION_VALUES = (256, 512, 1024, 2048, 4096, 8192)
RESOLUTION_ITEMS = tuple(
    (str(value), f"{value} × {value}", f"Bake a {value} × {value} lightmap")
    for value in RESOLUTION_VALUES
)

TAG_GENERATED = "pm_lightmap_generated"
TAG_SOURCE_NAME = "pm_lightmap_source"
TAG_SOURCE_REF = "pm_lightmap_source_object"
TAG_ASSET_TYPE = "pm_lightmap_asset_type"
TAG_VERSION = "pm_lightmap_version"
TAG_OPERATION = "pm_lightmap_operation"
TAG_EXPORT_PATH = "pm_lightmap_export_path"

ASSET_OBJECT = "OBJECT"
ASSET_MESH = "MESH"
ASSET_MATERIAL = "MATERIAL"
ASSET_IMAGE = "IMAGE"

NODE_UV_NAME = "PM Lightmap UV"
NODE_IMAGE_NAME = "PM Lightmap Image"
NODE_MIX_NAME = "PM Lightmap Multiply"

LOG_PREFIX = "[PM Lightmap]"
