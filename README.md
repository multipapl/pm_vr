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
export filename stem. Assign non-baked geometry and empties as `Export Original`.

With a render layer active, click `+` to turn every selected mesh into a separate
bake unit; Shift-click `+` to create one shared-atlas unit from the selection.
The unit list shows only the active layer. Checkbox-select multiple rows with
Shift and changing one selected resolution applies it to that batch only. Select
any unit member and use **Add Selected Units** in Bake—the complete unit is queued
and duplicates are ignored.

New units receive an initial 1K/2K/4K/8K resolution suggestion from the
`SimpleBake` UV channel and the project texel-density target. Missing or invalid
second UV channels safely fall back to the configured default resolution. The
Bake stage provides queue-wide `÷2` and `×2` controls for quick test passes.

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

Every semantic layer has one authoritative type: `Unlit`, `PBR`, `Alpha`,
`Translucent`, `Glass`, `Emissive`, or `Runtime`. The type directly
defines both its runtime meaning and Blender behavior:

- `Unlit` and `Translucent`: baked Base Color on `SimpleBake` in a simple Principled material;
- `PBR`: baked Base Color plus original Metallic/Roughness/Normal on `UVMap`;
- `Alpha`: baked Base Color plus original Alpha on `UVMap`;
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
overwrites the other mode's artifacts.

## Legacy tools

The original Lightmap Baker, material-pair rebuild operator, and collection
exporter remain registered as backend and compatibility code, but are hidden
from the main production interface.

The complete design and implementation contract is in
[`docs/PIPELINE_IMPLEMENTATION_PLAN.md`](docs/PIPELINE_IMPLEMENTATION_PLAN.md).

## Installation

Install or link this directory as the `PM_VR` Blender addon, then enable **PM VR** in Blender preferences. The tools appear in **View3D > N-Panel > PM VR**.
