# PM Lightmap Baker v1

Status: implemented in `modules/lightmap_baker`.

Future work is tracked separately in [ROADMAP.md](ROADMAP.md).

Automated Blender smoke coverage includes colored HDR lighting, compositor
denoise with albedo/normal guides, material hookup, UV preservation, internal
and PIZ OpenEXR output, resolution override, skip/fallback behavior, state
restoration, and transactional rebaking. Final visual approval should still be
performed on representative production scenes.

## Goal

Add a focused lightmap baker to PM VR. Its workflow should feel familiar to
SimpleBake, but include only the functionality required by the current
Blender-to-USD pipeline.

The baker must:

1. Bake a classic, non-directional RGB lightmap for each requested object.
2. Create an `_LM` copy of the object and its material.
3. Connect the lightmap to the copied material through a Multiply operation
   immediately before Principled BSDF Base Color.
4. Preserve the original PBR material inputs, including normal, roughness,
   metallic, and reflection-related inputs.
5. Keep every original UV channel. In particular, it must never remove
   `UVMap` after baking.

## Lightmap definition

The output is a classic diffuse lighting lightmap, independent of the target
object's Base Color.

It includes:

- direct diffuse lighting;
- indirect diffuse lighting and bounced light;
- shadows and natural occlusion produced by the Cycles light transport;
- colored lights;
- World lighting;
- emissive objects acting as light sources;
- color bleeding from the actual materials in the scene.

It does not bake the receiver's:

- Base Color/albedo;
- specular or reflections;
- roughness or metallic appearance;
- transmission;
- normal-map or bump-map detail;
- self-emission as a surface appearance term.

All objects visible to the active Cycles render/view layer participate in light
transport. Only valid objects in the bake list receive lightmaps.

Do not add a separate AO multiplication pass. Occlusion that naturally results
from Cycles lighting is already part of the lightmap.

## UI location

Create a new top-level PM VR category:

`LIGHTMAP BAKER`

Do not place this workflow inside VR Project, Utilities, or the existing
SimpleBake reference add-on.

## Bake list

Store the list on the active Scene so it persists in the `.blend` file.

Required controls:

- Add Selected;
- Remove;
- Clear;
- Refresh;
- Bake Lightmaps.

Do not add list reordering, highlighting, high/low matching, cages, or
Selected-to-Active controls in v1.

Each row contains:

- an object reference and object name;
- `Use Resolution Override`, disabled by default;
- a per-object square resolution field, enabled only when the override is on.

Only mesh objects can be added. Do not add the same object twice. Generated
`_LM` results must not be accepted as new source objects.

## Resolution

Global default:

`2048 × 2048`

Supported resolutions are square powers of two:

- 256;
- 512;
- 1024;
- 2048;
- 4096;
- 8192.

When an object's override is disabled, use the global resolution. When it is
enabled, use the value stored on that bake-list item.

## Validation and skip behavior

Validate every list item independently before processing it.

Skip the object and continue the batch when:

- the object no longer exists;
- it is not a mesh;
- it does not contain a UV layer named exactly `SimpleBake`;
- it has zero materials, an empty material slot, or more than one material;
- a user-owned untagged datablock prevents safe creation of the `_LM` result;
- another unrecoverable per-object validation error occurs.

Every skipped object must produce a clear log entry containing its name and the
reason. One invalid object must not stop other valid objects from baking.

The baker must not create or unwrap a missing `SimpleBake` UV layer.

## Cycles behavior

Use the active scene's Cycles render setup. Do not duplicate these settings in
the add-on UI:

- samples and adaptive sampling;
- render device;
- light-path and bounce settings;
- clamping;
- scene lights and World;
- object, collection, ray, and render visibility.

If necessary, switch the active scene to Cycles temporarily and restore the
previous render engine after the batch.

Use a separate global `Margin` field in the Lightmap Baker panel instead of the
scene's Bake Margin. Default to 16 pixels.

The implementation technique may use a temporary receiver shader or native
Cycles bake contributions, but the resulting texture must satisfy the
lightmap definition above. The chosen method must be validated with colored
lights and color bleeding before it is treated as complete.

## Compositor denoise

Denoise the baked lightmap through Blender's compositor.

Requirements:

- use the active scene's compositor context and relevant scene settings;
- provide the lightmap image and appropriate temporary albedo/normal guide
  data to the compositor denoiser;
- preserve HDR values;
- expose no separate sample or denoiser settings in the add-on;
- require no named nodes or manual node contract from the user;
- any helper nodes, links, guide images, scenes, or outputs must be temporary;
- restore the user's compositor graph and settings exactly after processing,
  including on failure or cancellation;
- leave no visible technical nodes after the operation.

If compositor denoising fails, preserve the raw bake, report a warning, and
continue safely. Do not destroy a previous successful `_LM` result because a
replacement failed.

## UV requirements

Use the UV layer named exactly `SimpleBake` as the bake target.

On the generated object:

- retain all UV layers from the source;
- retain their original names, contents, and order;
- keep the original primary UV channel, normally `UVMap`;
- keep `SimpleBake`;
- do not move `SimpleBake` to the first position;
- do not delete any unrelated UV layers;
- use an explicit UV Map shader node for the lightmap, so changing the
  render-active UV layer is unnecessary.

This intentionally differs from SimpleBake's Copy and Apply behavior, which
removes every UV layer except the bake UV on the generated copy.

## Generated object and material

For each successful source object named `{Source}`:

- create object `{Source}_LM`;
- create a single-user mesh copy;
- create material `{Source}_LM`;
- place the object in collection `PM_Lightmap_Bakes`;
- preserve transforms, parenting, mesh data, and existing modifiers;
- do not apply modifiers automatically;
- hide the source object after the new result has been completed successfully.

Hide the successful source from both normal viewport display and rendering so
it does not overlap the `_LM` result. Do not change the source's persistent
visibility if its bake is skipped or fails.

The original object, mesh, material, and node tree must otherwise remain
unchanged.

## Material node setup

The supported automatic hookup case is one unambiguous Principled BSDF in the
single copied material.

Create this chain immediately before its Base Color input:

```text
Existing Base Color source/default ──> Mix Color A
UV Map ("SimpleBake") ──> Lightmap Image ──> Mix Color B
Mix Color (Multiply, Factor 1.0) ──> Principled BSDF Base Color
```

Requirements:

- preserve the existing Base Color link when present;
- if Base Color is unlinked, copy its existing default value into Mix Color A;
- set the Mix Color blend operation to Multiply with factor `1.0`;
- do not clamp the multiplication;
- connect an explicit UV Map node set to `SimpleBake`;
- assign the generated EXR to the Image Texture node;
- keep all other Principled inputs and material nodes untouched;
- lay out and label the added nodes clearly.

If no unambiguous Principled BSDF can be found:

- still copy the object and material;
- add `UV Map ("SimpleBake") -> Lightmap Image` near the active Material
  Output;
- leave the lightmap image unconnected;
- write a warning to the log;
- treat the lightmap bake itself as successful.

## Image and disk output

Add:

- `Export to Disk` toggle;
- output-directory field.

Defaults:

- export enabled;
- directory `//Lightmaps/`;
- file name `{Source}_LM.exr`;
- OpenEXR;
- RGB;
- 32-bit float;
- PIZ lossless compression.

Keep the resulting image datablock loaded in Blender and assigned to the copied
material.

Do not call Blender's Pack operation. If Blender's own automatic resource
packing is enabled, allow Blender to handle that when the user saves the file.

Store the lightmap as scene-linear data. Do not bake AgX, Standard, exposure,
look, display, or view-transform output into the EXR. The Image Texture must
interpret the EXR through the scene-linear color-space role.

When export is disabled, retain the generated image as an unpacked internal
image datablock. Temporary files needed for compositor processing must be
cleaned up.

## Repeat bake and ownership

Tag every generated object, mesh, material, and image with PM Lightmap metadata
that identifies:

- the source object;
- the generated asset type;
- the bake version/operation.

Rebaking a source replaces its previous tagged `_LM` object, material, image,
and exported EXR.

Replacement must be transactional:

1. Keep the previous successful result until the new bake and material setup
   have succeeded.
2. Prevent the previous `_LM` copy from contributing duplicate geometry or
   lighting during the rebake.
3. Commit the new result.
4. Remove the previous tagged result.

Never delete or overwrite an untagged user datablock merely because its name
ends in `_LM`.

## State restoration and failure safety

Restore all temporary state after the batch, including after an exception or
user cancellation:

- active object and selection;
- current mode;
- active UV layers;
- temporary bake target nodes;
- temporary receiver materials;
- render engine and any temporarily changed bake settings;
- object and collection visibility used internally;
- compositor nodes, links, outputs, and settings;
- temporary guide images and files.

Only these persistent changes are intended:

- successful `_LM` results;
- exported EXR files;
- source objects hidden after successful completion;
- bake-list and panel settings;
- PM metadata on generated assets.

## Logging

Log at least:

- batch start and finish;
- each object's start;
- chosen resolution;
- raw bake completion;
- denoise completion or warning;
- exported file path;
- material hookup or unconnected fallback;
- replacement of a previous `_LM` result;
- skip/error reason.

Finish with a concise summary:

- successful;
- skipped;
- completed with warnings;
- failed.

During a foreground bake, mirror the live log into a 3D View overlay. Show the
current object, current bake/denoise/export stage, elapsed time, and overall
batch progress. The overlay must clean itself up after completion and when the
add-on is disabled. Do not use Blender's status-bar progress API because it
changes the cursor and conflicts with Cycles' own bake progress.

## Non-goals for v1

Do not implement:

- automatic UV generation or packing;
- more than one material per object;
- per-material lightmaps;
- atlases or merged-object bakes;
- UDIM;
- Selected-to-Active, cages, or high/low workflows;
- background/headless baking;
- PBR texture baking other than the lightmap;
- AO multiplication;
- PNG/JPEG conversion;
- automatic USD export;
- RealityKit directional lightmaps;
- permanent compositor templates;
- presets.

## Acceptance checks

The first version is complete when the following are verified:

1. A colored-light test produces a correctly colored lightmap independent of
   the receiver Base Color.
2. Direct light, indirect light, emissive lighting, World lighting, shadows,
   and color bleeding are present.
3. Reflections, material normal-map detail, metallic/specular appearance, and
   Base Color are absent from the lightmap.
4. Multiplying the source Base Color by the lightmap gives the expected static
   diffuse appearance in Blender within the known limitations of a
   non-directional lightmap.
5. `UVMap`, `SimpleBake`, and any additional source UV layers all remain on the
   `_LM` object in their original order.
6. The material node chain is inserted without disturbing unrelated PBR
   inputs.
7. An unsupported material receives an unconnected lightmap texture and a
   warning instead of failing the batch.
8. Missing `SimpleBake` UV and multiple-material objects are skipped and
   logged.
9. Global resolution and per-object override both work.
10. EXR output is scene-linear, 32-bit RGB, PIZ-compressed, and not
    automatically packed.
11. Compositor denoise leaves no temporary nodes or resources behind.
12. A repeated bake replaces only the previous PM-generated result.
13. Original objects are hidden only after their replacement succeeds.
14. User selection, mode, render settings, visibility, and compositor graph
    are restored after success, failure, and cancellation.
15. Foreground baking shows live viewport feedback and leaves no draw handler
    or progress state behind after completion.
