"""Non-destructive transparent viewport overlays for pipeline state."""

from collections import Counter

import blf
import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .constants import LAYER_COLOR_PALETTE, TAG_GENERATED


STATUS_COLORS = {
    'MISSING': (0.96, 0.035, 0.025),
    'EXISTING': (0.04, 0.78, 0.13),
    'SESSION': (0.01, 0.92, 0.58),
    'NO_BAKE': (0.30, 0.34, 0.38),
    'UNASSIGNED': (1.00, 0.38, 0.025),
}
STATUS_ALPHA = {
    'MISSING': 1.00,
    'EXISTING': 0.45,
    'SESSION': 1.00,
    'NO_BAKE': 0.16,
    'UNASSIGNED': 0.80,
}
STATUS_LABELS = {
    'MISSING': "Missing",
    'EXISTING': "Existing",
    'SESSION': "This Session",
    'NO_BAKE': "No Bake",
    'UNASSIGNED': "Unassigned",
}

_view_handle = None
_pixel_handle = None
_shader = None
_surface_shader_instance = None
_surface_batch_cache = {}
_session_bakes = set()
_last_draw_error = ""
_last_legend_error = ""


def tag_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(window_manager, "windows", ()):
        screen = window.screen
        if not screen:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def ensure_layer_colors(project):
    for index, layer in enumerate(project.render_layers):
        if layer.viewport_color_initialized:
            continue
        layer.viewport_color = LAYER_COLOR_PALETTE[
            index % len(LAYER_COLOR_PALETTE)
        ]
        layer.viewport_color_initialized = True


def draw_controls(layout, project):
    box = layout.box()
    box.label(text="Viewport Overlay", icon='OVERLAY')
    box.row(align=True).prop(project, "overlay_mode", expand=True)
    if project.overlay_mode != 'OFF':
        box.row(align=True).prop(project, "overlay_style", expand=True)
        box.prop(project, "overlay_opacity", text="Intensity", slider=True)
        box.prop(project, "overlay_show_unassigned", toggle=True)
        if project.overlay_mode == 'BAKE_STATUS':
            state = project.active_lighting_state.title()
            mode = "Beauty" if project.bake_mode == 'BEAUTY' else "Lightmap"
            box.label(text=f"Showing {state} · {mode}", icon='INFO')


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


def _classify(project, obj, layers, units):
    metadata = obj.pm_vr_pipeline
    layer = layers.get(metadata.render_layer_id)
    if not metadata.is_registered_source or not layer:
        return 'UNASSIGNED', STATUS_COLORS['UNASSIGNED']

    if project.overlay_mode == 'RENDER_LAYERS':
        return f"LAYER:{layer.layer_id}", tuple(layer.viewport_color)

    if (
        metadata.processing_role == 'EXPORT_ORIGINAL'
        or layer.processing_profile == 'EXPORT_ORIGINAL'
    ):
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
    if not project or not project.initialized or project.overlay_mode == 'OFF':
        return [], Counter()
    layers = {layer.layer_id: layer for layer in project.render_layers}
    units = {unit.unit_id: unit for unit in project.bake_units}
    items = []
    counts = Counter()
    for obj in _source_objects(scene, project):
        if not _is_visible(obj, context):
            continue
        token, color = _classify(project, obj, layers, units)
        counts[token] += 1
        if token == 'UNASSIGNED' and not project.overlay_show_unassigned:
            continue
        alpha_scale = STATUS_ALPHA.get(token, 1.0)
        if project.overlay_style == 'SURFACE':
            base_alpha = 0.05 + (project.overlay_opacity * 0.35)
        else:
            base_alpha = 0.25 + (project.overlay_opacity * 0.75)
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


_BOUND_BOX_EDGES = (
    (0, 1), (0, 3), (0, 4),
    (1, 2), (1, 5),
    (2, 3), (2, 6),
    (3, 7),
    (4, 5), (4, 7),
    (5, 6),
    (6, 7),
)


def _corner_segments(obj, depsgraph, fraction=0.22):
    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world
    corners = [matrix @ Vector(corner) for corner in evaluated.bound_box]
    positions = []
    for first_index, second_index in _BOUND_BOX_EDGES:
        first = corners[first_index]
        second = corners[second_index]
        positions.extend((first, first.lerp(second, fraction)))
        positions.extend((second, second.lerp(first, fraction)))
    return positions


def _line_width(token):
    if token in {'MISSING', 'SESSION'}:
        return 2.5
    if token == 'UNASSIGNED':
        return 2.0
    if token == 'NO_BAKE':
        return 1.0
    return 1.5


def _draw_surface_items(items, context):
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


def _draw_corner_items(items, context):
    shader = _uniform_shader()
    depsgraph = context.evaluated_depsgraph_get()
    shader.bind()
    gpu.state.depth_test_set('NONE')
    groups = {}
    for obj, color, token in items:
        key = (color, token)
        groups.setdefault(key, []).extend(_corner_segments(obj, depsgraph))
    for (color, token), positions in groups.items():
        if not positions:
            continue
        gpu.state.line_width_set(_line_width(token))
        batch = batch_for_shader(shader, 'LINES', {"pos": positions})
        shader.uniform_float("color", color)
        batch.draw(shader)


def _draw_view():
    global _last_draw_error
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D':
        return
    previous_depth_mask = gpu.state.depth_mask_get()
    previous_depth_test = gpu.state.depth_test_get()
    previous_line_width = gpu.state.line_width_get()
    state_changed = False
    try:
        items, _counts = _collect_items(context)
        if not items:
            return
        state_changed = True
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_mask_set(False)
        if context.scene.pm_vr_project.overlay_style == 'SURFACE':
            gpu.state.depth_test_set('LESS_EQUAL')
            _draw_surface_items(items, context)
        else:
            _draw_corner_items(items, context)
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
            gpu.state.line_width_set(previous_line_width)
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


def _legend_lines(project, counts):
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


def _draw_pixel():
    global _last_legend_error
    context = bpy.context
    if not context.area or context.area.type != 'VIEW_3D':
        return
    project = getattr(context.scene, "pm_vr_project", None)
    if not project or not project.initialized or project.overlay_mode == 'OFF':
        return
    try:
        gpu.state.blend_set('ALPHA')
        _items, counts = _collect_items(context)
        title, entries = _legend_lines(project, counts)
        x = 14
        visible_entries = entries[:10]
        height = (
            37
            + (17 * len(visible_entries))
            + (17 if len(entries) > 10 else 0)
        )
        top = height + 18
        _draw_legend_background(x, top, 235, height)
        x += 10
        y = top - 21
        _draw_text(x, y, title, (1.0, 1.0, 1.0, 0.95), 13)
        y -= 20
        for label, count, color in visible_entries:
            _draw_text(x, y, "■", (*color, 1.0), 12)
            _draw_text(x + 17, y, f"{label}: {count}", (0.92, 0.92, 0.92, 0.92), 11)
            y -= 17
        if len(entries) > 10:
            _draw_text(
                x + 17,
                y,
                f"+ {len(entries) - 10} more",
                (0.8, 0.8, 0.8, 0.9),
                11,
            )
        _last_legend_error = ""
    except Exception as exc:
        message = str(exc)
        if message != _last_legend_error:
            print(f"[PM VR][Overlay] Legend warning: {message}")
            _last_legend_error = message
    finally:
        gpu.state.blend_set('NONE')


@persistent
def _load_post(_filepath):
    _surface_batch_cache.clear()
    _session_bakes.clear()
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project:
            ensure_layer_colors(project)
    tag_redraw()


@persistent
def _depsgraph_update(_scene, depsgraph):
    dirty_objects = set()
    dirty_meshes = set()
    for update in depsgraph.updates:
        if not getattr(update, "is_updated_geometry", False):
            continue
        datablock = getattr(update.id, "original", update.id)
        if isinstance(datablock, bpy.types.Object):
            dirty_objects.add(datablock.as_pointer())
        elif isinstance(datablock, bpy.types.Mesh):
            dirty_meshes.add(datablock.as_pointer())
    if not dirty_objects and not dirty_meshes:
        return
    for key, entry in tuple(_surface_batch_cache.items()):
        if key in dirty_objects or entry["mesh_pointer"] in dirty_meshes:
            _surface_batch_cache.pop(key, None)


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
    for scene in bpy.data.scenes:
        project = getattr(scene, "pm_vr_project", None)
        if project:
            ensure_layer_colors(project)


def unregister():
    global _view_handle, _pixel_handle, _shader, _surface_shader_instance
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
    _surface_batch_cache.clear()
    _session_bakes.clear()
    _shader = None
    _surface_shader_instance = None
