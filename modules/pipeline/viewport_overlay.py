"""Non-destructive transparent viewport overlays for pipeline state."""

from collections import Counter
import colorsys
import hashlib
import math

import blf
import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from .. import checker_preview
from ..scene_diagnostics import (
    get_target_td,
    has_applied_scale,
    has_pipeline_uvs,
    measure_texel_areas,
)
from .constants import (
    BAKE_UV_NAME,
    BAKE_LAYER_TYPES,
    GENERAL_DEBUG_MODES,
    LAYER_COLOR_PALETTE,
    PIPELINE_DEBUG_MODES,
    PRIMARY_UV_NAME,
    TAG_GENERATED,
)


STATUS_COLORS = {
    'MISSING': (0.96, 0.035, 0.025),
    'EXISTING': (0.04, 0.78, 0.13),
    'SESSION': (0.01, 0.92, 0.58),
    'NO_BAKE': (0.30, 0.34, 0.38),
    'UNASSIGNED': (1.00, 0.38, 0.025),
    'UV_INVALID': (0.96, 0.035, 0.025),
    'UV_VALID': (0.04, 0.78, 0.13),
    'UV_NOT_REQUIRED': (0.30, 0.34, 0.38),
    'TD_BAD': (0.96, 0.05, 0.02),
    'TD_ACCEPTABLE': (1.00, 0.72, 0.02),
    'TD_GREAT': (0.04, 0.85, 0.13),
    'TD_INVALID': (1.00, 0.02, 0.55),
    'TD_NOT_REQUIRED': (0.30, 0.34, 0.38),
    'CHECKER': (1.00, 1.00, 1.00),
    'CHECKER_MISSING': (1.00, 0.02, 0.55),
    'SCALE_INVALID': (0.96, 0.035, 0.025),
    'SCALE_OK': (0.04, 0.78, 0.13),
    'UNIT_UNASSIGNED': (0.30, 0.34, 0.38),
    'LINKED_INVALID': (0.96, 0.035, 0.025),
    'LINKED_OK': (0.04, 0.78, 0.13),
}
STATUS_ALPHA = {
    'MISSING': 1.00,
    'EXISTING': 0.45,
    'SESSION': 1.00,
    'NO_BAKE': 0.16,
    'UNASSIGNED': 0.80,
    'UV_INVALID': 1.00,
    'UV_VALID': 0.35,
    'UV_NOT_REQUIRED': 0.12,
    'TD_BAD': 1.00,
    'TD_ACCEPTABLE': 0.90,
    'TD_GREAT': 0.75,
    'TD_INVALID': 1.00,
    'TD_NOT_REQUIRED': 0.12,
    'CHECKER': 1.00,
    'CHECKER_MISSING': 1.00,
    'SCALE_INVALID': 1.00,
    'SCALE_OK': 0.0,
    'UNIT_UNASSIGNED': 0.12,
    'LINKED_INVALID': 1.00,
    'LINKED_OK': 0.0,
}
STATUS_LABELS = {
    'MISSING': "Missing",
    'EXISTING': "Existing",
    'SESSION': "This Session",
    'NO_BAKE': "No Bake",
    'UNASSIGNED': "Unassigned",
    'UV_INVALID': "Invalid / Missing",
    'UV_VALID': "Valid",
    'UV_NOT_REQUIRED': "Not Required",
    'TD_BAD': "Bad",
    'TD_ACCEPTABLE': "Acceptable",
    'TD_GREAT': "Great",
    'TD_INVALID': "Invalid UV / Geometry",
    'TD_NOT_REQUIRED': "Not Required",
    'CHECKER': "Checker",
    'CHECKER_MISSING': "Selected UV Missing",
    'SCALE_INVALID': "Unapplied Scale",
    'SCALE_OK': "Applied",
    'UNIT_UNASSIGNED': "No Bake Unit",
    'LINKED_INVALID': "Shared Mesh Data",
    'LINKED_OK': "Single User",
}

_view_handle = None
_pixel_handle = None
_shader = None
_surface_shader_instance = None
_checker_shader_instance = None
_checker_texture_instance = None
_surface_batch_cache = {}
_checker_batch_cache = {}
_texel_area_cache = {}
_session_bakes = set()
_last_draw_error = ""
_last_legend_error = ""
_uv_message_owner = object()


def tag_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def invalidate_texel_cache():
    _texel_area_cache.clear()
    tag_redraw()


def ensure_layer_colors(project):
    for index, layer in enumerate(project.render_layers):
        if layer.viewport_color_initialized:
            continue
        layer.viewport_color = LAYER_COLOR_PALETTE[
            index % len(LAYER_COLOR_PALETTE)
        ]
        layer.viewport_color_initialized = True


def _session_key(project, unit, state, mode):
    return (project.project_id, unit.unit_id, state, mode)


def mark_baked(project, unit, state, mode):
    _session_bakes.add(_session_key(project, unit, state, mode))
    tag_redraw()


def _is_session_bake(project, unit, state, mode):
    return _session_key(project, unit, state, mode) in _session_bakes


def _has_result(unit, state, mode):
    if mode == 'LIGHTMAP':
        status = (
            unit.day_lightmap_status
            if state == 'DAY'
            else unit.evening_lightmap_status
        )
        image_name = (
            unit.day_lightmap_image
            if state == 'DAY'
            else unit.evening_lightmap_image
        )
    else:
        status = unit.day_status if state == 'DAY' else unit.evening_status
        image_name = (
            unit.day_beauty_image
            if state == 'DAY'
            else unit.evening_beauty_image
        )
    return bool(status == "Ready" and image_name and bpy.data.images.get(image_name))


def _source_objects(scene, project):
    if project.overlay_mode in GENERAL_DEBUG_MODES:
        return [
            obj for obj in scene.objects
            if obj.type == 'MESH' and obj.data and not obj.get(TAG_GENERATED)
        ]

    objects = set()
    root = project.source_root_collection
    if root:
        objects.update(root.all_objects)
    objects.update(
        obj for obj in scene.objects
        if getattr(
            getattr(obj, "pm_vr_pipeline", None),
            "is_registered_source",
            False,
        )
    )
    return [
        obj for obj in objects
        if obj.type == 'MESH' and obj.data and not obj.get(TAG_GENERATED)
    ]


def _is_visible(obj, context):
    view_layer = context.view_layer
    if view_layer.objects.get(obj.name) != obj:
        return False
    try:
        return obj.visible_get(view_layer=view_layer, viewport=context.space_data)
    except TypeError:
        return obj.visible_get(view_layer=view_layer)
    except (ReferenceError, RuntimeError):
        return False


def _unit_color(unit_id):
    digest = hashlib.sha1(unit_id.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    saturation = 0.58 + (digest[2] / 255.0) * 0.22
    value = 0.82 + (digest[3] / 255.0) * 0.16
    return colorsys.hsv_to_rgb(hue, saturation, value)


def _object_resolution(project, metadata, layer, units):
    unit = units.get(metadata.bake_unit_id)
    if (
        metadata.is_registered_source
        and layer
        and metadata.processing_role == 'BAKE'
        and unit
        and unit.render_layer_id == layer.layer_id
    ):
        return int(unit.resolution)
    return int(project.default_unit_resolution)


def _is_export_original(metadata, layer):
    return bool(
        metadata.processing_role == 'EXPORT_ORIGINAL'
        or (layer and layer.layer_type not in BAKE_LAYER_TYPES)
    )


def _active_texel_summary(context, project):
    obj = context.active_object
    if not obj or obj.type != 'MESH' or not obj.data or obj.get(TAG_GENERATED):
        return ""
    metadata = getattr(obj, "pm_vr_pipeline", None)
    if metadata is None:
        return ""
    layers = {layer.layer_id: layer for layer in project.render_layers}
    units = {unit.unit_id: unit for unit in project.bake_units}
    resolution = _object_resolution(
        project,
        metadata,
        layers.get(metadata.render_layer_id),
        units,
    )
    mesh_area_cm2, uv_area, error = _texel_areas(context, obj)
    if error or mesh_area_cm2 <= 0.0 or uv_area <= 0.0:
        return "Active invalid"
    actual = resolution * math.sqrt(uv_area / mesh_area_cm2)
    return f"Active {actual:.2f} px/cm @ {resolution}"


def _texel_areas(context, obj):
    key = obj.as_pointer()
    matrix_signature = tuple(
        component
        for row in obj.matrix_world
        for component in row
    )
    signature = (
        obj.data.as_pointer(),
        tuple(layer.name for layer in obj.data.uv_layers),
        matrix_signature,
        float(context.scene.unit_settings.scale_length),
    )
    cached = _texel_area_cache.get(key)
    if cached and cached[0] == signature:
        return cached[1:]

    mesh_area_cm2, uv_area, error = measure_texel_areas(context, obj)
    _texel_area_cache[key] = (
        signature,
        mesh_area_cm2,
        uv_area,
        error,
    )
    return mesh_area_cm2, uv_area, error


def _classify(context, project, obj, layers, units):
    metadata = obj.pm_vr_pipeline
    layer = layers.get(metadata.render_layer_id)

    if project.overlay_mode == 'SCALE_CHECK':
        valid = has_applied_scale(obj)
        token = 'SCALE_OK' if valid else 'SCALE_INVALID'
        return token, STATUS_COLORS[token]

    if project.overlay_mode == 'LINKED_MESHES':
        token = 'LINKED_INVALID' if obj.data.users > 1 else 'LINKED_OK'
        return token, STATUS_COLORS[token]

    if project.overlay_mode == 'UV_HEALTH':
        if _is_export_original(metadata, layer):
            return 'UV_NOT_REQUIRED', STATUS_COLORS['UV_NOT_REQUIRED']
        valid = has_pipeline_uvs(obj.data)
        token = 'UV_VALID' if valid else 'UV_INVALID'
        return token, STATUS_COLORS[token]

    if project.overlay_mode == 'TEXEL_DENSITY':
        if _is_export_original(metadata, layer):
            return 'TD_NOT_REQUIRED', STATUS_COLORS['TD_NOT_REQUIRED']
        resolution = _object_resolution(project, metadata, layer, units)
        mesh_area_cm2, uv_area, error = _texel_areas(context, obj)
        if error or mesh_area_cm2 <= 0.0 or uv_area <= 0.0:
            return 'TD_INVALID', STATUS_COLORS['TD_INVALID']
        target = max(1.0e-6, float(get_target_td(context)))
        actual = resolution * math.sqrt(uv_area / mesh_area_cm2)
        ratio = actual / target
        # Density quality is monotonic for production: exceeding the minimum
        # target remains good. Memory efficiency is a separate concern and
        # must not make a denser object look worse than a sparser one.
        if ratio >= 1.0:
            token = 'TD_GREAT'
        elif ratio >= 0.5:
            token = 'TD_ACCEPTABLE'
        else:
            token = 'TD_BAD'
        return token, STATUS_COLORS[token]

    if project.overlay_mode == 'UV_CHECKER':
        uv_index = 0 if project.debug_checker_uv == 'PRIMARY' else 1
        if len(obj.data.uv_layers) <= uv_index:
            return 'CHECKER_MISSING', STATUS_COLORS['CHECKER_MISSING']
        return 'CHECKER', STATUS_COLORS['CHECKER']

    if not metadata.is_registered_source or not layer:
        return 'UNASSIGNED', STATUS_COLORS['UNASSIGNED']

    if project.overlay_mode == 'RENDER_LAYERS':
        return f"LAYER:{layer.layer_id}", tuple(layer.viewport_color)

    if project.overlay_mode == 'BAKE_UNITS':
        unit = units.get(metadata.bake_unit_id)
        if metadata.processing_role != 'BAKE' or not unit:
            return 'UNIT_UNASSIGNED', STATUS_COLORS['UNIT_UNASSIGNED']
        return f"UNIT:{unit.unit_id}", _unit_color(unit.unit_id)

    if _is_export_original(metadata, layer):
        return 'NO_BAKE', STATUS_COLORS['NO_BAKE']
    if metadata.processing_role != 'BAKE':
        return 'UNASSIGNED', STATUS_COLORS['UNASSIGNED']
    unit = units.get(metadata.bake_unit_id)
    if not unit or unit.render_layer_id != layer.layer_id:
        return 'UNASSIGNED', STATUS_COLORS['UNASSIGNED']

    state = project.active_lighting_state
    mode = project.bake_mode
    if not _has_result(unit, state, mode):
        return 'MISSING', STATUS_COLORS['MISSING']
    if _is_session_bake(project, unit, state, mode):
        return 'SESSION', STATUS_COLORS['SESSION']
    return 'EXISTING', STATUS_COLORS['EXISTING']


def _collect_items(context):
    scene = context.scene
    project = getattr(scene, "pm_vr_project", None)
    if not project or project.overlay_mode == 'OFF':
        return [], Counter()
    if project.overlay_mode in PIPELINE_DEBUG_MODES and not project.initialized:
        return [], Counter()
    layers = {layer.layer_id: layer for layer in project.render_layers}
    units = {unit.unit_id: unit for unit in project.bake_units}
    items = []
    counts = Counter()
    for obj in _source_objects(scene, project):
        if not _is_visible(obj, context):
            continue
        token, color = _classify(context, project, obj, layers, units)
        counts[token] += 1
        if (
            token in {'UNASSIGNED', 'UNIT_UNASSIGNED'}
            and not project.overlay_show_unassigned
        ):
            continue
        if token in {'SCALE_OK', 'LINKED_OK'}:
            continue
        alpha_scale = STATUS_ALPHA.get(token, 1.0)
        base_alpha = 0.05 + (project.overlay_opacity * 0.35)
        alpha = min(1.0, base_alpha * alpha_scale)
        items.append((obj, (*color, alpha), token))
    return items, counts


def _uniform_shader():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _surface_shader():
    global _surface_shader_instance
    if _surface_shader_instance is None:
        shader_info = gpu.types.GPUShaderCreateInfo()
        shader_info.push_constant('MAT4', "modelViewProjectionMatrix")
        shader_info.push_constant('VEC4', "color")
        shader_info.vertex_in(0, 'VEC3', "pos")
        shader_info.fragment_out(0, 'VEC4', "fragColor")
        shader_info.vertex_source("""
        void main()
        {
            gl_Position = modelViewProjectionMatrix * vec4(pos, 1.0);
        }
        """)
        shader_info.fragment_source("""
        void main()
        {
            fragColor = color;
            // Four steps of a typical 24-bit viewport depth buffer. This is
            // enough to resolve the coincident source surface without moving
            // vertices or noticeably overtaking nearby geometry.
            gl_FragDepth = max(0.0, gl_FragCoord.z - 0.00000024);
        }
        """)
        _surface_shader_instance = gpu.shader.create_from_info(shader_info)
    return _surface_shader_instance


def _checker_shader():
    global _checker_shader_instance
    if _checker_shader_instance is None:
        interface = gpu.types.GPUStageInterfaceInfo("pmvr_checker_interface")
        interface.smooth('VEC2', "checkerUV")
        shader_info = gpu.types.GPUShaderCreateInfo()
        shader_info.push_constant('MAT4', "modelViewProjectionMatrix")
        shader_info.push_constant('FLOAT', "tiling")
        shader_info.push_constant('FLOAT', "opacity")
        shader_info.push_constant('FLOAT', "bakeResolution")
        shader_info.sampler(0, 'FLOAT_2D', "checkerTexture")
        shader_info.vertex_in(0, 'VEC3', "pos")
        shader_info.vertex_in(1, 'VEC2', "uv")
        shader_info.vertex_out(interface)
        shader_info.fragment_out(0, 'VEC4', "fragColor")
        shader_info.vertex_source("""
        void main()
        {
            checkerUV = uv;
            gl_Position = modelViewProjectionMatrix * vec4(pos, 1.0);
        }
        """)
        shader_info.fragment_source("""
        void main()
        {
            vec2 pixelUV = (
                floor(checkerUV * bakeResolution) + vec2(0.5)
            ) / bakeResolution;
            vec4 checker = texture(checkerTexture, fract(pixelUV * tiling));
            fragColor = vec4(checker.rgb, opacity * checker.a);
            gl_FragDepth = max(0.0, gl_FragCoord.z - 0.00000024);
        }
        """)
        _checker_shader_instance = gpu.shader.create_from_info(shader_info)
    return _checker_shader_instance


def prepare_checker():
    global _checker_texture_instance
    if _checker_texture_instance is None:
        image = checker_preview.get_checker_image()
        _checker_texture_instance = gpu.texture.from_image(image)
    return _checker_texture_instance


def _surface_batch(obj, depsgraph):
    key = obj.as_pointer()
    cached = _surface_batch_cache.get(key)
    if cached:
        return cached["batch"]

    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(
        preserve_all_data_layers=False,
        depsgraph=depsgraph,
    )
    if not mesh:
        return None
    try:
        mesh.calc_loop_triangles()
        if not mesh.vertices or not mesh.loop_triangles:
            return None
        positions = np.empty((len(mesh.vertices), 3), dtype=np.float32)
        mesh.vertices.foreach_get("co", positions.ravel())
        indices = np.empty((len(mesh.loop_triangles), 3), dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", indices.ravel())
        batch = batch_for_shader(
            _surface_shader(),
            'TRIS',
            {"pos": positions},
            indices=indices,
        )
        _surface_batch_cache[key] = {
            "batch": batch,
            "mesh_pointer": obj.data.as_pointer(),
        }
        return batch
    finally:
        evaluated.to_mesh_clear()


def _checker_batch(obj, depsgraph, uv_index):
    key = (obj.as_pointer(), uv_index)
    cached = _checker_batch_cache.get(key)
    if cached:
        return cached["batch"]

    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    if not mesh:
        return None
    try:
        mesh.calc_loop_triangles()
        if (
            not mesh.vertices
            or not mesh.loop_triangles
            or len(mesh.uv_layers) <= uv_index
        ):
            return None

        positions = np.empty((len(mesh.vertices), 3), dtype=np.float32)
        mesh.vertices.foreach_get("co", positions.ravel())
        vertex_indices = np.empty(
            (len(mesh.loop_triangles), 3),
            dtype=np.int32,
        )
        mesh.loop_triangles.foreach_get("vertices", vertex_indices.ravel())

        uv_coordinates = np.empty((len(mesh.loops), 2), dtype=np.float32)
        mesh.uv_layers[uv_index].data.foreach_get("uv", uv_coordinates.ravel())
        loop_indices = np.empty(
            (len(mesh.loop_triangles), 3),
            dtype=np.int32,
        )
        mesh.loop_triangles.foreach_get("loops", loop_indices.ravel())

        batch = batch_for_shader(
            _checker_shader(),
            'TRIS',
            {
                "pos": positions[vertex_indices].reshape((-1, 3)),
                "uv": uv_coordinates[loop_indices].reshape((-1, 2)),
            },
        )
        _checker_batch_cache[key] = {
            "batch": batch,
            "mesh_pointer": obj.data.as_pointer(),
        }
        return batch
    finally:
        evaluated.to_mesh_clear()


def _draw_surface_items(items, context):
    if not items:
        return
    shader = _surface_shader()
    depsgraph = context.evaluated_depsgraph_get()
    shader.bind()
    gpu.state.face_culling_set('BACK')
    for obj, color, _token in items:
        batch = _surface_batch(obj, depsgraph)
        if not batch:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        gpu.state.front_facing_set(evaluated.matrix_world.determinant() < 0.0)
        shader.uniform_float(
            "modelViewProjectionMatrix",
            context.region_data.perspective_matrix @ evaluated.matrix_world,
        )
        shader.uniform_float("color", color)
        batch.draw(shader)


def _draw_checker_items(items, context, project):
    if not items:
        return
    shader = _checker_shader()
    texture = prepare_checker()
    depsgraph = context.evaluated_depsgraph_get()
    uv_index = 0 if project.debug_checker_uv == 'PRIMARY' else 1
    tiling = float(getattr(context.scene, "pm_vr_checker_tiling", 1))
    opacity = min(1.0, 0.25 + (project.overlay_opacity * 0.75))
    shader.bind()
    shader.uniform_sampler("checkerTexture", texture)
    shader.uniform_float("tiling", tiling)
    shader.uniform_float("opacity", opacity)
    layers = {layer.layer_id: layer for layer in project.render_layers}
    units = {unit.unit_id: unit for unit in project.bake_units}
    gpu.state.face_culling_set('BACK')
    for obj, _color, _token in items:
        batch = _checker_batch(obj, depsgraph, uv_index)
        if not batch:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        metadata = obj.pm_vr_pipeline
        resolution = _object_resolution(
            project,
            metadata,
            layers.get(metadata.render_layer_id),
            units,
        )
        gpu.state.front_facing_set(evaluated.matrix_world.determinant() < 0.0)
        shader.uniform_float(
            "modelViewProjectionMatrix",
            context.region_data.perspective_matrix @ evaluated.matrix_world,
        )
        shader.uniform_float("bakeResolution", float(resolution))
        batch.draw(shader)


def _draw_view():
    global _last_draw_error
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D':
        return
    previous_depth_mask = gpu.state.depth_mask_get()
    previous_depth_test = gpu.state.depth_test_get()
    state_changed = False
    try:
        items, _counts = _collect_items(context)
        if not items:
            return
        state_changed = True
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_mask_set(False)
        gpu.state.depth_test_set('LESS_EQUAL')
        project = context.scene.pm_vr_project
        if project.overlay_mode == 'UV_CHECKER':
            checker_items = [item for item in items if item[2] == 'CHECKER']
            missing_items = [
                item for item in items if item[2] == 'CHECKER_MISSING'
            ]
            _draw_checker_items(checker_items, context, project)
            _draw_surface_items(missing_items, context)
        else:
            _draw_surface_items(items, context)
        _last_draw_error = ""
    except Exception as exc:
        message = str(exc)
        if message != _last_draw_error:
            print(f"[PM VR][Overlay] Draw warning: {message}")
            _last_draw_error = message
    finally:
        if state_changed:
            gpu.state.front_facing_set(False)
            gpu.state.face_culling_set('NONE')
            gpu.state.depth_mask_set(previous_depth_mask)
            gpu.state.depth_test_set(previous_depth_test)
            gpu.state.blend_set('NONE')


def _draw_text(x, y, text, color, size=12):
    font_id = 0
    blf.size(font_id, size)
    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.enable(font_id, blf.SHADOW)
    blf.shadow(font_id, 3, 0.0, 0.0, 0.0, 0.85)
    blf.shadow_offset(font_id, 1, -1)
    try:
        blf.draw(font_id, text)
    finally:
        blf.disable(font_id, blf.SHADOW)


def _legend_lines(context, project, counts):
    if project.overlay_mode == 'BAKE_STATUS':
        title = (
            f"Bake Status · {project.active_lighting_state.title()} · "
            f"{project.bake_mode.title()}"
        )
        entries = []
        for token in ('MISSING', 'EXISTING', 'SESSION', 'NO_BAKE', 'UNASSIGNED'):
            if not counts[token]:
                continue
            label = STATUS_LABELS[token]
            if token == 'UNASSIGNED' and not project.overlay_show_unassigned:
                label += " (hidden)"
            entries.append((label, counts[token], STATUS_COLORS[token]))
        return title, entries

    if project.overlay_mode == 'UV_HEALTH':
        entries = []
        for token in ('UV_INVALID', 'UV_VALID', 'UV_NOT_REQUIRED'):
            if counts[token]:
                entries.append(
                    (STATUS_LABELS[token], counts[token], STATUS_COLORS[token])
                )
        return f"UV Health · {PRIMARY_UV_NAME} + {BAKE_UV_NAME}", entries

    if project.overlay_mode == 'TEXEL_DENSITY':
        entries = []
        for token in (
            'TD_BAD',
            'TD_ACCEPTABLE',
            'TD_GREAT',
            'TD_INVALID',
            'TD_NOT_REQUIRED',
        ):
            if counts[token]:
                entries.append(
                    (STATUS_LABELS[token], counts[token], STATUS_COLORS[token])
                )
        target = float(get_target_td(context))
        default_resolution = int(project.default_unit_resolution)
        active = _active_texel_summary(context, project)
        resolution_label = active or f"Default {default_resolution}"
        return (
            f"Texel Density · Target {target:.1f} · {resolution_label}",
            entries,
        )

    if project.overlay_mode == 'UV_CHECKER':
        uv_name = (
            PRIMARY_UV_NAME
            if project.debug_checker_uv == 'PRIMARY'
            else BAKE_UV_NAME
        )
        entries = []
        if counts['CHECKER']:
            entries.append(
                ("Previewed", counts['CHECKER'], STATUS_COLORS['CHECKER'])
            )
        if counts['CHECKER_MISSING']:
            entries.append(
                (
                    STATUS_LABELS['CHECKER_MISSING'],
                    counts['CHECKER_MISSING'],
                    STATUS_COLORS['CHECKER_MISSING'],
                )
            )
        tiling = int(getattr(context.scene, "pm_vr_checker_tiling", 1))
        return (
            f"UV Checker · {uv_name} · Tiling {tiling} · Unit Resolution",
            entries,
        )

    if project.overlay_mode == 'SCALE_CHECK':
        entries = []
        if counts['SCALE_INVALID']:
            entries.append((
                STATUS_LABELS['SCALE_INVALID'],
                counts['SCALE_INVALID'],
                STATUS_COLORS['SCALE_INVALID'],
            ))
        return "Scale Check · only problems are filled", entries

    if project.overlay_mode == 'LINKED_MESHES':
        entries = []
        if counts['LINKED_INVALID']:
            entries.append((
                STATUS_LABELS['LINKED_INVALID'],
                counts['LINKED_INVALID'],
                STATUS_COLORS['LINKED_INVALID'],
            ))
        return "Linked Meshes · only shared data is filled", entries

    if project.overlay_mode == 'BAKE_UNITS':
        entries = []
        for unit in project.bake_units:
            count = counts[f"UNIT:{unit.unit_id}"]
            if count:
                entries.append((unit.display_name, count, _unit_color(unit.unit_id)))
        if counts['UNIT_UNASSIGNED']:
            entries.append((
                STATUS_LABELS['UNIT_UNASSIGNED'],
                counts['UNIT_UNASSIGNED'],
                STATUS_COLORS['UNIT_UNASSIGNED'],
            ))
        return "Bake Units", entries

    title = "Render Layers"
    entries = []
    for layer in project.render_layers:
        count = counts[f"LAYER:{layer.layer_id}"]
        if count:
            entries.append((layer.display_name, count, tuple(layer.viewport_color)))
    if counts['UNASSIGNED']:
        label = (
            "Unassigned"
            if project.overlay_show_unassigned
            else "Unassigned (hidden)"
        )
        entries.append((label, counts['UNASSIGNED'], STATUS_COLORS['UNASSIGNED']))
    return title, entries


def _draw_legend_background(x, top, width, height):
    shader = _uniform_shader()
    batch = batch_for_shader(
        shader,
        'TRIS',
        {
            "pos": (
                (x, top),
                (x + width, top),
                (x + width, top - height),
                (x, top - height),
            )
        },
        indices=((0, 1, 2), (0, 2, 3)),
    )
    shader.bind()
    shader.uniform_float("color", (0.025, 0.025, 0.025, 0.62))
    batch.draw(shader)


def _text_width(text, size):
    font_id = 0
    blf.size(font_id, size)
    return blf.dimensions(font_id, text)[0]


def _draw_pixel():
    global _last_legend_error
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D':
        return
    project = getattr(context.scene, "pm_vr_project", None)
    if not project or project.overlay_mode == 'OFF':
        return
    if project.overlay_mode in PIPELINE_DEBUG_MODES and not project.initialized:
        return
    try:
        gpu.state.blend_set('ALPHA')
        _items, counts = _collect_items(context)
        title, entries = _legend_lines(context, project, counts)
        controls_primary = (
            (
                "1 Bake   2 Layers   3 Units   4 UV",
                "5 TD   6 Checker   7 Scale   8 Linked",
            )
            if project.initialized
            else (
                "4 UV   5 TD   6 Checker   7 Scale   8 Linked",
                "Initialize the pipeline to enable modes 1–3",
            )
        )
        controls_secondary = "[ / ] Cycle   Esc Exit   Ctrl Shift D Toggle"
        x = 20
        visible_entries = entries[:10]
        height = (
            182
            + (29 * len(visible_entries))
            + (29 if len(entries) > 10 else 0)
        )
        top = height + 26
        content_widths = [
            _text_width(title, 22),
            _text_width("Debug Controls", 17),
            _text_width(controls_secondary, 15),
        ]
        content_widths.extend(_text_width(line, 16) for line in controls_primary)
        content_widths.extend(
            _text_width(f"{label}: {count}", 18) + 29
            for label, count, _color in visible_entries
        )
        panel_width = max(420, max(content_widths, default=0.0) + 34)
        panel_width = min(
            panel_width,
            max(220, context.region.width - 40),
        )
        _draw_legend_background(x, top, panel_width, height)
        x += 17
        y = top - 35
        _draw_text(x, y, title, (1.0, 1.0, 1.0, 0.95), 22)
        y -= 34
        for label, count, color in visible_entries:
            _draw_text(x, y, "■", (*color, 1.0), 20)
            _draw_text(
                x + 29,
                y,
                f"{label}: {count}",
                (0.92, 0.92, 0.92, 0.92),
                18,
            )
            y -= 29
        if len(entries) > 10:
            _draw_text(
                x + 29,
                y,
                f"+ {len(entries) - 10} more",
                (0.8, 0.8, 0.8, 0.9),
                18,
            )
            y -= 29
        y -= 9
        _draw_text(x, y, "Debug Controls", (1.0, 1.0, 1.0, 0.95), 17)
        y -= 27
        for line in controls_primary:
            _draw_text(
                x,
                y,
                line,
                (0.88, 0.88, 0.88, 0.92),
                16,
            )
            y -= 25
        _draw_text(
            x,
            y,
            controls_secondary,
            (0.78, 0.78, 0.78, 0.90),
            15,
        )
        _last_legend_error = ""
    except Exception as exc:
        message = str(exc)
        if message != _last_legend_error:
            print(f"[PM VR][Overlay] Legend warning: {message}")
            _last_legend_error = message
    finally:
        gpu.state.blend_set('NONE')


def _subscribe_uv_edits():
    mesh_uv_loop = getattr(bpy.types, "MeshUVLoop", None)
    if mesh_uv_loop is not None:
        bpy.msgbus.clear_by_owner(_uv_message_owner)
        bpy.msgbus.subscribe_rna(
            key=(mesh_uv_loop, "uv"),
            owner=_uv_message_owner,
            args=(),
            notify=invalidate_texel_cache,
        )


@persistent
def _load_post(_filepath):
    global _checker_texture_instance
    # Loading a file drops message-bus subscriptions.
    _subscribe_uv_edits()
    _surface_batch_cache.clear()
    _checker_batch_cache.clear()
    _texel_area_cache.clear()
    _checker_texture_instance = None
    _session_bakes.clear()
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project:
            project.overlay_mode = 'OFF'
            ensure_layer_colors(project)
    tag_redraw()


@persistent
def _depsgraph_update(_scene, depsgraph):
    dirty_objects = set()
    dirty_meshes = set()
    for update in depsgraph.updates:
        datablock = getattr(update.id, "original", update.id)
        # UV edits update the Mesh ID but are not consistently reported as
        # is_updated_geometry. Treat every Mesh update as cache-invalidating.
        if isinstance(datablock, bpy.types.Mesh):
            dirty_meshes.add(datablock.as_pointer())
        elif (
            isinstance(datablock, bpy.types.Object)
            and getattr(update, "is_updated_geometry", False)
        ):
            dirty_objects.add(datablock.as_pointer())
    if not dirty_objects and not dirty_meshes:
        return
    for key, entry in tuple(_texel_area_cache.items()):
        signature = entry[0]
        mesh_pointer = signature[0]
        if key in dirty_objects or mesh_pointer in dirty_meshes:
            _texel_area_cache.pop(key, None)
    for key, entry in tuple(_surface_batch_cache.items()):
        if key in dirty_objects or entry["mesh_pointer"] in dirty_meshes:
            _surface_batch_cache.pop(key, None)
    for key, entry in tuple(_checker_batch_cache.items()):
        object_pointer = key[0]
        if object_pointer in dirty_objects or entry["mesh_pointer"] in dirty_meshes:
            _checker_batch_cache.pop(key, None)


def register():
    global _view_handle, _pixel_handle
    if _view_handle is None:
        _view_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_view, (), 'WINDOW', 'POST_VIEW'
        )
    if _pixel_handle is None:
        _pixel_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_pixel, (), 'WINDOW', 'POST_PIXEL'
        )
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    if _depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update)
    _subscribe_uv_edits()
    scenes = getattr(bpy.data, "scenes", None)
    if scenes is not None:
        for scene in scenes:
            project = getattr(scene, "pm_vr_project", None)
            if project:
                ensure_layer_colors(project)


def unregister():
    global _view_handle, _pixel_handle, _shader, _surface_shader_instance
    global _checker_shader_instance, _checker_texture_instance
    if _view_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_view_handle, 'WINDOW')
        _view_handle = None
    if _pixel_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_pixel_handle, 'WINDOW')
        _pixel_handle = None
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_update)
    bpy.msgbus.clear_by_owner(_uv_message_owner)
    _surface_batch_cache.clear()
    _checker_batch_cache.clear()
    _texel_area_cache.clear()
    _session_bakes.clear()
    _shader = None
    _surface_shader_instance = None
    _checker_shader_instance = None
    _checker_texture_instance = None
