"""Temporary Empty representations of authoring cameras, restored on failure."""
from contextlib import contextmanager
import uuid

import bpy
from mathutils import Matrix

from . import looks


@contextmanager
def representations(context, project, objects):
    from .probes import probe_cameras
    probes = {obj.as_pointer() for obj in probe_cameras(project, looks.active_id(project))}
    replacements, proxies = [], []
    try:
        result = []
        for obj in objects:
            if obj.type != 'CAMERA' or obj.as_pointer() not in probes:
                result.append(obj)
                continue
            position = obj.evaluated_get(context.evaluated_depsgraph_get()).matrix_world.translation.copy()
            original_name = obj.name
            proxy = bpy.data.objects.new('__PMVR_PROBE_EXPORT_' + uuid.uuid4().hex, None)
            proxies.append(proxy)
            for key, value in obj.items():
                if key in {'pm_vr_pipeline', '_RNA_UI'}:
                    continue
                proxy[key] = value.to_dict() if hasattr(value, 'to_dict') else value
            proxy.parent = obj.parent
            proxy.matrix_world = Matrix.Translation(position)
            replacements.append((obj, original_name))
            obj.name = '__PMVR_PROBE_SOURCE_' + uuid.uuid4().hex
            proxy.name = original_name
            result.append(proxy)
        yield result
    finally:
        for proxy in proxies:
            bpy.data.objects.remove(proxy, do_unlink=True)
        for obj, original_name in replacements:
            obj.name = original_name
