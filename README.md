# PM VR

A production pipeline addon for preparing, baking, previewing, and exporting
Apple Vision Pro scenes from Blender 5.2.

The View3D sidebar is organized as four stages:

1. **Optimize** — the existing manual audit, naming, UV, texel-density, relink,
   and texture tools.
2. **Setup** — semantic render layers, layer-filtered bake units, per-unit
   resolution, and Day/Evening lighting configuration.
3. **Bake** — a persistent bake-unit queue with Beauty/Lightmap mode selection.
4. **Export** — checked semantic layers assembled from generated Beauty objects
   plus untouched Export Original objects and exported to USDZ/GLB.

Global source-root, lighting collections, worlds, bake defaults, and output
directories live in **Project Settings** (the gear button in the panel header).
The `?` button next to it lists the naming and scene rules the pipeline relies on.
Bake, export, validation and Setup changes are logged to
`PMVR_Logs/<blend name>_<date>.log` next to the `.blend` (in the system temp
folder while the file is unsaved). Every line is written immediately, so the log
survives a crash; Project Settings shows the path and opens the folder. The last
500 lines are also kept in the **PMVR Pipeline Log** text inside the `.blend`.

## Production Beauty workflow

Initialize the project, configure Source Root plus Day/Evening collections and
worlds, then create semantic render layers. A render layer's name is also its
export filename stem.

With a render layer active, click `+` to turn every selected mesh into a separate
bake unit; Shift-click `+` to create one shared-atlas unit from the selection.
`+` takes objects from other layers or units as they are (a unit left without
objects disappears), so moving objects never needs a separate remove step;
Shift-click `+` also merges existing units into one. `-` removes the highlighted
unit and takes its objects out of the layer; **Members** `+`/`-` edit a shared
unit. Glass, Emissive and Runtime layers are not baked: they list their objects
instead of units, and `+`/`-` add or remove the selection.
The unit list shows only the active layer. **Unassigned: N** in the Render
Layers header selects the objects visible now inside Source Root that belong
to no layer (lights excluded), to find what is still left to set up. Checkbox-select multiple rows with
Shift and changing one selected resolution applies it to that batch only. Select
any unit member and use **Add Selected Units** in Bake—the complete unit is queued
and duplicates are ignored.

New units receive an initial 1K/2K/4K resolution suggestion (4K at most) from the
`SimpleBake` UV channel and the project texel-density target. Missing or invalid
second UV channels safely fall back to the configured default resolution.

Bake and delivery sizes are separate, like SimpleBake's bake and output sizes.
Every unit bakes and is denoised at the **Bake Resolution** (Project Settings,
4096 by default; a unit set higher bakes at its own), and its PNG keeps that
size; Blender shows it. Export writes each atlas into the USDZ/GLB at its unit's
resolution, averaged in linear light by covered area, so colour and brightness
stay as baked (the mean moves by less than a tenth of an 8-bit step). The baked
file is not changed, so a unit's resolution can be lowered, or raised back up to
the baked size, without a rebake. **Island Padding** (Project Settings, 0.002
by default) is the gap the UV packer leaves between islands, in UV units (the
UVPackmaster Margin). The bake margin follows it at any bake size: a unit of one
object fills the whole gap, and in a shared unit each object fills half of it.
Objects of a unit bake one after another into one image, and Blender paints an
object's margin over every pixel that is not its own, a neighbouring island
too; a margin wider than the gap would repaint the edges of islands baked
earlier. The Bake stage **Test** switch (100/75/50/25%) bakes the
queue at a share of the Bake Resolution for quick checks; it is back at 100%
when a file opens. A unit whose baked file is smaller than its resolution (a test
bake, or a resolution raised above the baked size) shows it in its status, and
export warns about it.

**Material variants** (Unlit units of one object): **Add Variant** in the unit
details adds a name and a material that takes the place of the unit's
**Changes** material. The queue bakes the unit, then each variant, per lighting
state; a variant uses its material on the bake copies only, and keeps just a PNG
(`<Layer>_<Unit>_<Variant>[_Evening]_Beauty.png`). The row shows `DE` when both
states are baked and turns red when the unit was baked again since. USDZ export
writes, per the Mac contract, `Variants/<Object>_<Variant>.usdz` (and
`_Evening.usdz`) holding only the object under its scene name, in place, with the
variant's colour at the unit's resolution; the optional **Marker** Empty goes in
as `VariantMarker`. It also writes `Variants/materialVariants.json`, the
`materialVariants` block for LevelManifest.json. Swatches are made by hand:
put `<Object>_<Variant>_swatch.jpg` (and `<Object>_swatch.jpg` for the default,
which then joins the manifest) into `Variants/`; export never writes or
replaces them. The default variant is the object
in the main scene and has no file.

**Flatten to Texture** (Shader Editor, right-click or the Node menu) turns
selected nodes, such as an image through Color Ramp, Hue/Saturation or Math,
into one Image Texture: the selection is rendered texel by texel and saved as
a PNG in `PMVR_Flattened` next to the .blend. The dialog asks for the long side,
the largest image's by default; the other side keeps its aspect ratio, and a
smaller size averages the texels down (an oversized 4K roughness can become 1K)
(sRGB into colour inputs, Non-Color into values and normal maps). Coordinates
left outside the selection (UV Map, Mapping) stay connected, so tiling and
resolution are kept; a selected Mapping is baked in. USD then gets exactly what
the material shows. Nodes that depend on the surface or the view (Geometry,
Layer Weight, AO, procedural textures without UV input) are refused. Generated
PBR materials are copied from the source at bake time, so flatten before the
bake (or rebake the unit).

Beauty bake uses Cycles Combined at 256 samples by default, shared image targets,
SimpleBake-style image-only compositor denoise, and a single PNG atlas per unit/state.
Day and Evening may be checked together and are processed sequentially. Source objects,
mesh geometry, materials, UV coordinates, modifiers, and collection membership are never edited.
The first UV channel is activated for baking as a safety measure; authored UV data is unchanged.
Generated objects remain separate and are owned through stable IDs rather than names.

Each unit is committed as a transaction: the PNG is written beside its final
path and replaces the previous file only after the generated result has been
prepared and checked. A unit that fails or is cancelled leaves the previous
PNG, generated mesh and materials current. Press **Esc** (or **Cancel Bake**)
to stop the whole queue: the running Cycles pass stops, the unit in progress
is discarded, and units finished earlier in the queue are kept. Adding or
removing layers and units is disabled while a bake runs. Day and Evening
materials are both kept in the `.blend` even though a generated object shows
one state at a time. If a file is saved during a bake, or Blender stops in the
middle of one, the temporary bake visibility and work data are restored the
next time the file is opened.

## Bake scenarios

A bake scenario is a saved set of enabled and disabled collections inside
Source Root (the outliner's Exclude checkbox), so a unit bakes only with the
part of the scene that matters to it. In **Bake > Bake Scenarios**, switch
collections in the outliner and click `+` to save them; **Capture Outliner**
re-records the active scenario and **Show in Outliner** switches the outliner
to it for checking (Ctrl+Z switches back). The checkbox list edits a scenario
directly; disabling a collection also disables everything inside it.

A render layer names the default scenario for its units; any unit can
override it with another scenario or **No Scenario** (outliner as it is),
in the queue row or in Setup. Checked units change together, like resolution.
The queue switches collections before each unit and restores the outliner
when it ends, is cancelled, or is interrupted (a file saved during the bake
or recovered after a crash is restored on load). Day/Evening lighting
collections and the collections that hold them are never switched by a
scenario: the Lighting switch turns a state on with every collection nested
in its lighting collection and turns the other state off whole. Collections
created later join every scenario automatically, as they are in the outliner
at that moment, and deleted ones leave. The queue does not start when a scenario would
disable a unit's own objects; **Validate Pipeline** reports the same.

Every semantic layer has one authoritative type: `Unlit`, `PBR`, `Alpha`,
`Translucent`, `Glass`, `Emissive`, or `Runtime`. The type directly
defines both its runtime meaning and Blender behavior:

- `Unlit` and `Translucent`: baked Base Color on `SimpleBake` in a simple Principled material;
- `PBR`: baked Base Color plus original Metallic/Roughness/Normal on `UVMap`;
- `Alpha`: baked Base Color plus the source's opacity on `UVMap`. The opacity is
  found in a Principled Alpha input or in a Mix Shader with a Transparent BSDF
  (its factor), also inside node groups when it comes in through a group input
  (like foliage shaders with an Opacity input). The generated material is one
  Principled; translucency and mixing are in the bake;
- `Glass`, `Emissive`, and `Runtime`: export original source data.

`Runtime` contains application-driven data such as visual FX/video surfaces,
SFX placement points, probe cameras, UI anchors, navigation, and collision
geometry. Cameras are included in USDZ/GLB export; conversion to empties can
be added later if needed.

Scene Debug lives in Optimize and can be used before pipeline initialization.
UV Health, Texel Density, Checker, Scale, and Linked Meshes inspect all visible
scene meshes. Bake Status, Render Layers, and Bake Units become available after
the semantic pipeline is initialized.

Day uses the base output filename; Evening adds `_Evening`. Export is blocked when
the current state has no compatible Beauty artifact, when that state's generated
materials are missing, or when a generated object has been duplicated. Export
temporarily shows the exported state's materials and restores the viewport
preview afterwards. A baked child whose parent is baked in another unit of the
same layer stays parented to that generated parent.

Export follows the setup, not the file's current visibility: objects in
disabled collections, with the render toggle off, hidden with H or behind
Show Generated off are all exported, and the file's visibility is unchanged
afterwards. The only state-specific rule is the lighting collections: an
object that lives only inside the Evening collection is exported in Evening
and left out of Day (and the other way round).

To include a source object in another layer's export file, open **Export**,
select the destination layer, select the source objects in the viewport, and
click **Include Selected**. The box lists additional objects in that file;
the `X` beside an object removes its assignment, while **Select Additional
Objects** and **Remove Selected** support batch changes. One source may be
included in several additional layers of the same type. Its primary layer and
bake unit stay unchanged, so no second bake is needed. The output files contain
the selected geometry and its existing
materials/textures; no separate manifest is generated.

Lightmap uses the same unit queue and shared atlas, but produces separate
scene-linear EXR images and generated objects/materials. Switching modes never
overwrites the other mode's artifacts. Its controls are hidden while it is not
in production use (`SHOW_LIGHTMAP` in `pipeline/ui.py`).

## Legacy tools

The original Lightmap Baker, material-pair rebuild operator, and collection
exporter remain registered as backend and compatibility code, but are hidden
from the main production interface.

The complete design and implementation contract is in
[`docs/PIPELINE_IMPLEMENTATION_PLAN.md`](docs/PIPELINE_IMPLEMENTATION_PLAN.md).

## Installation

Install or link this directory as the `PM_VR` Blender addon, then enable **PM VR** in Blender preferences. The tools appear in **View3D > N-Panel > PM VR**.
