"""Temporary white bake receiver that preserves non-camera light transport."""

import bpy

from .constants import BAKE_UV_NAME


def _active_output(tree):
    outputs = [
        node
        for node in tree.nodes
        if node.type == 'OUTPUT_MATERIAL'
    ]
    active = [node for node in outputs if node.is_active_output]
    if active:
        return active[0]
    if outputs:
        return outputs[0]
    return tree.nodes.new("ShaderNodeOutputMaterial")


def _fallback_original_shader(tree, material, location):
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.label = "Original Material Fallback"
    principled.location = location
    base_color = principled.inputs.get("Base Color")
    if base_color:
        base_color.default_value = tuple(material.diffuse_color)
    metallic = principled.inputs.get("Metallic")
    if metallic and hasattr(material, "metallic"):
        metallic.default_value = material.metallic
    roughness = principled.inputs.get("Roughness")
    if roughness and hasattr(material, "roughness"):
        roughness.default_value = material.roughness
    return principled.outputs["BSDF"]


def _wrap_surface_for_bake(material):
    tree = material.node_tree
    output = _active_output(tree)
    surface = output.inputs["Surface"]
    original_socket = (
        surface.links[0].from_socket
        if surface.is_linked
        else _fallback_original_shader(
            tree,
            material,
            (output.location.x - 560.0, output.location.y + 120.0),
        )
    )
    for link in list(surface.links):
        tree.links.remove(link)

    displacement = output.inputs.get("Displacement")
    if displacement:
        for link in list(displacement.links):
            tree.links.remove(link)

    white = tree.nodes.new("ShaderNodeBsdfDiffuse")
    white.label = "White Lightmap Receiver"
    white.location = (
        output.location.x - 520.0,
        output.location.y - 80.0,
    )
    white.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    if white.inputs.get("Roughness"):
        white.inputs["Roughness"].default_value = 1.0

    light_path = tree.nodes.new("ShaderNodeLightPath")
    light_path.label = "Bake-Ray Switch"
    light_path.location = (
        output.location.x - 760.0,
        output.location.y - 160.0,
    )
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.label = "Original Transport / White Receiver"
    mix.location = (
        output.location.x - 250.0,
        output.location.y,
    )
    tree.links.new(light_path.outputs["Is Camera Ray"], mix.inputs[0])
    tree.links.new(original_socket, mix.inputs[1])
    tree.links.new(white.outputs["BSDF"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], surface)


def _add_bake_target(material):
    tree = material.node_tree
    uv_node = tree.nodes.new("ShaderNodeUVMap")
    uv_node.label = 'Bake UV: "SimpleBake"'
    uv_node.uv_map = BAKE_UV_NAME
    uv_node.location = (-760.0, -420.0)
    image_node = tree.nodes.new("ShaderNodeTexImage")
    image_node.label = "Temporary Bake Target"
    image_node.location = (-520.0, -420.0)
    tree.links.new(uv_node.outputs["UV"], image_node.inputs["Vector"])
    return image_node


class TemporaryReceiver:
    """Use private copies so source mesh and material datablocks stay untouched."""

    def __init__(self, source):
        self.source = source
        self.original_mesh = source.data
        self.original_material = source.material_slots[0].material
        self.original_material_link = source.material_slots[0].link
        self.mesh = None
        self.material = None
        self.image_node = None

    def __enter__(self):
        try:
            self.mesh = self.original_mesh.copy()
            self.mesh.name = f"__PM_LM_RECEIVER_{self.source.name}"
            self.material = self.original_material.copy()
            self.material.name = f"__PM_LM_RECEIVER_{self.source.name}"
            if not self.material.use_nodes:
                self.material.use_nodes = True
            _wrap_surface_for_bake(self.material)
            self.image_node = _add_bake_target(self.material)

            self.mesh.materials.clear()
            self.mesh.materials.append(self.material)
            self.source.data = self.mesh
            self.source.material_slots[0].link = 'DATA'
            self.source.material_slots[0].material = self.material

            uv_layer = self.mesh.uv_layers.get(BAKE_UV_NAME)
            if uv_layer:
                self.mesh.uv_layers.active = uv_layer
            return self
        except Exception:
            self._restore_and_cleanup()
            raise

    def set_target_image(self, image):
        tree = self.material.node_tree
        self.image_node.image = image
        for node in tree.nodes:
            node.select = False
        self.image_node.select = True
        tree.nodes.active = self.image_node

    def _restore_and_cleanup(self):
        try:
            if (
                self.source
                and bpy.data.objects.get(self.source.name) is self.source
            ):
                self.source.data = self.original_mesh
                self.source.material_slots[0].link = self.original_material_link
                self.source.material_slots[0].material = self.original_material
        finally:
            if self.mesh and self.mesh.users == 0:
                bpy.data.meshes.remove(self.mesh)
            if self.material and self.material.users == 0:
                bpy.data.materials.remove(self.material)

    def __exit__(self, exc_type, exc_value, traceback):
        self._restore_and_cleanup()
