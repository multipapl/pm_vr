"""Real diffuse/Combined bakes; preserved authored PBR channels and USD UVs."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy
import numpy as np
from pxr import Sdf, Usd, UsdGeom, UsdShade

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import PM_VR
from PM_VR.modules.pipeline import bake, export, generated
from PM_VR.modules.pipeline.bake_scene import BakeConfigurationSnapshot
from PM_VR.modules.pipeline.identity import new_id
from PM_VR.modules.pipeline.state import activate_state


def material_state(material):
    node = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    state = {}
    for socket in node.inputs:
        if socket.name == 'Base Color' or not hasattr(socket, 'default_value'):
            continue
        value = socket.default_value
        state[socket.name] = (tuple(value) if hasattr(value, '__len__') else value,
                             tuple((x.from_node.name, x.from_socket.name) for x in socket.links))
    return state


def authored_specs(path):
    stage = Usd.Stage.Open(str(path))
    layer = stage.GetRootLayer()
    result = {}
    def visit(address):
        spec = layer.GetObjectAtPath(address)
        if spec:
            result[str(address)] = {key: spec.GetInfo(key) for key in spec.ListInfoKeys()}
    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return result


def inspect_pair(before, after, atlas_name):
    assert authored_specs(before) == authored_specs(after), 'Authored USD changed beyond atlas pixels'
    def media(path):
        with zipfile.ZipFile(path) as archive:
            return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
                    if not name.endswith(('.usda', '.usdc', '.usd'))}
    old, new = media(before), media(after)
    assert old.keys() == new.keys()
    changed = [name for name in old if old[name] != new[name]]
    assert len(changed) == 1 and Path(changed[0]).name == atlas_name, changed
    stage = Usd.Stage.Open(str(after))
    meshes = [UsdGeom.Mesh(p) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert meshes
    for mesh in meshes:
        uv = UsdGeom.PrimvarsAPI(mesh)
        assert uv.GetPrimvar('st') and uv.GetPrimvar('UVMap')
    shaders = [UsdShade.Shader(p) for p in stage.Traverse() if p.IsA(UsdShade.Shader)]
    preview = next(s for s in shaders if s.GetIdAttr().Get() == 'UsdPreviewSurface')
    assert preview.GetInput('metallic').HasConnectedSource()
    assert preview.GetInput('roughness').HasConnectedSource()
    assert preview.GetInput('normal').HasConnectedSource()
    assert preview.GetInput('clearcoat').Get() > .5
    assert preview.GetInput('emissiveColor').Get()[0] > .1
    return {'changed_media': changed, 'authored_usd_equal': True, 'original_pbr_uvs_preserved': True}


def bake_once(unit, source, folder, diffuse):
    project = bpy.context.scene.pm_vr_project
    project.beauty_output_directory = str(folder)
    project.pbr_diffuse_only = diffuse
    settings = BakeConfigurationSnapshot(bpy.context.scene)
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    try:
        assert runtime.prepare() == 'READY'
        assert runtime.bake_kwargs()['type'] == ('DIFFUSE' if diffuse else 'COMBINED')
        receiver_bsdf = next(n for n in runtime.receivers[0]['object'].data.materials[0].node_tree.nodes
                             if n.type == 'BSDF_PRINCIPLED')
        assert receiver_bsdf.inputs['Metallic'].default_value == 0 and not receiver_bsdf.inputs['Metallic'].links
        if diffuse:
            assert runtime.bake_kwargs()['pass_filter'] == {'DIRECT', 'INDIRECT', 'COLOR'}
            for field in ('use_pass_glossy', 'use_pass_transmission', 'use_pass_emit'):
                assert not getattr(bpy.context.scene.render.bake, field)
        for index in range(len(runtime.receivers)):
            runtime.select_receiver(index)
            assert bpy.ops.object.bake('EXEC_DEFAULT', **runtime.bake_kwargs()) == {'FINISHED'}
        pixels = np.array(runtime.image.pixels[:], dtype=np.float32).reshape(-1, 4)
        signature = runtime.signature
        runtime.finish()
        image = bpy.data.images[unit.day_beauty_image]
        atlas = Path(bpy.path.abspath(image.filepath))
        obj = generated.find_generated(unit.unit_id, source.pm_vr_pipeline.source_id)
        assert material_state(obj.data.materials[0]) == material_state(source.data.materials[0])
        for node in obj.data.materials[0].node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.name != 'PMVR Baked Beauty':
                assert node.inputs['Vector'].links[0].from_node.uv_map == 'UVMap'
            if node.type == 'NORMAL_MAP':
                assert node.uv_map == 'UVMap'
        textures = export.ExportTextures()
        package = folder / 'PBR.usdz'
        try:
            export._write_usdz(bpy.context, project, runtime.layer, [obj], str(package), textures)
        finally:
            textures.cleanup()
        return pixels, signature, atlas, package, obj.as_pointer()
    finally:
        runtime.cleanup(keep_image=runtime.finished)
        assert BakeConfigurationSnapshot(bpy.context.scene).values == settings.values
        assert bpy.context.scene.cycles.bake_type == settings.bake_type


def main():
    PM_VR.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.cycles.device = 'CPU'
    scene.render.threads_mode, scene.render.threads = 'FIXED', 2
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.seed = 17
    project = scene.pm_vr_project
    assert not project.pbr_diffuse_only
    project.initialized, project.project_id = True, new_id()
    project.bake_resolution, project.cycles_samples = '256', 8
    project.beauty_denoise, project.fill_empty_uv = 'OFF', False
    output = Path(tempfile.mkdtemp(prefix='pmvr_pbr_diffuse_'))
    project.log_output_directory = str(output / 'Logs')
    root, day, evening = (bpy.data.collections.new(x) for x in ('Root', 'Day', 'Evening'))
    scene.collection.children.link(root)
    root.children.link(day)
    root.children.link(evening)
    project.source_root_collection = root
    project.day_lighting_collection, project.evening_lighting_collection = day, evening
    project.day_world, project.evening_world = bpy.data.worlds.new('Day'), bpy.data.worlds.new('Evening')
    project.day_world.color = (.25, .25, .25)
    bpy.ops.mesh.primitive_plane_add(size=2)
    source = bpy.context.object
    source.name = 'PBRReceiver'
    for owner in list(source.users_collection):
        owner.objects.unlink(source)
    root.objects.link(source)
    source.data.uv_layers[0].name = 'UVMap'
    simple = source.data.uv_layers.new(name='SimpleBake', do_init=True)
    for loop in simple.data:
        loop.uv = tuple(.1 + .8 * value for value in loop.uv)
    material = bpy.data.materials.new('Authored PBR')
    material.use_nodes = True
    source.data.materials.append(material)
    tree = material.node_tree
    principled = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    principled.inputs['Base Color'].default_value = (.4, .2, .1, 1)
    principled.inputs['Coat Weight'].default_value = .8
    principled.inputs['Coat Roughness'].default_value = .03
    principled.inputs['Emission Color'].default_value = (.8, .2, .1, 1)
    principled.inputs['Emission Strength'].default_value = 2
    for field, color in (('Metallic', (.7, .7, .7, 1)), ('Roughness', (.15, .15, .15, 1)),
                         ('Normal', (.5, .5, 1, 1))):
        image = bpy.data.images.new('Original' + field, 8, 8)
        image.generated_color = color
        image.colorspace_settings.name = 'Non-Color'
        image.filepath_raw = str(output / (image.name + '.png'))
        image.file_format = 'PNG'
        image.save()
        tex = tree.nodes.new('ShaderNodeTexImage')
        tex.name, tex.image = field + ' texture', image
        socket = tex.outputs['Color']
        if field == 'Normal':
            normal = tree.nodes.new('ShaderNodeNormalMap')
            tree.links.new(socket, normal.inputs['Color'])
            socket = normal.outputs['Normal']
        tree.links.new(socket, principled.inputs[field])
    layer = project.render_layers.add()
    layer.layer_id, layer.display_name, layer.layer_type = new_id(), 'PBR', 'PBR'
    unit = project.bake_units.add()
    unit.unit_id, unit.artifact_key, unit.display_name = new_id(), 'PBRFixture', 'PBRFixture'
    unit.render_layer_id, unit.resolution = layer.layer_id, '256'
    meta = source.pm_vr_pipeline
    meta.source_id, meta.is_registered_source = new_id(), True
    meta.render_layer_id, meta.bake_unit_id, meta.processing_role = layer.layer_id, unit.unit_id, 'BAKE'
    activate_state(bpy.context, 'DAY')
    original = material_state(material)
    before = bake_once(unit, source, output / 'Combined', False)
    after = bake_once(unit, source, output / 'Diffuse', True)
    assert original == material_state(material), 'Source PBR inputs changed'
    assert before[1] == after[1], 'Protected compatibility signature changed'
    assert before[4] == after[4], 'Canonical generated object replaced'
    report = inspect_pair(before[3], after[3], before[2].name)
    mask = np.max(after[0][:, :3], axis=1) > .001
    assert mask.sum() > 2000
    delta = np.mean(np.abs(before[0][mask, :3] - after[0][mask, :3]))
    assert delta > .1, f'Emission still baked: delta {delta}'
    # A flat receiver cannot light itself: its own emission must not enter
    # its diffuse atlas, even when the authored strength is much higher.
    principled.inputs['Emission Strength'].default_value = 20
    emission = bake_once(unit, source, output / 'DiffuseHigherEmission', True)
    assert np.allclose(after[0][mask, :3], emission[0][mask, :3], atol=.001)
    layer.layer_type = 'UNLIT'
    runtime = bake.BeautyBakeRuntime(bpy.context, unit)
    try:
        assert runtime.prepare() == 'READY'
        assert runtime.bake_kwargs()['type'] == 'COMBINED', 'Switch affected non-PBR'
    finally:
        runtime.cleanup()
    report.update({'output': str(output), 'mean_linear_rgb_delta': float(delta),
                   'signature_unchanged': True, 'source_channels_unchanged': True,
                   'diffuse_ignores_receiver_emission': True, 'other_layer_types_unchanged': True})
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print('PM_VR_PBR_DIFFUSE_OK', flush=True)


if __name__ == '__main__':
    main()
