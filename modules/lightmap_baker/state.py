"""Temporary Blender context, visibility, and receiver state."""

import bpy


def _is_live_object(obj):
    if obj is None:
        return False
    try:
        return bpy.data.objects.get(obj.name) is obj
    except ReferenceError:
        return False


def _object_in_view_layer(context, obj):
    if not _is_live_object(obj):
        return False
    try:
        return context.view_layer.objects.get(obj.name) is obj
    except ReferenceError:
        return False


class ContextState:
    """Restore mode, active object, selection, and render engine."""

    def __init__(self, context):
        self.scene = context.scene
        self.view_layer = context.view_layer
        self.engine = self.scene.render.engine
        self.active_object = context.view_layer.objects.active
        self.active_name = self.active_object.name if self.active_object else ""
        self.active_mode = (
            self.active_object.mode if self.active_object else 'OBJECT'
        )
        self.selected = [
            (obj, obj.name)
            for obj in context.selected_objects
        ]

    @staticmethod
    def _resolve_object(obj, name):
        if _is_live_object(obj):
            return obj
        return bpy.data.objects.get(name) if name else None

    def enter_object_mode(self, context):
        active = context.view_layer.objects.active
        if active and active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    def restore(self, context):
        try:
            active = context.view_layer.objects.active
            if active and active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for obj in list(context.selected_objects):
            try:
                obj.select_set(False)
            except Exception:
                pass

        for obj, name in self.selected:
            resolved = self._resolve_object(obj, name)
            if not _object_in_view_layer(context, resolved):
                continue
            try:
                resolved.select_set(True)
            except Exception:
                pass

        active = self._resolve_object(self.active_object, self.active_name)
        if _object_in_view_layer(context, active):
            try:
                context.view_layer.objects.active = active
            except Exception:
                active = None

        if active and self.active_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=self.active_mode)
            except Exception:
                pass

        try:
            self.scene.render.engine = self.engine
        except Exception:
            pass


class ObjectVisibilityState:
    def __init__(self, context, obj):
        self.obj = obj
        self.view_layer = context.view_layer
        self.hide_viewport = obj.hide_viewport
        self.hide_render = obj.hide_render
        try:
            self.hidden = obj.hide_get(view_layer=context.view_layer)
        except TypeError:
            self.hidden = obj.hide_get()

    def restore(self):
        obj = self.obj
        if not _is_live_object(obj):
            return
        obj.hide_viewport = self.hide_viewport
        obj.hide_render = self.hide_render
        try:
            obj.hide_set(self.hidden, view_layer=self.view_layer)
        except TypeError:
            obj.hide_set(self.hidden)


def set_visible_for_bake(context, obj):
    obj.hide_viewport = False
    obj.hide_render = False
    try:
        obj.hide_set(False, view_layer=context.view_layer)
    except TypeError:
        obj.hide_set(False)


def set_hidden(context, obj):
    obj.hide_render = True
    obj.hide_viewport = True
    try:
        obj.hide_set(True, view_layer=context.view_layer)
    except TypeError:
        obj.hide_set(True)


def set_result_visible(context, obj):
    obj.hide_render = False
    obj.hide_viewport = False
    try:
        obj.hide_set(False, view_layer=context.view_layer)
    except TypeError:
        obj.hide_set(False)


def select_only(context, obj):
    for selected in list(context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


class BakeSettingsState:
    """Temporarily configure the scene bake pass and restore every changed field."""

    BAKE_ATTRIBUTES = (
        "target",
        "use_clear",
        "margin",
        "margin_type",
        "use_pass_direct",
        "use_pass_indirect",
        "use_pass_color",
    )

    def __init__(self, scene):
        self.scene = scene
        self.bake = scene.render.bake
        self.values = {
            attribute: getattr(self.bake, attribute)
            for attribute in self.BAKE_ATTRIBUTES
            if hasattr(self.bake, attribute)
        }
        self.cycles_bake_type = (
            scene.cycles.bake_type
            if hasattr(scene, "cycles") and hasattr(scene.cycles, "bake_type")
            else None
        )

    def configure(self, bake_type, margin, pass_filter=None):
        assignments = {
            "target": 'IMAGE_TEXTURES',
            "use_clear": True,
            "margin": margin,
            "margin_type": 'EXTEND',
        }
        if pass_filter is not None:
            assignments.update(
                {
                    "use_pass_direct": 'DIRECT' in pass_filter,
                    "use_pass_indirect": 'INDIRECT' in pass_filter,
                    "use_pass_color": 'COLOR' in pass_filter,
                }
            )
        for attribute, value in assignments.items():
            if hasattr(self.bake, attribute):
                setattr(self.bake, attribute, value)
        if self.cycles_bake_type is not None:
            self.scene.cycles.bake_type = bake_type

    def restore(self):
        for attribute, value in self.values.items():
            try:
                setattr(self.bake, attribute, value)
            except (AttributeError, TypeError, ValueError):
                pass
        if self.cycles_bake_type is not None:
            try:
                self.scene.cycles.bake_type = self.cycles_bake_type
            except (AttributeError, TypeError, ValueError):
                pass
