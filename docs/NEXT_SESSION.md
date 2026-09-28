# Next session

## Audit 2026-09-24 — fixed and reproduced in Blender 5.2.2

- Day/Evening materials: a canonical generated object shows one state, so the other state's material had no user and Blender dropped it on save. After reopen, Day export silently embedded the Evening texture. Committed generated materials now carry a fake user (released when replaced, when their source leaves the unit, or when the unit is removed), existing files are protected on load, and export refuses a unit whose state materials are missing.
- Material binding (export and Apply Preview) used `materials.clear()`, which resets every polygon to slot 0 and permanently collapsed PBR/Alpha multi-slot generated meshes. Binding now assigns slots in place, is strict for export, and export restores the previous preview binding.
- Bake transaction: the Beauty PNG (and Lightmap EXR) replaced the previous file before the commit; a later failure left the previous result overwritten, the generated mesh replaced and the object bound to temporary `__PMVR_WORK_MAT` materials. Files are now staged beside the final path, commit preconditions (material-slot counts, e.g. a Geometry Nodes Set Material) are checked before anything changes, and the previous file is restored on failure.
- Cancellation: Esc inside the Cycles job was consumed by the job's own modal handler, so the queue continued and committed a partially baked atlas as Ready. `object_bake_cancel` now cancels the whole queue; the unit in progress is discarded (recorded as CANCELLED) and earlier units are kept. A Cancel Bake button stops after the current Cycles pass. Lightmap mode stays synchronous; a cancelled Cycles pass there now stops the queue instead of continuing with the next unit.
- A running bake held Python references to `bake_units`/`render_layers` items, which move when those collections grow or shrink. Structural edits are disabled while a bake runs and the runtime resolves its unit/layer by ID.
- Setup selection sync used a message-bus subscription that Blender drops on every file load, so it never worked in an opened file. It is re-subscribed in `load_post` (also the overlay's UV-edit subscription).
- USDZ files were exported under `<name>.pmvr_tmp.usdz`, and that temporary stem became the package's root layer name. Export now writes the final filename inside a private temporary folder.
- A baked child whose parent was baked in another unit of the same layer lost its hierarchy in export. It is now parented to that generated parent (linked in either bake order), and removing a generated parent no longer shifts its children.
- Export refuses duplicated generated objects instead of exporting whichever copy is found first.
- Reload Scripts re-imports the nested `pipeline` and `lightmap_baker` packages.
- Interrupted bake recovery: every object the bake hides and the `PMVR_GENERATED` exclusion are mirrored into the file as `pmvr_bake_restore_*` markers. A file saved mid-bake (or recovered after a crash) gets its render visibility, generated collection and `PMVR_WORK` data restored on load. Without this, the next bake treated hidden sources as authored and silently skipped their units.
- `pipeline/bake.py` was split without behavior changes: `bake_scene.py` (isolation snapshot, receivers, Cycles calls, recovery), `generated.py` (generated ownership, material processors, state binding, commit and parenting), `bake_files.py` (staged external files), `bake.py` (unit runtime and queue operators). Source-ID lookup, Principled lookup and unused-material cleanup now have one implementation each.
- `run_tests.bat` runs every background test; `run_tests.bat gui` adds the interactive ones.
- Name verification (Cyrillic, dots, slashes, padded spaces, 63-character names, colliding mesh/material names, renames after bake) found and fixed three problems: a user collection named `PMVR_WORK` lost its objects (PM VR collections are now found by tag, not name); generated material names alternated between `Name` and `Name.001` on every rebake (the canonical name is restored after commit); units whose names differ only by case or special characters could not bake because their PNG names clash on Windows/macOS (a stable key suffix is added instead).
- Copies no longer share the original's ID. Blender copies every property on Shift+D, Alt+D and copy/paste; PM VR records the owner of each source/generated identity by `session_uid` (not copied, stable across undo) and gives new objects carrying a known ID their own (`identity.separate_copied_identities`, run from `depsgraph_update_post` when the object count grows and from `undo_post`/`redo_post`). A source copy keeps its layer and role but loses its unit and additional exports; a generated copy loses its `pmvr_*` tags. IDs already shared in a loaded file are not guessed and still need Register Selected Duplicates as New Sources.
- Bake receivers ignored material slots linked to the object (`slot.link == 'OBJECT'`): `new_from_object()` keeps the mesh materials, so such objects (typically linked duplicates with their own material) baked with the mesh's material. Receivers now use the slot's rendered material. Linked duplicates therefore bake independently in separate units; validation refuses members of one unit that share mesh data, because their SimpleBake UVs overlap. The Optimize Audit still reports shared mesh data as an issue.
- Logging: `pipeline/log.py` appends every line to `PMVR_Logs/<blend>_<date>.log` next to the `.blend` (system temp folder when unsaved), opening and closing the file per line so a crash keeps everything written before it (verified by killing Blender mid-bake). Queue start logs the environment (Blender/PM VR versions, file, Cycles device) and settings; units and members log durations; unexpected errors (not `PipelineBakeError`/`PipelineExportError`) log a traceback; export, validation and Setup changes are logged instead of printed. Project Settings shows the log path and opens the folder. The in-blend text keeps the last 500 lines.
- The `?` button in the panel header lists the naming and scene rules (`pipeline/ui.py`, `HELP_SECTIONS`). Keep it in sync when a rule changes.
- Regression coverage: `tests/blender_pipeline_integrity_smoke.py` (background) and the interactive `tests/blender_gui_bake_cancel.py`, `tests/blender_gui_selection_sync.py` (see their docstrings for the command lines).

## Bake scenarios (2026-09-27)

- `pipeline/scenarios.py`: a scenario records the Exclude flag of every collection under Source Root except the Day/Evening lighting collections, their content and the collections that hold them. Layers name a default (`bake_scenario_id`); units override it (`""` = layer, `SCENARIO_NONE` = outliner as it is). UI: Bake > Bake Scenarios, queue rows, Setup layer/unit detail.
- Blender does not inherit Exclude in View Layer sync: a child that is not excluded renders inside an excluded parent. Scenarios apply parent-first and exclude the content of an excluded collection; restore also goes parent-first, which keeps the outliner's hidden "previously excluded" memory of children in the normal cases.
- `ScenarioSession` touches nothing until the first unit with a scenario; its baseline is mirrored as JSON in `scene["pmvr_bake_restore_collections"]` and restored on finish, cancel, operator cancel and on load. Units without a scenario in a mixed queue bake with the baseline.
- The queue refuses to start (and Validate Pipeline reports) when a scenario would switch off a unit's own members; members hidden by the lighting state, their own render toggle or an unrecorded collection keep the usual skip behavior. Unrecorded collections keep their outliner state and are logged once per queue.
- Verified: `tests/blender_bake_scenarios_smoke.py` (a counter shadowed by a canopy in Bedroom bakes at 0.007 mean brightness without the Kitchen scenario and 0.658 with it; exact restore; mid-bake save recovery) and `tests/blender_gui_bake_scenarios.py` (real modal queue: refused start, collection states sampled during each Cycles job, restore after finish and after Esc).

## Unit list fixes (2026-09-28, from the UniPlace log)

- `active_unit()` wrote `active_bake_unit_index` when the index pointed at another layer's unit; from a panel or `poll()` Blender refuses the write, so the Setup panel raised and Remove Unit was disabled. It is read-only now: an index on another layer's unit means no active unit. Removing a unit makes its nearest neighbour of the same layer active (it used to take whatever unit followed in the shared collection).
- Unassign (and Remove from Unit, reassigning to another layer/unit) left units with no members in the list; giving the same objects new units then showed duplicate names. A unit left without members by these operators is removed with its generated data (not while a bake runs). Units emptied otherwise (objects deleted) are flagged red with a Remove Empty Units button.
- Regression: `tests/blender_gui_unit_list.py` (window; the panel draw is recorded, not just operator results).

## Setup simplification (2026-09-28, chosen by the user)

- Every list's `-` takes objects fully out of the layer (no layer, unit, role or additional exports). Before, removing a unit left its objects in the layer with role Unassigned, which failed validation and export and needed the Unassign button as a second step. Such leftovers are released on load/register (`release_roleless_members`).
- Removed from Setup: Export Original and Unassign buttons (unbaked objects live in Glass/Emissive/Runtime lists; older Export Original objects in baked layers are still listed with `-`), and the Active Source box (the lists follow the selection). Unit detail has Members `+`/`-`.
- `+` in a baked layer moves objects from other layers or units (units left empty disappear); objects already in a unit of this layer are left alone. Shift+ merges the selection into one shared unit, including objects that had units.
- Bake preview: Show Sources / Show Generated apply at once (Apply Preview removed from the UI; the operator stays for scripts), and the Day/Evening button binds the state's materials on baked results. Hidden objects outside the View Layer no longer raise. "Active Unit Sources" was removed (same as Setup's Select Sources).
- Lightmap controls are hidden (`SHOW_LIGHTMAP` in `pipeline/ui.py`); a file left in Lightmap mode still shows the mode switch.
- Setup header **Unassigned: N** selects visible objects inside Source Root without a layer (lights excluded); the old UNASSIGNED target selected every roleless object in the file, including hidden ones and generated copies.

## Export independent of visibility (2026-09-28)

- `resolve_layer_objects` no longer filters by render visibility. Before, objects in disabled collections or with the render toggle off were silently left out of USDZ/GLB, and a unit that exists only in Evening blocked the Day export ("Day Beauty is not ready"). Now every assigned object exports; the only state rule is `other_state_objects()`: objects that live only inside the other state's lighting collection are left out.
- The USD exporter (evaluation mode RENDER) drops render-disabled objects; their camera toggle is switched on only while the file is written and restored in `finally`. H-hidden and Disable-in-Viewports objects were already exported by both exporters.
- Bake still follows the outliner: a unit whose members are all hidden is skipped (counted as skipped), and its export then fails loudly as not ready.
- Regression: `tests/blender_export_visibility_smoke.py` (USDZ prims and GLB nodes checked per state; visibility compared before/after).

## UniPlace test bake 2026-09-28 (50%, Day+Evening): open problems

Result: 399 ready, 1 skipped, 20 failed in 3h 56m (log `PMVR_Logs/Uniplace_2026-09-28.log`). To bake one by one after fixes:

- `LeafIvy`, `LeafIvy01`..`LeafIvy07` (8 units, both states): "Material slot 0 must contain exactly one Principled BSDF"; foliage uses mixed shaders.
- `BR_Unlit Unit 146` (member `CeramicVaseDarkPBR`, both states): "evaluated mesh uses invalid material slots [19464]"; the vase has one material and no modifiers, shared unit packed in one UV tile.
- `TR_Unlit Unit 149` (both states): "only partially visible"; the user sees all members visible.
- `LampsWhiteLrPBR` Evening: skipped (Day-only PBR lamp; the Evening variant is a separate emissive object). Expected, but the log gives no reason.
- Console: thousands of identical "ERROR: Python context internal state bug. this should not happen!" lines, no other context.
- The 92 units queued at 03:35 were changed by the old ÷2/×2 buttons and probably share one Setup resolution now.
- The file shows the Evening baked result; the Day/Evening switch did not change it.
- Bake scenarios keep deleted collections in their list as "(deleted)".

Handled the same day:
- Alpha: `find_alpha_source()` (bake_scene) finds opacity in Principled Alpha or a Mix Shader with Transparent BSDF, through node groups via group inputs; the generated Alpha material is one Principled (baked colour + opacity branch on UVMap). PBR still needs one Principled per slot. Test: `tests/blender_alpha_sources_smoke.py`.
- Vase and rock are data problems the user fixes; messages now name them: faces using a missing slot (also in Validate), and each hidden member of a partially visible unit with the reason (render disabled / not in scene / collection disabled).
- Day/Evening: the queue ended with the lighting restored but baked results bound to the last baked state (the file showed Evening in Day). The queue now binds the restored state; the Lighting switch is also at the top of Bake; "Bake for" states are checkboxes by the Bake button. The GUI scenario test failed with the old code (Day lighting, Evening results) and passes now.
- Skipped units log why; queue start logs texture cache and autopack.
- Scenarios hide deleted collections and drop them on load/register (`prune_scenarios`).
- The console "Python context internal state bug" did not reproduce: GUI modal queue on OptiX with texture cache, a scenario and the Bake panel visible, 16 bakes, 0 lines. Likely from the user's environment (other add-ons, drivers, long session); open.

## Bake at 4K and small fixes (2026-09-28)

- `project.bake_at_max_resolution`: units bake at 4096 (times the test share), are denoised at that size, and `downscale_staged_beauty()` averages blocks in linear light down to the unit's resolution before materials are built. The bake margin is scaled by the factor. Test: brightness ratio 1.000 against a direct bake at the same size. On UniPlace `Artwork` (1024): 2.25x the fine-detail energy of a direct 1024 bake, colour within 1.7%, 176 s instead of 20 s.
- PBR generated materials drop the unreachable old Base Color branch (`_prune_unreachable_material_nodes`), like Alpha.
- A Beauty folder on another drive than the .blend made the commit fail (`relpath` across drives); `_blend_relative()` keeps such paths absolute.
- The Setup resolution log names the object and says when the required size is above 4096 (texel density stays below target).
- Texture limit and texture cache stay out of the add-on by the user's choice; they are managed in Blender. The texture cache shifts bounce light (see the pipeline document on the user's Desktop).

## Task list (2026-09-28, waiting for the user's go)

1. **Lighting switch leaves nested collections off.** `activate_state()` sets Exclude only on the Day/Evening collection. Blender's recursive Exclude remembers children that were excluded before the parent ("previously excluded") and keeps them off when the parent is re-enabled, so nested light groups stay off, also during queue bakes. Plan: activating a state includes its lighting collection with every nested collection, the other state's collection is excluded whole; the switch owns Exclude inside lighting collections (to leave a group out, move it out or disable its render). Test with nested collections and remembered exclusions. Inspect UniPlace read-only for nested lighting collections that were off during the last bakes and tell the user which states/units need a rebake. Rule confirmed by the user: whatever is in a lighting collection is switched on with all its nested collections. **Done:** `activate_state()` switches each lighting subtree whole (`_switch_subtree`). On UniPlace the nested groups Shelfs (48 lamps), Stairs_Daylight (95), LampsLR_Day, SkyboxDaylight and Stairs_Night (95), Shelfs_Night (48), LampsLR_Night, SkyboxNight were off after every switch; bakes made in that state miss them. Scenario lists now follow the collection tree (`sync_scenarios` on depsgraph updates when the tree changes, and on load); lighting subtrees stay out of scenarios.
2. **Bake resolution vs export resolution**, see below; confirm the open decisions first.
3. Optional: measure packed vs unpacked textures on one unit (memory, time). Expected: packed images always sit in RAM (compressed) and cannot use the texture cache.
- User side: bake with BlenderKit and Megascans Plugin disabled to find the console "Python context internal state bug"; check the 92 units' resolutions changed by the old ÷2/×2.

## Planned next: bake resolution vs export resolution (agreed 2026-09-28, not started)

The user's SimpleBake habit, automated: bake decides the light, export decides the budget (Vision Pro ~8 GB per app, mostly textures).
- **Project Settings: Bake Resolution** (default 4096). Every unit bakes at it (times the Test share) and is denoised there. This replaces the Bake at 4K checkbox.
- **Master files:** the full-size denoised Beauty PNG is kept on disk (own folder) per unit and state.
- **Export copy:** derived from the master at the unit's resolution (downscale in linear light, existing `downscale_staged_beauty`). Blender's generated materials show this copy (what you see is what ships; 250 masters at 4K would be ~16 GB in Material Preview) and USDZ/GLB embed it. The Mac side is unchanged.
- **Changing a unit's resolution** re-derives its copy from the master; the unit stays Ready, no rebake.
- **Open decisions:** test bakes (25–50%) must not overwrite masters (separate place, or marked and replaced by the next full bake); when copies regenerate (on resolution change and/or at export); how the unit status shows master vs export size.
- **Cost estimate for UniPlace:** bake time ~1.8x (about 16 h to 29 h for both states at 256 samples); downscaled units could use fewer samples. Masters ~15 MB each, ~7.5 GB for both states.

## Audit follow-ups not changed

- Export Original children of a baked parent keep the source parent as a transform-only USD Xform; GLB flattens them to the root. World transforms are correct in both.
- Generated objects are named after their source at creation (`Name.001`) and keep that name after the source is renamed; exported prim names follow the generated object.
- Memory, measured on an RTX 3090 (OptiX, 24 Beauty bakes of 4K units): VRAM stayed flat between units (about +1.2 GB over idle, peak 5.9 GB during a bake). Process RAM grew about 0.5 GB per 4K unit and levelled off near 13 GB by the 24th bake. Per-stage measurement places the growth at the Beauty denoise step; the denoise alone does not grow in isolation, and freeing the committed image buffers did not change it. Not investigated further; watch RAM in Task Manager during the first long 8K queue.

## Unified layer taxonomy

- One authoritative `layer_type` drives both runtime meaning and Blender bake/export behavior; there is no parallel processing-profile entity.
- Types are Unlit, PBR, Alpha, Translucent, Glass, Emissive, and Runtime.
- Behavior: Unlit/Translucent bake unlit Beauty; PBR preserves PBR channels; Alpha preserves Alpha; Glass/Emissive/Runtime export originals.
- New projects initialize these seven general layers instead of the project-specific Scene/Reflect/Translusent/Curtains/Homepod set.
- Video surfaces are runtime-driven content and belong under `Runtime/FX`; `Runtime/SFX` is reserved for sound-effect placement points. Emissive and Skybox remain distinct visual outputs.
- The model was not yet in production, so no legacy schema or migration layer is retained.
- Generated objects, materials, and images store the flat `pmvr_layer_type` custom property. Runtime source metadata still needs a manifest or export proxy for a uniform Mac-side contract.
- Cameras are now included by both USDZ and GLB exporters, allowing Runtime layers to carry probe-camera transforms directly.

## Implemented, awaiting live-scene feedback

- Scene Debug now lives only in Optimize. UV Health, Texel Density, Checker, Scale, and Linked Meshes work on all visible scene meshes before project initialization; Bake Status, Render Layers, and Bake Units unlock after Initialize.
- Bake isolation now excludes every active View Layer instance of `PMVR_GENERATED`, restores its previous state after success/failure/cancellation, and keeps per-object `hide_render` as a fallback.
- Setup list selection follows the active viewport object through a deferred Blender message-bus update. Registered sources and generated outputs resolve to their semantic render layer and bake unit; Export Original resolves only to its layer.
- Bake-unit batch selection now uses native independent Bool checkboxes. Every checkbox can be cleared, LMB-drag selection is available, and resolution propagation remains limited to checked units in the same render layer.

## Removed after live-scene feedback

- The synchronized Base Color/Roughness/Alpha texture preview was removed. Updating every material in a real scene made the interaction too slow to be useful.

## Retired material checker

- The former Optimize Global/Selected Checker and all bake/export suspension hooks were removed after the GPU Checker replaced them.
- On load, legacy slot-state data is used once to restore original materials and remove unused PMVR_CheckerPreview materials. Only the shared A1-H8 image and tiling property remain for GPU diagnostics.

## Viewport pipeline overlay

- The Setup and Bake stages offer non-destructive GPU diagnostics without changing materials, object colors, or viewport shading. The renderer uses cached bulk mesh buffers and a sub-pixel fragment-depth offset for transparent exact-surface fills; it does not scale or displace geometry.
- Bake Status colors source meshes as Missing, Existing, baked This Session, No Bake, or Unassigned for the active Day/Evening and Beauty/Lightmap combination.
- Render Layers uses persistent user-editable colors stored per semantic layer. Existing layers receive distinct palette colors when the add-on loads. Unassigned meshes are hidden by default and can be revealed as restrained amber warnings.
- Session bake state is held only in memory, marked after a successful commit, and cleared when another blend file is loaded.
- Scene Debug starts with Ctrl+Shift+D, Start Debug, or a choice from the compact Mode dropdown. While active, keys 1-8 select Bake Status, Render Layers, Bake Units, UV Health, Texel Density, Checker, Scale Check, and Linked Meshes; bracket keys cycle modes and Esc exits. All unrelated events pass through to Blender. The lower-left HUD includes the controls.
- UV Health validates the reserved first two UV channels (UVMap, SimpleBake). Invalid bake-capable meshes are red, valid meshes green, and explicit Export Original meshes muted.
- Texel Density and automatic unit setup now share one canonical area calculation. It evaluates only UV channel index 1 (the second channel), which must be named SimpleBake, against evaluated world-space mesh area and the scene unit scale. Objects in a valid bake unit use that unit's resolution; objects not yet added to the pipeline use Default Unit Resolution. The diagnostic is discrete and monotonic: green is Great at or above target, yellow is Acceptable from 0.5x target up to target, red is below 0.5x target, and missing/invalid UV or geometry data is magenta. The HUD shows exact px/cm and resolution for the active source object.
- Checker is a GPU-only Scene Debug channel on key 6. It uses the shared A1-H8 asset without changing materials, defaults to the second SimpleBake UV channel, can switch to the first UVMap channel, and reuses the existing checker tiling setting. Sampling is quantized to each object's bake-unit resolution (or the project default), making 512/2K/4K previews visibly resolution-aware when viewed closely. Missing selected UV channels are magenta.
- Bake Units assigns a stable deterministic color to each unit, so grouped source objects are visible directly in the scene. Scale Check fills only meshes whose local scale is not 1/1/1. Linked Meshes fills only objects whose mesh datablock has multiple users.
- Scene Debug mode is session-only and is forced Off whenever a blend file loads, so a saved active overlay cannot leave an orphaned HUD without its modal controller.

## Audit scope

- The working Audit checks bake-preparation concerns: shared mesh data, reserved UV channels, and unapplied scale. Multiple input materials are valid and no longer reported as an error.
- Customer naming conventions are intentionally separate behind Check Names. They are not part of pipeline identity; source/layer/unit relationships use stable IDs.

## Material output contract

- Scene / Beauty units now create one state-specific material per bake unit and reuse it only among generated members of that unit.
- PBR and Alpha still preserve source-specific Roughness/Normal/Alpha branches and therefore can currently produce more than one generated material. Reaching the strict one-unit/one-material export contract for those types requires unit-atlas generation for the preserved channels; do not collapse these slots destructively.

## Deliberately deferred engineering work

- Grouped-unit texel density is calculated correctly per member at the shared unit resolution, but the diagnostic does not yet detect UV overlap between different objects packed into that unit.
- Scene Debug currently has one modal-controller state for the Blender process. This is reliable for the normal single-window workflow; true simultaneous multi-window debug sessions would need window-scoped controller state.
- `pipeline/viewport_overlay.py` intentionally remains one module until the diagnostic engine settles. Split rendering/cache code from mode classifiers only after the real-scene test, so the refactor does not obscure functional regressions.

## Resolution (2026-09-28)

- 8192 removed everywhere (pipeline and legacy Lightmap Baker); stored enum value 5 is capped to 4096 on load/register (`cap_removed_resolutions`).
- The ÷2/×2 buttons rewrote every queued unit's Setup resolution, with no way back. Replaced by `project.test_resolution` (100/75/50/25%, reset to 100% on load): `bake_resolution()` scales only what the queue bakes. Units record `day/evening_baked_resolution`; the unit status shows a result below Setup and export warns about it.
- Regression: `tests/blender_resolution_smoke.py` (real 25% bake writes a 64 px PNG for a 256 unit, Setup unchanged, export warning, 100% rebake clears it, reopen resets).
