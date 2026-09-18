"""Shared, read-only scene preparation checks.

Keep these predicates independent from UI and operators so Audit and viewport
diagnostics cannot silently disagree about the same scene state.
"""

import numpy as np

PRIMARY_UV_NAME = "UVMap"
BAKE_UV_NAME = "SimpleBake"
SCALE_TOLERANCE = 1.0e-4
CM_PER_BLEND_UNIT = 100.0
TARGET_TD_PX_PER_CM = 5.0


def has_pipeline_uvs(mesh):
    layers = mesh.uv_layers if mesh else ()
    return bool(
        len(layers) >= 2
        and layers[0].name == PRIMARY_UV_NAME
        and layers[1].name == BAKE_UV_NAME
    )


def has_applied_scale(obj):
    return all(
        abs(component - 1.0) <= SCALE_TOLERANCE
        for component in obj.scale
    )


def get_target_td(context):
    return getattr(
        context.scene,
        "pm_vr_target_td",
        TARGET_TD_PX_PER_CM,
    )


def measure_texel_areas(context, obj):
    """Return evaluated world area in cm² and area of UV channel two."""
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    try:
        if not mesh or not mesh.polygons:
            return 0.0, 0.0, "no polygons"

        uv_layer = mesh.uv_layers[1] if len(mesh.uv_layers) >= 2 else None
        if uv_layer is None:
            return 0.0, 0.0, "second UV channel not found"
        if uv_layer.name != BAKE_UV_NAME:
            return (
                0.0,
                0.0,
                f'second UV channel is "{uv_layer.name}"; '
                f'expected "{BAKE_UV_NAME}"',
            )

        mesh.calc_loop_triangles()
        triangle_count = len(mesh.loop_triangles)
        if not triangle_count:
            return 0.0, 0.0, "no triangles"

        positions = np.empty((len(mesh.vertices), 3), dtype=np.float64)
        mesh.vertices.foreach_get("co", positions.ravel())
        transform = np.asarray(evaluated.matrix_world, dtype=np.float64)
        positions = positions @ transform[:3, :3].T + transform[:3, 3]

        vertex_indices = np.empty((triangle_count, 3), dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", vertex_indices.ravel())
        triangles = positions[vertex_indices]
        cross_products = np.cross(
            triangles[:, 1] - triangles[:, 0],
            triangles[:, 2] - triangles[:, 0],
        )
        world_area_bu2 = float(
            np.linalg.norm(cross_products, axis=1).sum() * 0.5
        )

        uv_coordinates = np.empty((len(mesh.loops), 2), dtype=np.float64)
        uv_layer.data.foreach_get("uv", uv_coordinates.ravel())
        loop_indices = np.empty((triangle_count, 3), dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", loop_indices.ravel())
        uv_triangles = uv_coordinates[loop_indices]
        first = uv_triangles[:, 1] - uv_triangles[:, 0]
        second = uv_triangles[:, 2] - uv_triangles[:, 0]
        uv_area = float(
            np.abs(
                (first[:, 0] * second[:, 1])
                - (first[:, 1] * second[:, 0])
            ).sum() * 0.5
        )

        scene_scale = max(
            1.0e-9,
            float(context.scene.unit_settings.scale_length),
        )
        cm_per_blend_unit = CM_PER_BLEND_UNIT * scene_scale
        return world_area_bu2 * (cm_per_blend_unit ** 2), uv_area, None
    finally:
        evaluated.to_mesh_clear()
