"""Help tabs plus data-only Runtime meshes: no bake occlusion, correct export."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import bpy
import numpy as np
from pxr import Usd, UsdGeom

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import bake, export, platform, probes, ui
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state


def main():
    PM_VR.register()
    spec = importlib.util.spec_from_file_location('ui_checks', Path(__file__).with_name('blender_ui_smoke.py'))
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    harness = SimpleNamespace(layout=checks._LayoutRecorder(), help_tab='RULES', rule_section='Scene')
    for tab in ('RULES', 'RUNTIME', 'AFTER'):
        harness.help_tab = tab
        for section, _icon, _lines in ui.HELP_SECTIONS:
            harness.rule_section = section
            ui.PMVR_OT_ShowHelp.draw(harness, bpy.context)
    assert {x.identifier for x in bpy.ops.pmvr.show_help.get_rna_type().properties['help_tab'].enum_items} == {'RULES', 'RUNTIME', 'AFTER'}
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene, project = bpy.context.scene, bpy.context.scene.pm_vr_project
    scene.cycles.device = 'CPU'
    scene.render.threads_mode, scene.render.threads = 'FIXED', 2
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.seed = 37
    project.initialized, project.project_id = True, new_id()
    project.bake_resolution, project.cycles_samples = '256', 2
    project.beauty_denoise, project.fill_empty_uv = 'OFF', False
    output = Path(tempfile.mkdtemp(prefix='pmvr_runtime_rules_'))
    project.beauty_output_directory = str(output / 'Bakes')
    project.log_output_directory = str(output / 'Logs')
    project.usdz_output_directory = str(output / 'USD')
    root, day, evening = (bpy.data.collections.new(name) for name in ('Root', 'Day', 'Evening'))
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection, project.evening_lighting_collection = day, evening
    project.day_world, project.evening_world = bpy.data.worlds.new('Day'), bpy.data.worlds.new('Evening')
    project.day_world.color = (.5, .5, .5)
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), 'Unlit', 'UNLIT'
    runtime_layer = project.render_layers.add()
    runtime_layer.layer_id, runtime_layer.display_name, runtime_layer.layer_type = new_id(), 'Runtime', 'RUNTIME'
    # Re-fetch RNA items after collection grows.
    layer = project.render_layers[0]
    bpy.ops.mesh.primitive_plane_add(size=2)
    receiver = bpy.context.object
    for owner in list(receiver.users_collection):
        owner.objects.unlink(receiver)
    root.objects.link(receiver)
    receiver.data.uv_layers[0].name = 'UVMap'
    receiver.data.uv_layers.new(name='SimpleBake', do_init=True)
    unit = project.bake_units.add()
    unit.unit_id, unit.artifact_key, unit.display_name = new_id(), 'Receiver', 'Receiver'
    unit.render_layer_id, unit.resolution = layer.layer_id, '256'
    meta = receiver.pm_vr_pipeline
    meta.source_id, meta.is_registered_source = new_id(), True
    meta.render_layer_id, meta.bake_unit_id, meta.processing_role = layer.layer_id, unit.unit_id, 'BAKE'
    activate_state(bpy.context, 'DAY')
    def pixels():
        runtime = bake.BeautyBakeRuntime(bpy.context, unit)
        try:
            assert runtime.prepare() == 'READY'
            runtime.select_receiver(0)
            bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs())
            return np.array(runtime.image.pixels[:]), runtime.signature
        finally:
            runtime.cleanup()
    baseline, signature = pixels()
    helpers = []
    for name in ('Zone_Room', 'NavmeshFloor', 'CollisionCeiling'):
        bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, 1))
        helper = bpy.context.object
        helper.name = name
        for owner in list(helper.users_collection):
            owner.objects.unlink(helper)
        root.objects.link(helper)
        meta = helper.pm_vr_pipeline
        meta.source_id, meta.is_registered_source = new_id(), True
        meta.render_layer_id, meta.processing_role = runtime_layer.layer_id, 'EXPORT_ORIGINAL'
        helpers.append(helper)
    assert not platform.runtime_warnings(helpers)
    before = {obj.name: (obj.hide_render, obj.matrix_world.copy(), obj.data.as_pointer()) for obj in helpers}
    current, current_signature = pixels()
    assert signature == current_signature
    assert np.allclose(baseline, current, atol=.001), 'Runtime helpers cast shadows'
    for obj in helpers:
        assert (obj.hide_render, obj.matrix_world, obj.data.as_pointer()) == before[obj.name]
    session = probes.ProbeSession(bpy.context)
    try:
        session.begin()
        assert all(obj.hide_render for obj in helpers)
    finally:
        session.end()
    assert not any(obj.hide_render for obj in helpers)
    # Snapshot cleanup also covers a preparation/render failure.
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    try:
        assert runtime.prepare() == 'READY'
        assert all(obj.hide_render for obj in helpers)
    finally:
        runtime.cleanup()
    assert not any(obj.hide_render for obj in helpers)
    status, path = export.export_semantic_layer(bpy.context, runtime_layer, 'USDZ')
    assert status == 'SUCCESS'
    stage = Usd.Stage.Open(path)
    assert len([p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]) == 3
    assert not any(obj.hide_render for obj in helpers)
    print('PM_VR_RUNTIME_RULES_OK', flush=True)


if __name__ == '__main__':
    main()
