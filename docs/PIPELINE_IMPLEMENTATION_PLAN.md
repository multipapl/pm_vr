# PM VR Pipeline Machine — implementation plan

**Status:** working implementation specification
**Target:** Blender 5.2, PM VR add-on
**Scope:** Blender-side authoring, setup, bake orchestration, generated artifacts and USDZ/GLB export
**Out of scope:** changes to the macOS Asset Manager and UP_AVP runtime, except where their existing contracts constrain Blender output

> Current taxonomy amendment: one authoritative `layer_type` defines both
> runtime meaning and Blender behavior. Its values are Unlit, PBR, Alpha,
> Translucent, Glass, Emissive, Video, and Runtime. There is no parallel
> processing-profile entity. Later sections using the former
> Scene/Reflect/Translusent/Curtains terminology are legacy examples.

---

## 1. Product objective

Transform PM VR from a collection of independent utilities into a project-specific pipeline machine with four explicit stages:

1. **Optimization** — deliberate manual operations on source content.
2. **Setup** — persistent description of render layers, bake units, resolutions, states and export policy.
3. **Bake** — a unit-based queue with isolated Day/Evening and Lightmap/Beauty artifacts.
4. **Export** — one-command assembly and export of an active, selected, or configured set of render layers.

The user decides artistic semantics:

- which objects belong to which output layer;
- which objects share one baked texture;
- how the shared `SimpleBake` UV is packed;
- which collections are enabled for a particular bake batch;
- which resolution each bake unit uses.

The add-on persists these decisions, validates them, repeats the mechanical work, and never treats generated data as source of truth.

---

## 2. Non-negotiable rules

### 2.1. Source preservation

Normal Setup, Bake, Preview, Selection and Export operations must not modify source originals:

- object, mesh or material names;
- geometry or topology;
- transforms or parenting;
- material slots or node graphs;
- UV layers;
- modifiers;
- authoring collection membership.

The Optimization tab is the intentional exception. Its existing audit/fix tools may modify source data because the command itself clearly requests that change.

Pipeline identity and membership metadata may be written to source objects only through explicit Setup operations.

### 2.2. No name-based identity

Object, mesh and material display names are not stable identifiers.

Names remain contracts only for external runtime entities that intentionally use them, for example anchors, probes, teleport points or other named AVP targets. Pipeline membership and generated ownership must use UUIDs.

Renaming a normal source object must not break:

- render-layer membership;
- bake-unit membership;
- source-to-generated mapping;
- Day/Evening artifacts;
- rebake or export.

### 2.3. Authoring collections are not export semantics

Collections remain free for scene organization and manual visibility control. Render layers are persistent metadata, not Blender collections.

The one collection with special pipeline meaning is the configured **Source Root Collection**, which bounds the scene that may contribute to a bake.

### 2.4. Safe replacement

A previous successful generated result is replaced only after the complete new result for that bake unit has succeeded and passed validation.

Partial output must never become current output.

### 2.5. Explicit state isolation

The following artifact dimensions must never overwrite one another:

- Day versus Evening;
- Beauty versus Lightmap;
- one bake unit versus another;
- work-in-progress versus last successful output.

---

## 3. User-interface architecture

Replace the current stack of simultaneously expandable categories with one stage selector:

```text
[ Optimization | Setup | Bake | Export ]              [Project Settings ⚙]
```

Only the active stage is drawn in the main panel.

Global settings live behind **Project Settings** and do not occupy the daily workflow UI.

### 3.1. Optimization

Move or retain the existing manual tools here:

- source audit;
- object/mesh/material name synchronization;
- UV channel preparation and activation;
- texel-density analysis and labels;
- texture relink;
- texture externalization;
- future explicit geometry/material optimization utilities.

Every operation that mutates source data must remain visibly separated from Setup, Bake and Export.

### 3.2. Setup

Setup owns persistent project semantics:

- render-layer definitions;
- object assignment;
- per-object processing role;
- bake-unit definitions;
- bake-unit membership;
- resolution;
- validation and issue navigation.

### 3.3. Bake

Bake is centered on a persistent queue of bake units, not on `Bake All`.

The common production workflow is:

```text
prepare scene visibility manually
→ activate Day or Evening
→ select one or more relevant objects
→ Add Selected Units to Queue
→ inspect/adjust queue
→ Bake Queue
→ fix problem objects
→ add their units again
→ rebake only those units
```

### 3.4. Export

Export works on semantic render layers and the active lighting state. It does not depend on current object selection or authoring collection layout.

---

## 4. Global Project Settings

Create a dedicated Project Settings dialog/panel with these fields.

### 4.1. Scene setup

```text
Source Root Collection        Collection pointer
```

The Source Root contains all authoring geometry and state collections that are allowed to affect baking.

Objects outside Source Root are temporarily excluded from bake evaluation, even if visible in the active View Layer.

### 4.2. Lighting states

```text
Day
  Lighting Collection         Collection pointer
  World                       World pointer

Evening
  Lighting Collection         Collection pointer
  World                       World pointer
```

Both lighting collections should be descendants of Source Root.

The lighting collections may contain nested collections. In particular:

```text
Day Lighting
  lights
  Lamp_OFF objects

Evening Lighting
  lights
  Lamp_ON objects
```

State activation changes only:

- the `LayerCollection.exclude` state of the configured Day and Evening lighting collections;
- `Scene.world`.

It does not bake, export, change arbitrary child visibility, or change generated preview mode.

### 4.3. Bake defaults

```text
Margin                       integer, initial default 16 px
Beauty output directory     //Beauty_Bakes/
Lightmap output directory   //Lightmaps/
```

Resolution is not global. It belongs to each bake unit.

Most technical bake settings are embedded in bake profiles rather than duplicated in the main UI.

### 4.4. Export settings

```text
USDZ output directory
GLB output directory
```

Existing format-specific exporter presets remain backend implementation details.

### 4.5. Schema metadata

```text
Pipeline schema version
Project initialized flag
Project/project-content identifier if later required by the Mac handoff
```

The schema version is required from the first release so future `.blend` migrations can be explicit.

---

## 5. Lighting-state controller

### 5.1. State UI

Expose a simple selector in Setup/Bake/Export context:

```text
[ Day | Evening ]
```

Switching it calls one state activation service. UI operators must not reimplement state changes independently.

### 5.2. Day activation

```text
Day Lighting LayerCollection.exclude      = False
Evening Lighting LayerCollection.exclude  = True
Scene.world                                = Day World
```

### 5.3. Evening activation

```text
Day Lighting LayerCollection.exclude      = True
Evening Lighting LayerCollection.exclude  = False
Scene.world                                = Evening World
```

### 5.4. Manual collection visibility

The user manually excludes child collections to control bake cost and memory. The pipeline must respect that work.

During state activation or bake:

- do not enable arbitrary descendants of Source Root;
- do not infer contributors from render-layer membership;
- preserve every unrelated `LayerCollection.exclude` value;
- temporarily enable only the ancestor path required to reach Source Root and the active state collection;
- restore the complete tree after a scoped bake/export operation when temporary isolation was used.

### 5.5. Ambiguous collection links

A Blender `Collection` may appear more than once in a View Layer hierarchy. A Collection pointer alone may therefore resolve to multiple `LayerCollection` instances.

Validation must block state switching/baking when:

- Source Root is not present in the active View Layer;
- a configured lighting collection is not present;
- a configured collection resolves through more than one hierarchy path;
- Day and Evening point to the same collection;
- Day is nested inside Evening or vice versa;
- either lighting collection is outside Source Root.

### 5.6. Bake evaluation universe

There is no separate Bake Contributor metadata.

```text
Bake contributors
  = all render-visible evaluated content inside Source Root
    after the active Day/Evening state is applied
```

Objects in layers using `Export Original` still contribute to lighting, shadows, transmission, reflection and emission when visible.

Examples include Glass, Emission, Fire, Water and `Lamp_ON`.

---

## 6. Persistent data model

Use Blender `PropertyGroup` data stored in the `.blend` file.

### 6.1. Project data

Proposed logical structure:

```text
PMVRProjectSettings
  schema_version
  source_root_collection
  day_lighting_collection
  day_world
  evening_lighting_collection
  evening_world
  active_lighting_state
  margin
  beauty_output_directory
  lightmap_output_directory
  usdz_output_directory
  glb_output_directory
  render_layers[]
  active_render_layer_index
  bake_units[]
  active_bake_unit_index
  bake_queue[]
  active_bake_queue_index
  build_records[]
```

### 6.2. Render-layer definition

```text
PMVRRenderLayer
  layer_id                 immutable UUID
  display_name             e.g. LO_Unlit; also the export filename stem
  enabled
  layer_type
  export_usdz
  export_glb
```

The Day filename is the base output name:

```text
LO_Unlit.usdz
```

Evening automatically adds the fixed suffix:

```text
LO_Unlit_Evening.usdz
```

Do not store a second manually editable Evening filename.

Prefixes such as `LO_` and `TR_` are ordinary parts of the layer/output name. They do not introduce another pipeline hierarchy.

`LO_Unlit` and `TR_Unlit` are different render layers with the same layer type.

### 6.3. Layer types

Initial closed enum:

```text
UNLIT
PBR
ALPHA
TRANSLUCENT
GLASS
EMISSIVE
VIDEO
RUNTIME
```

Behavior is derived directly from this single value:

- Unlit and Translucent use the simple unlit Beauty processor.
- PBR bakes Base Color and preserves PBR channels.
- Alpha bakes Base Color and preserves Alpha.
- Glass, Emissive, Video, and Runtime export original source data.

Do not infer the type from the portion of the layer name after `_`. The saved enum is authoritative.

### 6.4. Source-object metadata

Attach a dedicated object-level PropertyGroup:

```text
PMVRObjectMetadata
  source_id                immutable UUID for a registered source
  render_layer_id
  processing_role
  bake_unit_id             required only for Bake role
  is_registered_source
```

Processing roles:

```text
BAKE
EXPORT_ORIGINAL
UNASSIGNED
```

Suggested UI labels:

```text
Bake
Export Original
Unassigned
```

Rules:

- Empty objects use `Export Original` and never belong to a bake unit.
- A mesh inside a bake-capable layer may be either `Bake` or `Export Original`.
- Homepod screen geometry and UI empties use `Export Original`.
- A layer with profile `Export Original` forces all its members to that role.
- `UNASSIGNED` is a validation state, not a silent export behavior.

### 6.5. Bake-unit definition

```text
PMVRBakeUnit
  unit_id                  immutable UUID
  display_name
  artifact_key             immutable filesystem-safe key
  render_layer_id
  resolution              256..8192 power-of-two enum
  enabled
```

Membership is resolved from source-object `bake_unit_id` and verified against the unit definition.

One bake unit means:

- one resolution;
- one shared `SimpleBake` atlas in the 0–1 tile;
- one shared baked image per lighting state and bake mode;
- one or more separate generated objects.

It does **not** imply permanently joined geometry.

### 6.6. Bake queue entry

```text
PMVRBakeQueueEntry
  unit_id
```

The queue is persistent in the `.blend` until explicitly cleared.

State and bake mode are selected at execution time and are not duplicated into every queue row.

### 6.7. Generated ownership metadata

Every generated object, mesh, material and image must be tagged with stable metadata:

```text
pmvr_generated             true
pmvr_asset_type            OBJECT / MESH / MATERIAL / IMAGE
pmvr_source_id             for per-source assets
pmvr_unit_id
pmvr_layer_id
pmvr_lighting_state        DAY / EVENING when state-specific
pmvr_bake_mode             BEAUTY / LIGHTMAP
pmvr_build_operation_id
pmvr_generator_version
pmvr_schema_version
```

Names are for display and export contracts, never for ownership decisions.

### 6.8. Duplicate source IDs

Blender duplication may copy object custom properties and therefore clone `source_id`.

Validation blocks Bake and Export when duplicate source IDs exist.

Keep an explicit repair operator available as a backend action:

```text
Register Duplicate as New Source
```

It assigns a new UUID and clears inherited unit assignment. It is intentionally
not a permanent main-panel button; validation reports the problem first.

Do not silently regenerate IDs during ordinary validation, file load, bake or export.

---

## 7. Render-layer matrix

The current project uses output names including:

```text
LO_Clocks
LO_Curtains
LO_Emission
LO_Fire
LO_Glass
LO_Homepod
LO_Navmesh
LO_Probes
LO_Reflect      → may be renamed to LO_PBR
LO_Scene
LO_Translusent
Skybox
TR_Emission
TR_Glass
TR_Navmesh
TR_Probes
TR_Scene
TR_Translusent
TR_Water
```

Expected profile families:

| Name suffix/family | Saved profile | Notes |
|---|---|---|
| Scene | Scene / Beauty | Baked color, simple Principled output |
| Curtains | Scene / Beauty | Same Blender behavior as Scene; AVP handles it differently |
| Homepod | Scene / Beauty | Mix of baked geometry, `Export Original` screen geometry and empties |
| PBR, formerly Reflect | PBR / Beauty | Baked Base Color plus restored original PBR inputs |
| Translusent | Translucent / Beauty | Baked Base Color plus original Alpha branch |
| Clocks, Emission, Fire, Glass, Navmesh, Probes, Skybox, Water | Export Original | Still visible bake contributors when enabled |
| Lamp_OFF | Scene / Beauty | Lives under Day collection; baked and exported for Day |
| Lamp_ON | Export Original | Lives under Evening collection; contributes emission and exports as source |

Keep the existing external spelling `Translusent` where required by filenames. The internal enum and UI may use the correctly spelled `Translucent` without silently renaming existing output contracts.

---

## 8. Bake-unit geometry and UV contract

### 8.1. Separate generated objects

If one unit contains objects A, B and C, successful output contains generated A, B and C, not one permanently joined mesh.

All generated members reference the same unit image.

Benefits:

- stable one-to-one source/generated mapping;
- preserved transforms and hierarchy;
- simpler selection and issue navigation;
- safer transactional replacement;
- stable AVP entity structure;
- easier comparison between Day and Evening.

If runtime draw-call optimization is later required, merging belongs to a separate export optimization pass, not to bake semantics.

### 8.2. Shared atlas

The user prepares the `SimpleBake` UV manually.

For every enabled member of a unit:

- a UV layer named `SimpleBake` must exist;
- all islands for all members must already be packed into one shared 0–1 tile;
- no automatic unwrap or repack is performed by the production bake command.

Validation detects:

- missing `SimpleBake` UV;
- UVs outside the expected tile, subject to a small numeric tolerance;
- invalid/empty UV area;
- fully overlapping islands where detectable;
- incompatible unit membership or resolution.

Cross-object UV overlap detection should be implemented as a diagnostic, not a destructive fixer. Exact polygon-overlap testing may be staged after basic bounds/area validation.

### 8.3. Original UVMap

Do not delete the first/source UV map from source or generated meshes in the initial implementation.

Contract:

```text
UVMap       → original PBR or Alpha textures
SimpleBake  → Beauty or Lightmap texture
```

For Scene/Curtains/Homepod generated materials, `UVMap` is retained but unused.

Removing unused UV sets may be evaluated later as an export-only optimization after USD/RealityKit tests.

---

## 9. Bake queue behavior

### 9.1. Queue UI

```text
Lighting States    [x] Day  [ ] Evening
Bake Mode          [ Beauty | Lightmap ]

Bake Queue
  LO_Scene / Walls_Main        4K    3 objects
  LO_PBR / Table_Metal         2K    1 object
  TR_Scene / Terrace_Decor     2K   12 objects

[ Add Selected Units ]
[ Remove ] [ Clear ] [ Up ] [ Down ]

[ Bake Queue ]
```

Queue and project validation runs as part of execution. The main interface does
not expose separate ambiguous validation or active-unit buttons.

Do not place a prominent `Bake All` button in the main interface. The cost of accidental execution is too high.

If useful, provide `Add All Enabled Units to Queue` as a secondary menu action that only populates the queue and does not start baking.

### 9.2. Add Selected Units

Given any selection of source or generated objects:

1. Resolve each object to its source identity.
2. Resolve each source to its bake unit.
3. Add the complete unit once, even if only one member was selected.
4. Deduplicate units already in the queue.
5. Ignore `Export Original` objects with a concise report.
6. Report unassigned or invalid source objects instead of guessing.

### 9.3. Queue execution

`Bake Queue` processes units in visible queue order for the currently active:

- Day or Evening state;
- Beauty or Lightmap mode.

It does not automatically run both states. The user may need different manual collection exclusions for different batches.

### 9.4. Visibility outcomes

For a queued unit in the active state:

- if every member is excluded because the entire authored state branch is inactive, skip the unit with an informational status;
- if only some members are visible, block that unit as an error because a partial shared-atlas bake would silently corrupt the result;
- if all members are visible, bake normally.

This allows `Lamp_OFF` to be naturally skipped in Evening while preventing accidental partial-unit baking.

### 9.5. Error isolation

An error in one unit does not stop unrelated queued units unless Blender enters an unsafe global state.

Each unit reports one of:

```text
Succeeded
Succeeded with warnings
Skipped for active state
Failed validation
Failed during bake
Cancelled
```

---

## 10. Bake modes

Implement one shared runner with mode-specific processors.

```text
Shared runner
  queue
  state snapshot/restore
  build plan
  transaction
  progress
  ownership
  generated geometry
  staged file output

Mode processor
  validation additions
  temporary receiver material
  Cycles bake type and passes
  denoise behavior
  output color contract
  generated material hookup
```

### 10.1. Beauty

Beauty is the production bake mode for Scene, PBR, Curtains, Homepod and Translucent layers.

Use the discovered global SimpleBake preset `CyclesBake_Unlit` as the reference configuration, without requiring SimpleBake at runtime.

Reference preset location:

```text
C:\Users\papl\AppData\Roaming\Blender Foundation\Blender\data\SimpleBake\CyclesBake_Unlit
```

Relevant embedded settings:

```text
Engine                       Cycles
Bake type                    COMBINED
Pass Direct                  true
Pass Indirect                true
Pass Diffuse                 true
Pass Glossy                  true
Pass Transmission            true
Pass Emit                    true
Pass Color                   true
Samples                      256
Margin                       project setting, initial 16
Margin type                  ADJACENT_FACES
View from                    ABOVE_SURFACE
Existing SimpleBake UV       required/preferred
Generate new UV              false
Denoise                      true
Internal image               32-bit float
Delivery format              PNG
Delivery color interpretation sRGB
Clear image                  first member only
```

Unit resolution overrides the 4096×4096 resolution stored in the preset.

Do not expose all these settings in the normal UI. They are a versioned internal bake profile. Settings that are deliberately user-controlled are resolution per unit and global margin.

### 10.2. Lightmap

Retain the existing classic diffuse lightmap implementation as the Lightmap processor:

- scene-linear lighting signal;
- direct and indirect diffuse lighting;
- world, emission, shadows and color bleeding;
- receiver Base Color excluded;
- specular/metallic/normal-map appearance excluded;
- existing denoise and EXR path retained initially.

Lightmap and Beauty artifacts use different ownership keys and files. Running one never replaces the other.

The existing lightmap generated-material behavior should be preserved during the runner refactor before further product decisions are made about exporting Lightmap artifacts to AVP.

Beauty is the default production artifact used by the new layer export path. Lightmap export remains available only after its USD/RealityKit material contract is explicitly validated; until then it remains a Blender-side generated result/tool.

---

## 11. Multi-object, multi-material Beauty bake

Objects in one unit may have arbitrary materials and any number of material slots. Their only required shared property is the prepared `SimpleBake` atlas.

### 11.1. Temporary bake receivers

For each unit:

1. Create isolated temporary copies of every member object, mesh and material.
2. Preserve evaluated transforms and the chosen modifier policy.
3. Hide the corresponding source receiver objects only for the scoped bake so geometry is not double-counted.
4. Leave all other visible objects inside Source Root as contributors.
5. Add the shared target image node to every temporary material slot.
6. Bake members sequentially into the same image.
7. Clear the image only before the first member; preserve existing pixels for subsequent members.
8. Denoise/process the completed unit image once after all members succeed.

The permanent source materials are never edited to zero metallic, add target nodes, or change active image nodes.

### 11.2. Modifier policy

The current SimpleBake preset applies modifiers on exported/generated mesh output. The new runner must make this behavior deterministic.

Initial implementation recommendation:

- bake evaluated geometry;
- create generated meshes from the same evaluated result;
- do not apply modifiers to source objects;
- validate that Day and Evening builds use structurally compatible evaluated geometry.

Modifier behavior requires representative production-scene tests before release.

---

## 12. Generated material processors

All generated output uses Principled BSDF materials for Blender/USD compatibility.

### 12.1. Scene / Beauty

Used by Scene, Curtains, baked Homepod geometry and `Lamp_OFF`.

For every generated object:

- collapse output to one material slot;
- assign a simple Principled BSDF material;
- connect the shared baked Beauty texture to Base Color;
- drive that texture with the `SimpleBake` UV map;
- set Metallic to 0;
- keep other values at tested pipeline defaults;
- normalize all polygon material indices to slot 0 on the generated mesh only.

The generated material is intentionally simple. The original source can retain any number and type of materials.

Where safe, all members of one unit may share one generated Scene material because they share one image and material policy.

### 12.2. PBR / Beauty

During bake preparation, every temporary receiver material neutralizes metallic response:

- disconnect the Metallic input branch or override Metallic with 0;
- never edit the source material;
- use the temporary copy only.

Generated export materials:

- connect baked Beauty to Base Color using `SimpleBake`;
- restore original Metallic using `UVMap`;
- restore original Roughness using `UVMap`;
- restore original Normal/Bump using `UVMap`;
- preserve other explicitly supported PBR branches after validation;
- use Principled BSDF;
- preserve material-slot correspondence required by the source object.

The existing `material_rebuild.py` logic is the starting reference, but it must be redesigned to:

- use stable IDs rather than `Name`/`Name_Baked` pairs;
- support multiple material slots;
- operate automatically inside the transaction;
- tag generated ownership with unit/state/mode;
- avoid source-name-based collision rules.

Multi-object PBR units are supported by the architecture. They may be deferred from the first production milestone if representative project content only needs single-object PBR units, but the data model must not prohibit them.

### 12.3. Translucent / Beauty

Generated materials:

- connect baked Beauty to Base Color using `SimpleBake`;
- preserve the source Alpha/opacity branch using `UVMap`;
- remove unrelated source shading branches not required by the translucent export contract;
- use Principled BSDF;
- preserve the required Blender 5.2 surface/render settings for USD opacity export.

The existing material rebuild behavior for names containing `Leaf` or `Alpha` is the reference implementation. Replace name detection with the render layer's `Translucent / Beauty` profile.

Support multiple material slots by rebuilding each slot independently and maintaining polygon-to-slot mapping.

### 12.4. Export Original

No permanent generated object or material is created.

Export uses the source original with its:

- name;
- transform;
- mesh/materials;
- UVs;
- parenting;
- runtime identity.

This applies both to whole Export Original layers and to individual objects such as the Homepod screen and UI empties inside a bake-capable layer.

---

## 13. Day/Evening artifacts and structural safety

### 13.1. Canonical generated geometry

Maintain one canonical generated object/mesh per source object and unit/mode, plus separate state-specific images/material bundles.

Reason:

- Blender datablock names are global, so independent Day and Evening object copies tend to acquire `.001` suffixes;
- exported entity paths must be identical between Day and Evening;
- duplicate geometry increases memory;
- one canonical structure makes cross-state validation explicit.

Logical result:

```text
Unit
  canonical generated objects/meshes
  Day image + Day material bundle
  Evening image + Evening material bundle
```

### 13.2. State-specific material binding

Preview and Export temporarily bind the selected state's generated materials to canonical generated objects through a reversible state service.

The Day/Evening lighting selector itself does not change generated preview. Preview is controlled separately so state activation remains predictable.

### 13.3. Structural compatibility signature

Store a compatibility signature for each unit build containing at least:

- ordered source IDs;
- evaluated topology counts/structure;
- transforms relevant to generated output;
- `SimpleBake` UV coordinates;
- material-slot count and polygon material indices;
- generator schema/version.

This is not presented as a general `Up to date` fingerprint.

Its only initial purpose is to prevent incompatible Day/Evening artifacts from being exported together.

If rebaking Day changes the canonical structure:

- commit the new Day result;
- mark the old Evening result structurally incompatible;
- block Evening export for that unit until Evening is rebaked successfully.

Do not silently reuse an old texture on changed UV/topology.

---

## 14. Generated collections and preview

Use controlled internal namespaces tagged with metadata rather than trusted only by name:

```text
PMVR_GENERATED
PMVR_WORK
```

`PMVR_WORK` contains only temporary transaction data and must be empty after success, failure or cancellation.

`PMVR_GENERATED` contains canonical successful results.

### 14.1. Preview controls

Keep preview independent from lighting-state activation:

```text
Show Sources
Show Generated for Active State
Show Active Unit Result
Show Active Layer Result
```

Preview operations may change visibility/material bindings of generated data but must:

- never modify source content;
- never alter authored collection membership;
- be reversible;
- clearly show whether Day/Evening and Beauty/Lightmap is being previewed;
- warn when the requested artifact does not exist or is incompatible.

### 14.2. Navigation

Provide:

```text
Select Render Layer Sources
Select Unit Sources
Select Unit Generated Objects
Select Unassigned Objects
Reveal Active Source
Reveal Active Generated Result
```

Generated-to-source navigation must use `source_id`, not names.

---

## 15. Unit transaction

Each `(unit, lighting state, bake mode)` build is one transaction.

### 15.1. Stages

```text
1. Validate project, state and unit
2. Build immutable execution plan
3. Snapshot Blender context and evaluation state
4. Create PMVR_WORK receiver copies and shared image
5. Configure mode/profile-specific temporary materials
6. Bake every member into the shared image
7. Denoise/process image
8. Build canonical generated geometry and state material bundle
9. Validate all generated members and external files
10. Stage external files under temporary names
11. Commit generated datablocks and atomically replace files
12. Replace previous owned artifact for the same unit/state/mode
13. Update build record and compatibility state
14. Restore Blender context and remove all work data
```

### 15.2. Atomicity

Commit is allowed only after every unit member succeeds.

On failure:

- delete only assets created under the current operation ID;
- retain the previous successful output;
- retain previous external files;
- restore source visibility and Blender context;
- mark the queue row failed with a useful message.

### 15.3. External files

Use staged files and atomic replacement:

```text
final.png.__pmvr_tmp_<operation-id>
→ verify
→ os.replace(temp, final)
```

File identity must use a stable unit `artifact_key`, not only the mutable display name.

A human-readable filename may combine the stored key and a sanitized label, but renaming a unit must not accidentally orphan or collide with another unit's output.

---

## 16. Blender state snapshot and restoration

One reusable context/state service must cover:

- mode;
- selected objects;
- active object;
- active View Layer;
- complete relevant `LayerCollection.exclude` tree;
- object `hide_viewport`, `hide_render` and `hide_set` values changed by the runner;
- active World;
- render engine;
- Cycles bake settings touched by the profile;
- image settings and color management touched during save;
- active material slots;
- material bake target node state;
- Image Editor images touched by temporary targets;
- compositor tree/settings/resources used for denoise;
- generated preview material bindings temporarily changed during export.

Restoration runs through `try/finally` after success, validation failure, bake error or supported cancellation.

Do not use a broad reset operation that destroys unrelated user state.

---

## 17. Validation system

Validation returns structured issues:

```text
severity       INFO / WARNING / ERROR
code
message
layer_id
unit_id
source_ids[]
fix_operator   optional explicit safe action
```

### 17.1. Project validation

- Source Root missing or unavailable in active View Layer.
- Day/Evening collection missing, ambiguous or outside Source Root.
- Day/Evening collection relationship invalid.
- Day/Evening World missing.
- unsaved `.blend` when relative output paths require a saved project.
- duplicate source IDs.
- invalid or unsupported schema version.

### 17.2. Layer validation

- empty or duplicate output base name;
- invalid filesystem characters;
- missing/duplicate layer UUID;
- unsupported layer type;
- layer has no members, reported as warning unless required;
- member points to missing layer;
- generated object registered as a source;
- source object located in the generated namespace;
- hierarchy crosses incompatible layers in a way that would break export paths.

### 17.3. Object-role validation

- source is unassigned;
- mesh in bake-capable layer has `UNASSIGNED` role;
- empty assigned to `BAKE`;
- `BAKE` object lacks unit;
- `EXPORT_ORIGINAL` object incorrectly references a unit;
- object is outside Source Root;
- unsupported object type for selected export format.

### 17.4. Unit validation

- missing/duplicate unit UUID;
- empty unit;
- unit belongs to missing or Export Original layer;
- members span different layers;
- member points to another unit;
- missing `SimpleBake` UV;
- invalid UV area or out-of-tile coordinates;
- partial member visibility in active state;
- incompatible evaluated geometry/material configuration;
- invalid resolution;
- generated artifact collision not owned by PM VR.

### 17.5. Export validation

- required current-state Beauty result missing;
- Day/Evening artifact structurally incompatible;
- required generated member missing;
- duplicate final filenames;
- parent/hierarchy dependency missing from assembly;
- required technical named entity absent;
- output directory invalid or unwritable;
- active state does not match configured collection/World state.

### 17.6. Fix behavior

`Validate` never performs destructive fixes.

Safe fixes may be offered as explicit buttons, for example:

- register duplicate as new source;
- clear dangling unit reference;
- select/reveal affected objects;
- remove an empty unit definition;
- repair generated namespace links.

---

## 18. Hierarchy and export-path contract

Preserve source object transforms and hierarchy in generated output.

Rules to validate and implement:

1. If a baked source parent is also baked into the same render layer, the generated child is parented to the corresponding generated parent.
2. If the parent is `Export Original` in the same render layer, include that parent in the export assembly and preserve the relationship.
3. If a required parent belongs to another render layer, block export unless an explicit future cross-layer hierarchy policy is introduced.
4. Do not silently bake world transforms and drop parenting when AVP entity paths rely on the hierarchy.
5. Day and Evening assemblies must produce identical paths for paired look-bearing layers.

Representative hierarchy tests are mandatory before production rollout.

---

## 19. Export assembly

### 19.1. Current-state export

Export uses the active Day/Evening state.

Filename:

```text
Day       <display_name>.usdz
Evening   <display_name>_Evening.usdz
```

Apply the same rule to GLB when that format is enabled.

### 19.2. Layer composition

For a bake-capable layer:

```text
BAKE members
  → canonical generated objects
  → current-state Beauty materials/images

Export Original members
  → source originals
```

For an Export Original layer:

```text
visible assigned source originals
```

Objects hidden by the active authored lighting/state collection are not exported for that state.

Examples:

- `Lamp_OFF` generated output is available in Day and naturally absent/skipped in Evening.
- `Lamp_ON` source originals are visible/exported in Evening and absent in Day.

### 19.3. Temporary assembly

Build a temporary export collection/selection without moving source objects out of authoring collections.

The existing collection exporter remains the backend adapter:

1. Resolve the semantic layer members.
2. Resolve generated versus original representation.
3. Apply current-state generated material bundle.
4. Create temporary export assembly.
5. Call the existing USDZ or GLB exporter preset.
6. Restore temporary material bindings.
7. remove only the temporary assembly.

Never use current selection as the source of export membership.

### 19.4. Export UI

```text
Lighting State      [ Day | Evening ]

Render Layers
  [x] LO_Scene
  [x] LO_PBR
  [ ] LO_Glass
  ...

[ Export Active Layer ]
[ Export Checked Layers ]

Format
  [ USDZ ] [ GLB ]
```

`Export All` need not be a prominent primary action. Checked layers provide a safer explicit batch.

Retain modal batch progress and cancellation between files from the current exporter.

---

## 20. Progress and cancellation

Adapt the existing viewport overlay rather than replacing it.

Show:

```text
Lighting state
Bake mode
Render layer
Bake unit
Unit index / total queued units
Member index / total unit members
Current phase
Overall percentage
Elapsed time
Recent warnings/errors
```

Suggested phases:

```text
Validating
Preparing receivers
Baking member N/M
Denoising
Building generated materials
Verifying
Saving
Committing
Restoring state
```

Cancellation is supported only at safe boundaries initially:

- between units;
- between member bakes when Blender has returned control;
- never by forcibly interrupting a blocking Cycles call in an unsafe state.

An Escape request during a blocking bake becomes `cancel after current safe step` unless Blender exposes a tested safe cancellation path.

---

## 21. Storage and build records

### 21.1. `.blend` as authoring truth

Store in the `.blend`:

- project pointers/settings;
- render-layer definitions;
- object IDs and membership;
- bake-unit definitions/resolutions;
- persistent bake queue;
- generated ownership tags;
- last successful build records;
- structural compatibility signatures.

### 21.2. Build record

Logical fields:

```text
unit_id
layer_id
lighting_state
bake_mode
status
operation_id
generator_version
structural_signature
image datablock pointer/name
external file path
timestamp
warning summary
```

### 21.3. Sidecar manifest

A generated JSON sidecar is useful for debugging and future Windows/Mac handoff, but it must not become a second editable source of truth.

Implement it after core `.blend` persistence and export orchestration are stable.

When added, label it as generated and regenerate it from `.blend` project/build data.

---

## 22. Migration from current PM VR

Migration must preserve existing user data and avoid deleting legacy generated assets automatically.

### 22.1. Existing Lightmap Baker

- Refactor its runner/state/progress/transaction services into shared infrastructure.
- Preserve current Lightmap output behavior during the refactor.
- Do not automatically reinterpret every legacy queue row as production Beauty setup.
- Offer an explicit migration helper that creates one unit per legacy queued object if useful.
- Keep legacy `_LM` results until the user explicitly cleans or replaces them.

### 22.2. Existing collection export list

- Retain the exporter implementation and presets.
- Replace the collection list as the long-term semantic source with render-layer definitions.
- Optionally offer `Import Export Collections as Render Layers`.
- Imported layers default to `Export Original` and require user review before bake profiles are assigned.

### 22.3. Existing material rebuild

- Extract graph traversal and pruning helpers.
- Replace manual `Name_Baked` pairing with processor input from stable IDs.
- Keep the old operator temporarily for compatibility until PBR and Translucent processors pass production tests.

### 22.4. Existing Optimization tools

- Move UI placement only at first.
- Do not rewrite working functionality while the core pipeline is being introduced.
- Later extract shared texel-density code for optional resolution suggestions without making automatic resolution mandatory.

---

## 23. Proposed module architecture

Avoid one large module. Suggested layout:

```text
modules/pipeline/
  __init__.py
  constants.py
  data.py
  migrations.py
  identity.py
  project_settings.py
  state_controller.py
  layer_service.py
  unit_service.py
  queue.py
  validation.py
  build_plan.py
  context_state.py
  ownership.py
  transactions.py
  artifacts.py
  preview.py
  progress.py
  export_assembly.py
  export_adapter.py
  ui/
    stage_tabs.py
    optimization.py
    setup.py
    bake.py
    export.py
    project_settings.py
    issue_list.py
  bake_modes/
    base.py
    beauty.py
    lightmap.py
  material_profiles/
    scene.py
    pbr.py
    translucent.py
    export_original.py
```

Existing modules may be adapted gradually rather than moved all at once, but dependencies should point toward shared services rather than UI operators calling one another.

---

## 24. Implementation phases

### Phase 0 — fixtures and regression baseline

- Save representative test `.blend` files or scripted fixtures.
- Record current Lightmap output and collection-export behavior.
- Capture the `CyclesBake_Unlit` preset in project documentation/tests.
- Add pure helper tests where Blender-independent.
- Establish console logging conventions and generator versioning.

**Exit condition:** existing working features have reproducible smoke tests before refactoring.

### Phase 1 — core schema and stable identity

- Add project settings PropertyGroup and schema version.
- Add render-layer and bake-unit PropertyGroups.
- Add object metadata and UUID registration.
- Implement duplicate-ID validation and explicit repair.
- Add migration framework.
- Do not bake/export through the new model yet.

**Exit condition:** assignments survive save/reopen and object renames; duplicates are detected.

### Phase 2 — Project Settings and state controller

- Add Source Root, Day/Evening collection and World pointers.
- Implement unique LayerCollection resolution.
- Implement Day/Evening activation.
- Snapshot/restore View Layer exclusion and World state.
- Add project validation.

**Exit condition:** repeated state switching changes only the configured collections and World, with unrelated exclusions untouched.

### Phase 3 — Setup UI

- Dynamic render-layer list and editing.
- Processing-profile selection.
- Assign selected objects to layer.
- Explicit `Bake` / `Export Original` role.
- Create unit from selected objects.
- Move/remove selected objects between units.
- Per-unit resolution.
- Active-object context panel.
- Selection/reveal helpers.

**Exit condition:** a production scene can be fully described without relying on authoring collection names.

### Phase 4 — validation and persistent unit queue

- Structured issue model and issue list.
- Queue operators and UI.
- Add Selected resolution from source/generated objects.
- Deduplication and ordering.
- active-state full/partial visibility validation.
- immutable build-plan generation.

**Exit condition:** the user can prepare a safe, validated batch without starting Cycles.

### Phase 5 — shared runner extraction

- Extract context restoration, progress, logging, ownership and transactions from the existing Lightmap Baker.
- Run existing Lightmap mode through the shared interface.
- Preserve current Lightmap images/material behavior.
- Add unit/state/mode ownership keys.

**Exit condition:** Lightmap regression fixture matches baseline and no source state leaks after errors.

### Phase 6 — Beauty Scene processor

- Embed the `CyclesBake_Unlit` behavior.
- Support shared target image across multiple separate objects.
- Support arbitrary source material counts during bake preparation.
- Create simple one-slot Principled generated materials.
- Implement unit-level atomic commit.
- Add Day/Evening image isolation.

**Exit condition:** Scene/Curtains-style units bake correctly at mixed resolutions and repeated builds replace only owned artifacts.

### Phase 7 — structural artifacts and preview

- Canonical generated geometry.
- State-specific image/material bundles.
- structural compatibility signatures.
- source/generated preview controls.
- current-state material binding and restoration.

**Exit condition:** Day and Evening can be inspected independently and incompatible stale state output cannot masquerade as valid.

### Phase 8 — PBR and Translucent processors

- PBR temporary metallic neutralization.
- PBR restoration of Metallic/Roughness/Normal and supported branches.
- Translucent restoration of Alpha through `UVMap`.
- multiple material slots.
- Blender 5.2/USD material setting verification.
- retain legacy manual rebuild until acceptance tests pass.

**Exit condition:** representative Reflect/PBR and Translusent assets match the current manual production result.

### Phase 9 — mixed layers and lamp states

- Homepod baked + Export Original geometry/empties.
- `Lamp_OFF` Day bake behavior.
- `Lamp_ON` Evening Export Original behavior.
- hierarchy validation for anchors and runtime entities.

**Exit condition:** mixed layers export complete and state-specific lamp content behaves correctly without object-level state overrides.

### Phase 10 — semantic export orchestration

- temporary layer assembly;
- current-state generated/original resolution;
- Day/Evening filename policy;
- active/checked layer export;
- adapter to current USDZ/GLB exporter;
- hierarchy and entity-path checks;
- atomic output and progress.

**Exit condition:** a layer exports in one command without moving originals or relying on current selection.

### Phase 11 — hardening and production rollout

- cancellation at safe boundaries;
- crash/failure cleanup;
- larger batch tests;
- missing/deleted source recovery;
- linked/shared datablock edge cases;
- unsaved project and invalid path handling;
- performance/memory measurements;
- end-to-end Mac Asset Manager and device tests;
- user documentation and migration notes.

**Exit condition:** representative full project completes Blender → USDZ → Asset Manager → AVP without manual reconstruction of generated layers.

---

## 25. Acceptance scenarios

### 25.1. Identity and persistence

1. Assign objects to layers/units, save, close and reopen: assignments remain.
2. Rename an object: layer, unit and generated mapping remain.
3. Duplicate a registered source: Bake/Export block until explicitly registered as new.
4. Delete a source: validation identifies the affected unit/build record without deleting unrelated output.

### 25.2. State switching

1. Day activates Day collection and World only.
2. Evening activates Evening collection and World only.
3. Manually excluded unrelated child collections remain excluded.
4. State activation and failed bake restore the previous context when used in a scoped operation.
5. Ambiguous/missing LayerCollection paths produce actionable validation errors.

### 25.3. Queue

1. Selecting one member adds the complete unit.
2. Selecting several members of the same unit adds one row.
3. Selecting generated objects resolves to their source units.
4. Export Original objects are ignored with a clear message.
5. Queue persists across save/reopen.
6. Partial unit visibility blocks the unit; fully state-hidden unit is skipped.

### 25.4. Scene Beauty

1. One-object unit bakes and produces one generated object.
2. Multi-object unit bakes into one shared image while retaining separate generated objects.
3. Multiple source material slots bake correctly.
4. Generated Scene output uses one Principled material slot and `SimpleBake` UV.
5. Source objects/materials/UVs remain byte-for-byte logically unchanged.
6. Unit resolutions 1K/2K/4K can coexist in one queue.

### 25.5. PBR

1. Metallic is neutralized only on temporary receivers.
2. Baked Base Color matches the intended non-metallic bake input.
3. Generated output restores Metallic, Roughness and Normal from source on `UVMap`.
4. Rebake does not accumulate nodes or modify originals.

### 25.6. Translucent

1. Base Color comes from Beauty on `SimpleBake`.
2. Alpha comes from original material on `UVMap`.
3. Multiple slots retain correct polygon assignments.
4. USDZ preserves the tested opacity behavior on AVP.

### 25.7. Mixed Homepod layer

1. Baked body exports generated geometry.
2. Screen exports original geometry/material.
3. UI empties export with correct transforms/names/hierarchy.
4. All appear in one Homepod layer file.

### 25.8. Day/Evening safety

1. Day and Evening images coexist without overwrite.
2. Rebuilding Day does not replace Evening.
3. Structural Day change marks old Evening artifact incompatible.
4. Export blocks incompatible paired state output.
5. `Lamp_OFF` is baked/exported for Day.
6. `Lamp_ON` contributes and exports original for Evening.

### 25.9. Export

1. Day filename uses the base name.
2. Evening filename uses `_Evening`.
3. `LO_` and `TR_` layers share processors but retain independent content/files.
4. Current selection has no effect on export membership.
5. Source collection membership is unchanged.
6. Failed export leaves the previous complete file intact.
7. Day/Evening entity paths and material-slot structures match where required by texture look swapping.

---

## 26. Explicitly deferred work

Do not include in the first production implementation unless a blocking need appears:

- automatic UV generation or repacking;
- general-purpose material graph authoring;
- automatic artistic grouping into units;
- automatic `Bake Changed` claims based on a full source fingerprint;
- object-level Day/Evening overrides;
- arbitrary user-defined layer types;
- forced cancellation inside an unsafe blocking Cycles operation;
- permanent mesh joining as part of bake;
- automatic removal of source UV maps;
- native/directional RealityKit lightmaps;
- silent cleanup of legacy PM VR or SimpleBake outputs;
- editable sidecar JSON as a second source of truth.

---

## 27. Remaining verification items, not design gaps

The main architecture is defined. These items require targeted implementation tests rather than more product-level invention:

1. Exact Blender 5.2 API behavior for preserving/restoring nested `LayerCollection.exclude` values.
2. Exact Beauty denoise path used by SimpleBake and whether it should be ported or replaced by the existing PM compositor service.
3. Evaluated modifier handling for representative production objects.
4. USD export behavior for `SimpleBake` and `UVMap` primvars.
5. Principled translucent opacity settings required by the current AVP importer.
6. Which original PBR inputs beyond Metallic/Roughness/Normal must be preserved in real Reflect assets.
7. Parent-chain behavior in the actual Homepod and technical layers.
8. Whether generated material datablocks can be shared across all members of a Scene unit without exporter-specific side effects.
9. Performance and memory impact of canonical generated copies in a full scene.
10. Safe cancellation behavior around Blender's blocking Cycles bake call.

Each verification item should produce a small fixture and a recorded result before its dependent phase is declared complete.

---

## 28. Final product behavior

```text
Authoring source scene
  + Source Root
  + Day/Evening lighting collections and Worlds
  + persistent layer/unit metadata
              ↓
manual visibility preparation
              ↓
select problem or target objects
              ↓
resolve complete bake units into persistent queue
              ↓
Beauty or Lightmap build for active state
              ↓
transactional generated artifacts
              ↓
preview source or generated result
              ↓
export active/checked semantic render layers
              ↓
Day base filenames or automatic _Evening filenames
              ↓
existing Mac optimization/validation/packaging pipeline
```

The user remains in control of scene composition and batch size. PM VR owns persistence, repeatability, validation, generated-state isolation and safe export.
