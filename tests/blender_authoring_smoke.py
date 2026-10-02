"""Context properties, safe Runtime names, list selection and viewport-only focus."""
from pathlib import Path
import importlib.util
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import authoring, generated, looks, preview, selection_sync, setup_ops
from PM_VR.modules.pipeline.constants import TAG_GENERATED, TAG_MODE, TAG_SOURCE_ID, TAG_UNIT_ID
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state
from PM_VR.modules.pipeline.export import export_semantic_layer

spec = importlib.util.spec_from_file_location('integrity_fixture', Path(__file__).with_name('blender_pipeline_integrity_smoke.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def select(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def main():
    PM_VR.register()
    project, root, output = fixture.build_project()
    bpy.context.scene.cycles.device = 'CPU'
    project.beauty_denoise = 'OFF'
    pbr = fixture.add_layer(project, 'Metal', 'PBR')
    alpha = fixture.add_layer(project, 'Leaves', 'ALPHA')
    glass = fixture.add_layer(project, 'Clear', 'GLASS')
    runtime_layer = fixture.add_layer(project, 'Data', 'RUNTIME')
    normal = bpy.data.images.new('Original Normal', 8, 8)
    mask = bpy.data.images.new('Original Mask', 8, 8)
    material = fixture.principled('MetalSource', (.2, .4, .6))
    tree = material.node_tree
    tex = tree.nodes.new('ShaderNodeTexImage'); tex.image = normal
    bump = tree.nodes.new('ShaderNodeNormalMap')
    tree.links.new(tex.outputs['Color'], bump.inputs['Color'])
    tree.links.new(bump.outputs['Normal'], next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Normal'])
    tree.nodes.active = tex
    a = fixture.add_source(root, 'A', (0, 0, 0), [material])
    b = fixture.add_source(root, 'B', (2, 0, 0), [material])
    u1 = fixture.add_unit(project, pbr, 'Metals', [a, b])
    alpha_mat = fixture.principled('LeafSource', (.1, .5, .2))
    alpha_tree = alpha_mat.node_tree
    alpha_tex = alpha_tree.nodes.new('ShaderNodeTexImage'); alpha_tex.image = mask
    alpha_tree.links.new(alpha_tex.outputs['Color'], next(n for n in alpha_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Alpha'])
    alpha_tree.nodes.active = alpha_tex
    leaf = fixture.add_source(root, 'Leaf', (4, 0, 0), [alpha_mat])
    u2 = fixture.add_unit(project, alpha, 'Foliage', [leaf])
    activate_state(bpy.context, 'DAY')
    for unit in (u1, u2):
        assert fixture.bake_unit(unit) == 'SUCCESS'
    ga, gb, gl = (fixture.generated_for(o) for o in (a, b, leaf))
    for obj in (ga, gb, gl):
        for mat in obj.data.materials:
            assert mat.node_tree.nodes.active.name == 'PMVR Baked Beauty'
            assert [n.name for n in mat.node_tree.nodes if n.select] == ['PMVR Baked Beauty']
    assert tree.nodes.active == tex and alpha_tree.nodes.active == alpha_tex
    ids = [(o.name, o.get(TAG_SOURCE_ID), o.get(TAG_UNIT_ID), o.data.name) for o in (ga, gb, gl)]
    signatures = [u.day_signature for u in project.bake_units]
    select(ga, gb)
    assert authoring.fields_for(project, ga) == ('emissiveIntensity',)
    assert bpy.ops.pmvr.platform_property(key='emissiveIntensity', action='ADD') == {'FINISHED'}
    assert a['emissiveIntensity'] == 1 and 'emissiveIntensity' not in ga
    a['emissiveIntensity'] = .27
    assert bpy.ops.pmvr.platform_property(key='emissiveIntensity', action='COPY', bulk=True) == {'FINISHED'}
    assert abs(b['emissiveIntensity'] - .27) < 1e-6
    bpy.ops.pmvr.platform_property(key='emissiveIntensity', action='ADD', bulk=True)
    assert a['emissiveIntensity'] == b['emissiveIntensity']
    bpy.ops.pmvr.platform_property(key='emissiveIntensity', action='REMOVE')
    assert 'emissiveIntensity' not in a and 'emissiveIntensity' in b
    clear = fixture.add_source(root, 'GlassSource', (6, 0, 0), [fixture.principled('Glass', (1, 1, 1))])
    meta = clear.pm_vr_pipeline
    meta.is_registered_source, meta.source_id, meta.render_layer_id = True, new_id(), glass
    meta.processing_role = 'EXPORT_ORIGINAL'
    next(n for n in clear.data.materials[0].node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Alpha'].default_value = .18
    select(clear)
    bpy.ops.pmvr.platform_property(key='opacity', action='ADD')
    assert abs(clear['opacity'] - .18) < 1e-6
    bpy.ops.pmvr.platform_property(key='reflectionIntensity', action='ADD')
    assert bpy.context.scene['reflectionIntensity'] == 1
    project.preview_mode = 'SOURCES'
    assert project.show_sources and not project.show_generated and ga.hide_get()
    project.show_generated = True
    assert not project.show_sources and project.show_generated and a.hide_get()
    project.preview_mode = 'BOTH'
    assert project.show_sources and project.show_generated
    project.preview_mode = 'SOURCES'
    other = bpy.data.scenes.new('Other preview scene')
    other.pm_vr_project.show_generated = True
    preview.migrate(other.pm_vr_project)
    assert not a.hide_get() and ga.hide_get(), 'Another scene changed the active viewport'
    project.active_render_layer_index = 0
    # A click selects the entire two-object unit, reverse sync selects no extras.
    project.active_bake_unit_index = 0
    assert set(bpy.context.selected_objects) == {a, b}
    select(a)
    selection_sync.sync_now(bpy.context.scene, bpy.context.view_layer)
    assert set(bpy.context.selected_objects) == {a}
    project.bake_queue.add().unit_id = u2
    project.bake_queue.add().unit_id = u1
    bpy.context.scene.pm_vr_ui_state.stage = 'BAKE'
    project.active_bake_queue_index = 1
    assert set(bpy.context.selected_objects) == {a, b}
    # Unqueued old Foliage is hidden, but remains in the actual USD export.
    preview.finish(bpy.context, [preview.completed(u1, 'DAY', 'BEAUTY', [a, b])], 'DAY')
    assert project.preview_mode == 'GENERATED' and project.preview_last_queue
    assert not ga.hide_get() and not gb.hide_get() and gl.hide_get()
    assert all(obj.hide_get() for obj in (a, b, leaf, clear))
    status, path = export_semantic_layer(bpy.context, fixture.layer_by_id(alpha), 'USDZ')
    assert status == 'SUCCESS', (status, path)
    from pxr import Usd
    stage = Usd.Stage.Open(path)
    assert any(prim.GetTypeName() == 'Mesh' for prim in stage.Traverse())
    assert gl.hide_get(), 'Export changed the preview scope'
    # Clicking a different queue result releases focus so it can be selected.
    project.active_bake_queue_index = 0
    assert set(bpy.context.selected_objects) == {gl} and not project.preview_last_queue
    assert [(o.name, o.get(TAG_SOURCE_ID), o.get(TAG_UNIT_ID), o.data.name) for o in (ga, gb, gl)] == ids
    assert [u.day_signature for u in project.bake_units] == signatures
    assert bpy.ops.pmvr.runtime_role(create=True, role='SFX', tail='Street01') == {'FINISHED'}
    sound = bpy.context.object
    assert sound.name == 'SFX_Street01' and sound.type == 'EMPTY'
    assert sound.empty_display_type == 'SPHERE' and sound.empty_display_size == 1
    assert sound.pm_vr_pipeline.processing_role == 'EXPORT_ORIGINAL'
    assert authoring.fields_for(project, sound) == ('title', 'volume')
    assert authoring.media_hints(sound.name) == ['audio/sfx/SFX_Street.mp3']
    bpy.ops.pmvr.platform_property(key='title', action='ADD')
    assert sound['title'] == 'Street'
    assert bpy.ops.pmvr.runtime_role(create=True, role='Zone', tail='Terrace') == {'FINISHED'}
    zone = bpy.context.object
    assert zone.name == 'Zone_Terrace' and zone.type == 'MESH'
    assert bpy.ops.pmvr.runtime_role(create=True, role='SkyPoint', zone='Terrace') == {'FINISHED'}
    assert bpy.context.object.name == 'SkyPoint_Terrace'
    assert bpy.ops.pmvr.runtime_role(create=True, role='StartPosition') == {'FINISHED'}
    start = bpy.context.object
    assert start.name == 'StartPosition'
    assert authoring.naming_problem(project, None, 'StartPosition', 'StartPosition', True)
    assert authoring.naming_problem(project, zone, 'Zone_Evening', 'Zone')
    assert authoring.naming_problem(project, zone, 'Zone_bad_name', 'Zone')
    assert authoring.naming_problem(project, None, 'Navmesh', 'Navmesh', True)
    assert bpy.ops.pmvr.runtime_role(create=True, role='Music_Source') == {'FINISHED'}
    assert bpy.ops.pmvr.runtime_role(create=True, role='Music_Source', instance=1) == {'FINISHED'}
    assert bpy.context.object.name == 'Music_Source_001'
    assert bpy.ops.pmvr.runtime_role(create=True, role='Probe', tail='Center') == {'FINISHED'}
    assert bpy.context.object.type == 'CAMERA' and bpy.context.object.name in project.probe_collection.objects
    sky = fixture.add_source(root, 'SkyMesh', (8, 0, 0))
    select(sky)
    activate_state(bpy.context, 'EVENING')
    assert bpy.ops.pmvr.runtime_role(create=False, role='Skybox', look_id='DAY') == {'FINISHED'}
    assert sky.name == 'Skybox_Day' and tuple(sky.users_collection) == (project.day_lighting_collection,)
    assert looks.active_id(project) == 'DAY' and bpy.context.object == sky
    sky_id = sky.pm_vr_pipeline.source_id
    assert bpy.ops.pmvr.runtime_role(create=False, role='Skybox', look_id='ALL') == {'FINISHED'}
    assert sky.name == 'Skybox' and tuple(sky.users_collection) == (root,)
    assert sky.pm_vr_pipeline.source_id == sky_id
    assert authoring.media_hints('Video_Fire.001') == ['video/Fire.mov (or .mp4)']
    select(a)
    try:
        bpy.ops.pmvr.runtime_role(create=False, role='Video', tail='No')
    except RuntimeError as exc:
        assert 'outside a bake unit' in str(exc)
    else:
        raise AssertionError('Accepted a bake member as Runtime')
    assert a.pm_vr_pipeline.bake_unit_id == u1
    assert any(name == sound.name and 'audio/sfx/SFX_Street.mp3' in msg for name, msg in authoring.runtime_issues(project))
    blend = Path(output) / 'authoring.blend'
    project.preview_mode = 'BOTH'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    assert bpy.context.scene.pm_vr_project.preview_mode == 'BOTH'
    assert len(bpy.context.scene.pm_vr_project.preview_results) == 1
    assert bpy.data.objects['SFX_Street01']['title'] == 'Street'
    print('PMVR_AUTHORING_SMOKE_OK', flush=True)
    PM_VR.unregister()


if __name__ == '__main__':
    main()
