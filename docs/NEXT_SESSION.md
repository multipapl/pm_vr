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

- Superseded the same day by Bake Resolution (below). `project.bake_at_max_resolution`: units bake at 4096 (times the test share), are denoised at that size, and `downscale_staged_beauty()` averages blocks in linear light down to the unit's resolution before materials are built. The bake margin is scaled by the factor. Test: brightness ratio 1.000 against a direct bake at the same size. On UniPlace `Artwork` (1024): 2.25x the fine-detail energy of a direct 1024 bake, colour within 1.7%, 176 s instead of 20 s.
- PBR generated materials drop the unreachable old Base Color branch (`_prune_unreachable_material_nodes`), like Alpha.
- A Beauty folder on another drive than the .blend made the commit fail (`relpath` across drives); `_blend_relative()` keeps such paths absolute.
- The Setup resolution log names the object and says when the required size is above 4096 (texel density stays below target).
- Texture limit and texture cache stay out of the add-on by the user's choice; they are managed in Blender. The texture cache shifts bounce light (see the pipeline document on the user's Desktop).

## Task list (2026-09-28, waiting for the user's go)

1. **Lighting switch leaves nested collections off.** `activate_state()` sets Exclude only on the Day/Evening collection. Blender's recursive Exclude remembers children that were excluded before the parent ("previously excluded") and keeps them off when the parent is re-enabled, so nested light groups stay off, also during queue bakes. Plan: activating a state includes its lighting collection with every nested collection, the other state's collection is excluded whole; the switch owns Exclude inside lighting collections (to leave a group out, move it out or disable its render). Test with nested collections and remembered exclusions. Inspect UniPlace read-only for nested lighting collections that were off during the last bakes and tell the user which states/units need a rebake. Rule confirmed by the user: whatever is in a lighting collection is switched on with all its nested collections. **Done:** `activate_state()` switches each lighting subtree whole (`_switch_subtree`). On UniPlace the nested groups Shelfs (48 lamps), Stairs_Daylight (95), LampsLR_Day, SkyboxDaylight and Stairs_Night (95), Shelfs_Night (48), LampsLR_Night, SkyboxNight were off after every switch; bakes made in that state miss them. Scenario lists now follow the collection tree (`sync_scenarios` on depsgraph updates when the tree changes, and on load); lighting subtrees stay out of scenarios.
2. **Bake resolution vs export resolution.** **Done**, see "Bake Resolution and export at unit resolution".
3. Optional: measure packed vs unpacked textures on one unit (memory, time). Expected: packed images always sit in RAM (compressed) and cannot use the texture cache.
- User side: bake with BlenderKit and Megascans Plugin disabled to find the console "Python context internal state bug"; check the 92 units' resolutions changed by the old ÷2/×2.

## Bake Resolution and export at unit resolution (2026-09-28)

The user's SimpleBake habit, automated: bake decides the light, export decides the budget (Vision Pro ~8 GB per app, mostly textures). Decisions by the user: one file per unit and state (a test bake overwrites it like any bake; tests come before finals), Blender shows the baked size, export scales, colour must not change.
- `project.bake_resolution` (Project Settings, default 4096) replaces the Bake at 4K checkbox. `bake_size()` = max(Bake Resolution, unit resolution) times the Test share. The Beauty PNG keeps that size (no downscale at bake); `day/evening_baked_resolution` record it. The margin is scaled to keep its width at the unit's resolution (`ceil(margin * bake / export)`).
- Export: `export.ExportTextures` writes, once per export run, a copy of every Beauty atlas that is larger than its unit's resolution (`bake_files.scale_atlas`: linear light, exact area average for any ratio, 8-bit PNG written without colour management) into a temp folder under the baked file's name, points the image at it while each layer is written, then back at the baked file. Baked files and the Blender images are unchanged; the log line "Atlases scaled to unit resolution: N x 4096 to 1024" lists them.
- A unit's resolution can go down, or back up to the baked size, without a rebake; above the baked size the status shows "Ready 1K (Setup 2K)" and export warns (as for test bakes).
- Colour: every scaled pixel equals the exact linear-light average rounded to 8 bits; the image mean moves by the rounding only (about 0.0005 linear, under 0.1 of an 8-bit step). Black/white pixels average to code 188 (half the light), not 128.
- Lightmap keeps baking at the unit's resolution (`lightmap_resolution()`); not in production.
- Material Preview now loads the baked size (4K): about 64 MB per atlas in RAM and VRAM. If the viewport gets heavy, Blender's Preferences > Viewport > Texture Limit Size caps it without touching the files.
- Regression: `tests/blender_resolution_smoke.py` (1024 bake of a 256 unit; USDZ and GLB atlases at 256/512/1024 with the same mean light; baked file hash and image path unchanged; 2048 above the baked size warns; 25% test bake; scaler checks: flat colour at 3:2, black/white checker, noisy mean). Baking tests set `bake_resolution = '256'` so units bake at their own size.
- Existing UniPlace atlases were saved at unit size (old behaviour); they export as they are. Units rebaked from now on keep 4K.

## Where UniPlace stands (2026-09-29, end of session)

- Full USDZ export (28 files + Variants/) in `UniPlace_Sync/USD`, checked file by file: no empty materials, no missing textures, variant KitchenMarble Marble/Stone to the Mac contract, 11 probes in Runtime. Mac notes on the user's Desktop (`PMVR_Export_для_AssetManager.md`, with a "what changed" section).
- Added this session: island padding margin, Flatten to Texture (long side), material variants, Bevel-stable signatures, Blackbody/Light Path camera values into USD (colour and strength), render layer reordering, fix for the crash when a queue ends at its start (a dangling modal handler; also hit through Geo-Scatter's depsgraph handler).
- Open on the user's side: the lamp materials are fixed (rebake the lamp units before the next export); first the headset test of everything converting and working, only then the final bake (likely with the texture cache); headset review: double glow on the evening lamps (atlas + emissive), Alpha threshold, two UV channels in one material on the Mac. Additional (cross-layer) exports are not set up yet, not needed for the headset.
- Declined by the user: Export for Day/Evening toggles (full re-exports of every layer and state are rare).

## Headset findings: UV channels (2026-09-29)

- The Mac found 74 baked objects per look shipped with USD UV sets `SimpleBake, st` instead of `st, UVMap`: atlases lay on the authored layout, Alpha/PBR masks read a `UVMap` that did not exist. Ours: the bake copy renders with UVMap (intended), `commit_generated_geometry` copies that mesh and the result kept it. Blender 5.2's USD export renames the render UV map to `st` on the mesh but the active one in materials, so only active = render = SimpleBake gives the contract. On UniPlace 76 of 400 generated objects had the camera on UVMap: every mesh re-created after ~12:00 on 09-28; the night-run meshes were right because something (probably a bulk Activate SimpleBake) had set them.
- `generated.use_bake_uv()` sets both flags at commit and before every USDZ write (export logs any object it switched); `settle_generated_uvs()` repairs opened files (load_post/register, logged). `pin_normal_maps()` names UVMap in tangent Normal Map nodes of generated materials (28 on UniPlace), otherwise their tangents would follow the new render flag.
- `pipeline/usd_check.py`: after each USDZ is written, a baked mesh must carry exactly `st` + `UVMap`, every material may read only UV sets its mesh has, every texture must be in the package. A failing file stays written, counts as failed, each problem is logged ("USD check <file>: ...").
- UniPlace re-exported from the saved file (all layers, Day + Evening; file not saved, layers enabled in memory only): 0 failed, all 400 baked meshes `st, UVMap`, KitchenWood_001 `st` holds the atlas coordinates from the Mac report, mesh counts and the variant manifest unchanged. Previous export in `UniPlace/USD_backup_2026-09-29_0420`.
- Probes: all 22 EXRs were DWAA (Apple cannot read it). Re-saved as EXR Half ZIP with OpenImageIO, pixels bit-identical; originals in `UniPlace/probes_DWAA_backup_2026-09-29`.
- Regression: `tests/blender_usd_uv_smoke.py` (real bake of Unlit/PBR/Alpha; flags, normal map, `st`/`UVMap` coordinates, reopen repair, export repair, check catches the old failure and a missing texture, operator counts it as failed). With the old code it reproduces the Mac report.

## Render Probes (2026-09-29)

- `pipeline/probes.py`, Bake panel > Probes, `pmvr.render_probes`. A probe is a panoramic equirectangular camera assigned to a Runtime layer (UniPlace: the 11 `Probe_*` cameras in the Probes collection). For each checked Bake-for state: `activate_state`, generated results and objects outside Source Root hidden (`EvaluationSnapshot`, as a bake), Cycles at the project Samples, `Probe Size` (Project Settings, 1024 x 512 default), compositor/sequencer off, persistent data on between probes, from a temporary camera in PMVR_WORK at the probe's position, world-aligned (rotation 90/0/0: centre = +Y, u 0.75 = +X, top = +Z, seen from inside); the camera's own rotation is ignored and logged. EXR Half RGB ZIP, `<USD prim name>[_Evening].exr`, written beside and renamed, into `probes/` next to the USDZ folder (Project Settings > Probe Directory overrides). GUI: `render.render('INVOKE_DEFAULT')` with Render display "Keep User Interface" for the run, polled like the bake job; background: blocking renders. Esc (real key) cancels the render job and nothing is written for that probe; Cancel stops after the current probe. Scene camera, render/image settings, the display preference, state and visibility are restored.
- A simulated Esc (`event_simulate`) never cancels a render job, not even a plain F12 render; the real key does (render_cancel fires). `tests/blender_gui_probes.py -- esc` presses the real key only while its own window is in front, otherwise fails.
- UniPlace: 22 probes (Day + Evening) in 5m 14s on OptiX with the texture cache (manual renders took ~1.5-3.5 min each). Compared with the manual probes (rendered 09-27 from Uniplace_upd2_CleanUp.blend): Kitchen01 same orientation (correlation 0.96); the manual LivingRoom01 was turned 178.6 degrees like its camera; the add-on probes are ~1.4x brighter, uniformly over the panorama (current Day lighting with its nested groups and the texture cache, as the atlases). Manual probes (ZIP) kept in `UniPlace/probes_manual_zip_2026-09-29`, DWAA originals in `UniPlace/probes_DWAA_backup_2026-09-29`.
- Regression: `tests/blender_probes_smoke.py` (orientation with a turned camera, Day/Evening content, linear values under AgX, hidden generated and outside-Source-Root objects, USD names, ZIP half, full restore, refusal) and `tests/blender_gui_probes.py` (finish / esc / button; in run_tests.bat gui).

## Queue a whole layer (2026-09-29)

- Setup, layer detail: **Queue N Units** (`pmvr.queue_layer_units`, baked layer types only) adds every unit of the active layer to the bake queue in list order; units already queued and units without objects are skipped and counted in the report. On UniPlace the 11 baked layers queue all 251 units. Test: `tests/blender_queue_layer_smoke.py`.

## Bake queue continues after a stop (2026-09-29)

- Queue entries carry `day_done` / `evening_done`. After each job the queue (`_close_job`) marks the entry once the unit and all its variants for that state are through without a failure (a unit skipped for the state counts as done); the entry leaves the queue when every checked state is done. A new run skips marked states and logs "Continuing the queue: N unit bake(s) already done in it are skipped"; a queue baked through is refused ("Clear Queue and add units"). Cancelled and failed units stay unmarked. The list shows D/E marks and "Already baked in this queue"; Setup unit detail shows "Last baked" per state from `build_records`.
- Stop, Save, reopen (or crash and open the last save), Bake: continues with the rest. Saving mid-bake is supported (recovery markers).
- The UniPlace full rebake started 2026-09-29 16:20:06 with the old code (251 units, Day + Evening, 4K). `C:\Users\papl\Desktop\PMVR_continue_queue.py` converts that queue after a stop: marks entries from SUCCESS build records since 16:20:06 (variants by title), removes units done for the checked states. Tested on synthetic records.
- Regression: `tests/blender_gui_bake_resume.py` (A, B, C Day + Evening; Cancel during B Evening, save, reopen, Bake: only B and C Evening bake, one success per unit and state, queue empty, refusal).

## Fill Empty UV Space (2026-09-29)

- `project.fill_empty_uv` (Project Settings > Bake Defaults, default on). `pipeline/uv_fill.py`: after the compositor denoise (units and variants), the staged 8-bit PNG is filled outside the SimpleBake islands of the baked receivers and the coloured part of the bake margin ring (codes above 2; background reads 0 or 1 after the view transform and denoise). Pull-push in linear light (weighted averages of kept pixels per level, bilinear push back), island pixels written back byte for byte. Working on the final pixels is why SimpleBake's background colour went wrong and this does not: that colour went through the view transform. A failure logs a warning and keeps black. Queue start logs "empty UV space filled/black"; each unit logs the share filled.
- Black cannot mark empty space: UniPlace islands hold legitimately black pixels (1.2% on RugKitchen, 7% on WindowsFrameLR01; whole islands of occluded book sides in LO_Unlit Unit 110), and the background is often code 1. The mask comes from the UVs.
- Measured on UniPlace atlases (copies): LO_Unlit Unit 110 (27 objects, 91k triangles, 4K) 5.9 s, WoodParquette01 (1K) 0.3 s. At the shipped resolution the margin shrinks (4K bake, 1K ship: 8 px -> 2 px, shared units 1 px); island-edge texels of Unit 110 in the 1K mips were 5-9 % darker than their island, 1-4 % with the fill.
- Parquet contours are not this: WoodParquette01 has one SimpleBake island per plank (174 islands for 174 faces), 2 px apart, packed away from their floor neighbours; its island edges show no background darkening (0.998). Far mips mix each plank edge with its atlas neighbour and a smeared margin band; the border pixel ring is baked 5.6 % darker. Fix on the user's side: one continuous SimpleBake island per floor (planar projection), then rebake. WoodParquette23 also has 26 T-junctions.
- Atlases baked before (the 51 Day units of the 2026-09-29 rebake) are not filled; `uv_fill.fill_png(path, generated meshes, bake_margin(...))` can fill them in place (idempotent).
- Regression: `tests/blender_uv_fill_smoke.py` (rasterizer vs brute force incl. large/outside triangles, pull-push kept pixels and odd sizes, real shared-unit bake with fill on/off).

## Guided Beauty denoise (2026-09-29)

- Smeared patches on fabric (the user saw them since SimpleBake) come from the denoiser, not the resolution: SimpleBake and PM VR denoised the saved, color-managed PNG in a compositor scene of their own (Image -> Denoise; Albedo and Normal unlinked; SimpleBake appends `compositor_denoise` from resources/denoise.blend). The scene's compositor and its Denoising Data passes never reach a bake. Without guides OIDN takes fabric weave in a noisy shadow for noise.
- `project.beauty_denoise` (Project Settings > Bake Defaults): Guided (default), Image Only (the SimpleBake way), Off. Guided: `BeautyBakeRuntime._denoise_guided` bakes albedo (Diffuse Color) and object-space normal guides with `bake_receivers` at `GUIDE_SAMPLES` (16) and denoises the float bake with `denoise_image` before the view transform; units and variants. A failure falls back to Image Only with a warning; Esc in a guide bake cancels the queue.
- The Beauty float image is tagged sRGB and holds sRGB-encoded values (Cycles writes them so); `copy_pixels` now encodes the linear denoise result for such a target. Without that the guided result came back ~5x darker.
- `copy_pixels` copied slices through Python lists: 27 of 31 s at 4K. One foreach_get/foreach_set: 0.1 s (also speeds up Lightmap denoise). Guided denoise of a 4K unit: guides ~23 s, denoise ~5 s.
- UniPlace SofaKiitchenSeat Day 4K (scratch copy, paths remapped): the production atlas of 2026-09-29 17:16 (Image Only) keeps less than half of the guided detail on 22 % of the fabric, 10 % in the worst shadows; mean colour within ~1 %.
- Probes now render with Cycles' own denoiser (OIDN, albedo + normal passes); the scene had it off and the compositor is off for probes.
- Guide bakes (`bake_scene.bake_guides`): first version baked each receiver separately in the full scene, 54 blocking calls for LO_Unlit Unit 108 (27 books): 5 min with the UI frozen. Blender prepares the whole atlas (pixel arrays, margin) for every object it bakes, so even one call per guide for 27 objects took 150-175 s. Now the receivers are joined into one world-space copy (`_joined_guide_object`, their materials, SimpleBake active, UVMap render) and baked in a scene holding only it, with the file's texture cache and limit: 10.7 s for both guides, albedo equal to the per-object bake within 0.002. The receivers and sources are untouched; a scene tagged `pmvr_temporary_guides` left by a crash is removed on load.
- Persistent Data does not speed up bake calls: 4 consecutive book bakes took 21-24 s each with and without it. Those ~22 s are mostly the Combined render itself (256 samples of the book's atlas area, full scene).
- Declined by the user (2026-09-29): joining the receivers for the Combined bake too; ~10-20 min per state is not worth it against a 2-3 day rebake. For reference: It would remove the per-object atlas preparation (~3 s per object) and per-call sync; needs an A/B on a multi-object unit (same lighting, the margin computed once) and the queue progress per object replaced by per unit.
- Regression: `tests/blender_denoise_smoke.py` (sky-lit checker at 2 samples: Guided error vs a 256-sample bake about half of Image Only, mean colour kept, guides removed, samples restored, fallback logged).

## First overnight run with guided denoise and fill (2026-09-29 22:30)

- "ChairsSeat" failed: writing the filled PNG over the staged file raised "Could not write image" (the file was held by another reader for a moment; Blender's texture cache builds a .tx in Beauty_Bakes/blender_tx from every new atlas, antivirus scans new files), and `_fill_empty` then called `log.warning(..., with_traceback=True)`, which does not exist, so a recoverable fill failure failed the unit. Fixed: the fill writes a new file and replaces the staged one; `lightmap_baker.images.replace_file` retries a held target for up to a minute and is used by the fill, the image-only denoise and `StagedExport.commit/rollback`; the log call is `log.write(level="WARNING", with_traceback=True)`. A static check of every `log.*` call in modules/ finds no other bad keyword.
- The Image Editor went empty after each unit: a bake switches Image Editors to the image it bakes, and the guide images were removed afterwards. `bake_guides` puts every Image Editor that showed a guide back on what it showed before.
- End-to-end check on a scratch copy of UniPlace, real modal queue (Day, test 25 %): BR_Unlit Unit 144 (4 objects), KitchenMarble with variant Stone, FruitBowl (PBR), LeafIvy01 (Alpha), CurtainsKitchen (Translucent): 6 ready, 0 failed, guided denoise and fill on each, queue drained, no temp files, scenes or images left.
- Regression: `tests/blender_uv_fill_smoke.py` now holds the staged PNG open for 1 s during the fill (os.replace fails with WinError 5 while held) and makes the fill fail (unit stays Ready, black kept, warning logged); `tests/blender_gui_image_editor.py` (fails without the fix).

## Out of memory after a night of baking (2026-09-30 10:49)

- Blender crashed (EXCEPTION_ACCESS_VIOLATION writing to 0, no Python frame) after 12 h and 205 Day units; Windows logged "low virtual memory: blender.exe 135 GB" at 10:48:51 and 10:49:18, and dwm.exe crashed the same second. Cause: every compositor render of a new scene keeps its render buffers until Blender quits, even after the scene is removed. Both denoisers (the SimpleBake-style image-only one too, so this predates the guided denoise) made a new scene per unit: ~0.6-0.9 GB per 4K unit. Measured: a passthrough compositor render of a new scene each time grows 0.6-0.9 GB per call; the same scene reused stays flat. Cycles bakes in a temporary scene (guides) and save_render with a temporary scene do not grow.
- Fix: `lightmap_baker.compositor._compositor_scene` makes one scene (tag `pmvr_compositor_scene`) reused by `denoise_image` and `denoise_external_beauty`; its tree is emptied after each use; `release_compositor_scene()` removes it when a queue ends or is cancelled (Beauty and Lightmap), after a Lightmap Baker batch, and on load (`recover_interrupted_bake`). Other temporary scenes use fixed names via `images.temporary_scene`.
- Real modal queue on a scratch copy, 4K, 16 samples, 10 units + a variant: private memory 6.1 GB after the first unit, plateau ~8 GB afterwards (7.9, 8.3, 8.0, 8.3, 8.6, 8.1), 0 failed. Before the fix it grew ~1 GB per unit.
- The file was last saved at 22:49, so the queue lost its night marks. `C:\Users\papl\Desktop\PMVR_recover_night.py` marks Day baked for queued units whose atlas (and variants) was written since 21:19 (first guided run), whose status is Ready and whose generated objects exist, and records the atlas size; tested on a copy of the 22:49 save: 204 Day units marked, none unproven, BSculpture (the crash unit) first to bake.
- Save During Bake (`project.bake_autosave_minutes`, Project Settings > Bake Defaults, default 15, 0 = by hand): the Beauty queue saves the .blend in `_start_next_job` between units once the interval passed (only while jobs remain; viewport shading restored for the save, wireframe again after) and in `_finish_modal` after everything is restored. A failed save is logged, the bake goes on. Saving UniPlace (190 MB) takes 0.3 s. `operation_running` is SKIP_SAVE but that does not keep it out of a .blend, so load_post clears it. Regression: `tests/blender_gui_bake_autosave.py` (saves after A and B and at the end; a copy from the first save opens unlocked, A out of the queue, no restore markers or PMVR_WORK, user shading; interval 0 saves nothing).

## Ideas from the user (2026-09-29, not started)

- UV adequacy check for all bake units, by rasterising each unit's SimpleBake triangles at the bake size: overlaps between islands and folded faces inside one, gaps below the island padding (dilate each island by padding x size), outside 0-1, zero-area and flipped faces, stretch (UV vs 3D area), texel density spread, atlas fill. A "check all units" list plus a Scene Debug highlight next to UV Health.
- Automatic packing of every unit onto the SimpleBake channel with the project's island padding: UVPackmaster through its Python operators when installed (the user likes its heuristic packing), Blender's packer otherwise. Seams stay manual. A home-grown heuristic packer is a separate, large project.

## UniPlace export test (2026-09-28, 50% test bake, read-only, scratch folder)

- Bake signatures were not stable under Bevel: evaluated UVs differ by 1.2e-7 between evaluations (multi-threaded), and signatures hashed them exactly. 20 units (all with Bevel) were "Structurally incompatible", which blocks their layers' export, and the KitchenMarble variant was refused. Fixed in b4e220d (signature version 2, authored SimpleBake UVs); the stored flags clear with a bake of either state.
- Export (USDZ, flags cleared in memory): about 9 s per state; Unlit/Translucent: every material has its atlas on `st`, sizes per unit. PBR: atlas on `st`, roughness/metallic/normal/emission from source textures on `UVMap`, nothing linked in Blender lost; no colour-correction nodes in PBR/Alpha chains. Source textures of 4K and 8K (Bosch 8K x3, AEG/Hood/LampKitchen/LampTable/Tap 4K) are candidates for Flatten with a smaller long side.
- Alpha: LeafIvy01/02/03/05/06 have no bake, so LO_Alpha/TR_Alpha fail; without them: atlas on `st`, opacity from the source alpha texture (`.r`, `UVMap`); no opacityThreshold is written (the app blends; cutout would need a threshold).
- Emissive (originals): Blackbody colours are lost (emission exported as white x strength); LampsKitchenNightEmissive, Material_002, LampsLRNightEmissive (non-Principled shaders) have no surface shader in USD. Glass exports opacity 0 (transmission).
- Runtime: probes, teleports, SFX, start positions, panels arrive as Xforms with userProperties; 54 collision/navmesh/trigger meshes have no material; the water material (RGB + noise bump) exports as grey, opacity 0.

- Second export test (file saved 22:09, leaves baked): all layers export (flags cleared in memory). The user's Blender still ran the pre-b4e220d code (stored signatures without "2:"), so the KitchenMarble variant failed again and the 20 Bevel units were re-flagged; after a script reload, a bake of either state clears each. USD prim names prefix a leading digit with _ (fixed in variants.usd_name, bd99fe7). Mac notes for the Asset Manager: `PMVR_Export_для_AssetManager.md` on the user's Desktop (files, prim/userProperties layout, UV channels, per-layer gaps with exact Emissive/Glass values, variants, checklist).

## Island padding margin and Flatten to Texture (2026-09-28)

- The bake margin scaled from pixels (4 px at the unit's resolution, so 16 px in a 4K bake of a 1K unit) repainted the edges of neighbouring islands in shared units: Blender bakes each object of a unit separately into the shared image and paints its margin over every pixel outside that object's own islands. With UVPackmaster padding 0.002 the gap is 8 px at 4K. `project.margin` is replaced by `project.uv_padding` (Island Padding, UV units, default 0.002); `setup_ops.bake_margin()` = padding x bake size for one object, half of it for several (neighbours meet in the middle), at least 1 px. Beauty and Lightmap use it; the unit start log shows the margin. Test: `tests/blender_margin_smoke.py` (two objects 2 px apart at 1024: a wider margin repaints the island baked first, the padding margin repaints nothing and splits the gap).
- `modules/node_flatten.py`: Shader Editor operator `pmvr.flatten_nodes` (right-click and Node menu). `Plan` checks the selection (outputs leaving it, no shader outputs, inputs from outside only coordinates into Vector sockets or Value/RGB constants, one coordinate source, no surface/view-dependent nodes, procedural textures need a Vector input, Flat projection only). `_bake_outputs` renders each output with an EMIT bake of a 0-1 UV plane in a temporary scene (Cycles CPU, 1 sample: pixel centres, texture cache and simplify off), the external coordinate source replaced by the plane's UV. The dialog (invoke) asks for the long side, default the largest image's, aspect kept (`output_size`); below the source size it renders at the source size and averages down with `bake_files.scaled_linear` (point sampling would alias). PNG 8-bit (sRGB into colour inputs, raw into data and normal maps) in `//PMVR_Flattened`, never overwritten; the new Image node gets the old coordinate link and the template image's interpolation/extension; selected nodes are removed (Ctrl+Z undoes). Test: `tests/blender_node_flatten_smoke.py` (Base Color and Roughness bake the same before and after, tiling kept, refusals change nothing).
- Material variants (`modules/pipeline/variants.py`), to the Mac contract the user pasted: every variant is its own USDZ with only the object, `Variants/<Object>_<Variant>[_Evening].usdz` in the USDZ folder, object named as in the scene (the app finds it by name), in place; default variant = the object in the main scene, no file; Unlit main scene only (the app swaps the base colour texture); structure (children, slot count/order, textured slots) and UV must match; optional Empty `VariantMarker`; swatch `<Object>_<Variant>_swatch.jpg` 256 px (default only via an explicit `swatch` field); the add-on writes `Variants/materialVariants.json` with the manifest block. Unit data: `variants` (title, material, per-state file and signature), `variant_material` (the material they replace), `variant_default_title`, `variant_marker`. Bake: queue jobs are (state, unit, variant), variants after their unit per state; `BeautyBakeRuntime(variant_id=...)` maps the replaced material to the variant's in `copy_receiver(material_map=...)` (bake copies only; the source never changes), requires the unit's Ready result with the same signature, and keeps only the PNG (`_finish_variant`). Export: `_export_variants` binds a copy of the generated object's material with the variant PNG (tagged so `ExportTextures` scales it), exports the generated object itself (its name is the scene's) plus a temporary `VariantMarker` (a user object holding that name is renamed for the moment), restores everything; `write_variant_manifest` after USDZ jobs. Test: `tests/blender_variants_smoke.py`.
- Both need Blender runs that were postponed while the user baked; see the test results in the commit.

- Scene Debug currently has one modal-controller state for the Blender process. This is reliable for the normal single-window workflow; true simultaneous multi-window debug sessions would need window-scoped controller state.
- `pipeline/viewport_overlay.py` intentionally remains one module until the diagnostic engine settles. Split rendering/cache code from mode classifiers only after the real-scene test, so the refactor does not obscure functional regressions.

## Resolution (2026-09-28)

- 8192 removed everywhere (pipeline and legacy Lightmap Baker); stored enum value 5 is capped to 4096 on load/register (`cap_removed_resolutions`).
- The ÷2/×2 buttons rewrote every queued unit's Setup resolution, with no way back. Replaced by `project.test_resolution` (100/75/50/25%, reset to 100% on load): the test share scales only what the queue bakes (now `bake_size()`, a share of the Bake Resolution). Units record `day/evening_baked_resolution`; the unit status shows a result below Setup and export warns about it.
- Regression: `tests/blender_resolution_smoke.py` (real 25% bake writes a 64 px PNG for a 256 unit, Setup unchanged, export warning, 100% rebake clears it, reopen resets).


## v3 environment prepared — 2026-10-01 (environment only)

Owner authorized preparation only. No addon implementation changes until his explicit command. He is currently rebaking 18 UniPlace units; do not operate his existing Blender process.

### Git and code isolation

- Production: D:\Blender_Python\addons\PM_VR remains main at e985768298206f98bca90c7f435f2ffab846c7e3, clean.
- Tag v2.0.0 created at that commit and pushed to origin.
- Worktree: D:\Blender_Python_v3\addons\PM_VR, branch v3, pushed and tracking origin/v3. Initial addon version remains 2.0.0; SCHEMA_VERSION remains 1.
- Compared all 59 tracked files before adding this note: zero content differences. core.autocrlf=true explains 31 CRLF/LF differences only.
- All 34 test files and run_tests.bat copied byte-for-byte from production. Tests remain ignored. The GUI-path backspace bug is deliberately unchanged: FIRST v3 code commit must fix it and track tests.
- This preparation note remains uncommitted until that first authorized code commit. Production docs were not edited.

### Separate Blender launch

- Launcher: D:\Blender_Python_v3\Blender_v3.bat. Uses the same installed Blender 5.2.2 LTS executable, with BLENDER_USER_CONFIG=D:\Blender_Python_v3\_blender_config and TEMP/TMP in the separate _temp.
- Copied userpref.blend from APPDATA Blender\5.2\config. Changed ONLY the copied preferences: Script Directory D:\Blender_Python\ -> D:\Blender_Python_v3 and temporary_directory -> isolated _temp.
- Original preference SHA256 remained 02867EFFA0D18F766E5FC3EEFB72A91BC207F367FFEFF83E5B246472B941E2CD. Immutable backup: D:\Blender_Python_v3\_environment\userpref_original.blend. Never publish preference files: they may contain private addon settings.
- Preserved 42 enabled-addon preference entries and OptiX: RTX 3090 enabled, Ryzen CPU disabled.
- Copied PM_Tools_Extension and pm_tools_v2 into the v3 Script Directory, excluding .git and caches. Third-party installed addons in APPDATA remain shared for reading; do not update/install/uninstall them for PM VR preparation.
- Startup.blend and recent-file history not copied; starts with an empty scene. Use this launcher for the future new-project acceptance after backup; do not open production UniPlace with it.
- Empty background startup VERIFIED actual PM_VR module at D:\Blender_Python_v3\addons\PM_VR\__init__.py, registered properties, isolated config, unchanged saved GPU/addon settings. No project, render, bake, export or GUI window opened.
- Graphics-dependent third-party addons complain headlessly (UVPackmaster GPU drawing context; Sweep Modifier registration/unregistration). Their preferences were preserved, not disabled or patched. GUI launch was not exercised during the owner's bake.

### Verification tools and evidence

- Reports in D:\Blender_Python_v3\_environment\: environment.json, preferences_setup.json, startup_verification.json, isolation_verification.json.
- Private verification Python: D:\Blender_Python_v3\_verification_python\Scripts\python.exe. Verified imports: Python 3.13.13, NumPy 2.3.4, USD 0.26.3, Pillow 12.3.0.
- Virtual environment reads Blender's installed packages; Pillow installed ONLY in the virtual environment. Private sitecustomize.py points DLL lookup at installed Blender's blender.shared (Blender's original derives an incorrect path inside a venv). Installed Blender/Python files unchanged.
- Versions report: _environment\python_dependencies.json. Pillow pinned in verification_requirements.txt. No full addon test suite or GUI tests run during preparation.
- User's Blender PID 12020 was never controlled, closed or restarted. Production bake activity and GPU load were observed read-only.

### Snapshot and earlier recovery

- Existing snapshot REUSED, not replaced while production bakes: C:\Users\papl\Desktop\PMVR_v3_test. Copied 2026-10-01 16:36:57 +03 from file saved 12:54:44; own Beauty_Bakes and PMVR_Flattened.
- Raw Uniplace.blend SHA256: E727608D7F02ED2D36708045F1844BBB3AE525F185FC6ABE930B67F12585A455. Its paths are still production paths: NEVER OPEN IT FOR BAKE/EXPORT.
- Isolated copy: Diagnostics_2026-10-01\Uniplace_recovered.blend. Previous script audit confirmed no references to production Beauty_Bakes, PMVR_Flattened or UniPlace_Sync. Source textures/assets are absolute read-only references, not copied.
- Earlier fix: 153 stale Day image links corrected; two proven lost Day records restored (Unit 135 / SofaKiitchenSeat); no rebake or signature algorithm changes. Owner confirmed the fix. KitchenMarble.002 / Holes had a separate missing-unit blocker; owner said he would fix it. Recheck in the fresh saved copy after the 18 rebakes.
- Temporary diagnostic exports, intermediate/history blends, scripts/logs (~3.81 GiB) moved to Recycle Bin at owner's request. Recovered copy, restore script/dependencies, concise proofs and detailed record remain: Diagnostics_2026-10-01\NEXT_SESSION.md.
- Created EMPTY Sync_v2, Sync_v3 and NewProject_backup inside the snapshot. NO full baseline yet; earlier 26 diagnostic USDZ outputs were incomplete and removed.

### Next steps after owner commands implementation

1. After the 18 rebakes finish AND owner saves, obtain a consistent fresh file and changed bake artifacts by plain file copying into the test area; keep provenance. Never background-open production .blend.
2. Isolate/audit ALL paths in that fresh copy before any export: no image/library/movie/sound/font/cache/output reference may write to production Beauty_Bakes, PMVR_Flattened or UniPlace_Sync.
3. Export full v2 baseline to Sync_v2: all layers, Day and Evening, plus Variants. Recheck KitchenMarble.002 blocker rather than assume its current state.
4. First v3 code commit fixes run_tests.bat and tracks tests. Follow agreed stages; keep signature_for_receivers, same_structure, all signature inputs, SCHEMA_VERSION, DAY/EVENING IDs and legacy fields (double-write) compatible.
5. GPU jobs only after independently confirming production idle via newest PMVR log and nvidia-smi. Elapsed time does not authorize operating owner's Blender.
6. New separate project is not ready; get its path and back up to NewProject_backup before first v3 opening when it becomes available. No need to ask now.
7. Re-read CURRENT authoritative contract because Mac implementation is underway: F:\CURRENT_PROJECTS\SUBURBIA\UniversityPlace\02_3d\UniPlace\UniPlace_Sync\PMVR_правила_платформи.md. Coordinate Runtime/PBR activation; no production switch or main merge before owner accepts v3.


### First v3 commit: test harness only — 2026-10-01

Corrected both GUI-test backspace paths in run_tests.bat; verified both target scripts exist and no backspace remains. Tracked the 34 existing test scripts and runner; removed their ignore rules. Included earlier environment preparation notes. No v3 feature implementation. Next: merge the separately tested urgent v2.0.1 rebake hotfix from main, as owner requested for production v2.


## v2.0.1 — refresh generated geometry on every successful rebake (2026-10-01)

- Owner explicitly requested this as an urgent v2 fix, independently of v3. Prepared and tested in D:\Blender_Python_hotfix\addons\PM_VR, branch codex/rebake-generated-geometry, before deployment to production main.
- Root cause proven on a FILE COPY of UniPlace: WoodParquette03's original Day record had a legacy unprefixed signature; same_structure deliberately accepts it against a current record for compatibility. Rebaking after repacking therefore skipped geometry replacement. New Day/Evening signatures were recorded, but the generated SimpleBake still had its old layout: 1672 coordinate values differed (maximum ~0.998); vertices, topology and UVMap matched exactly.
- Fix only generated.py: always copy the evaluated receiver mesh on success, while retaining the canonical OBJECT and all IDs/tags/parenting. Retain the old mesh name, assigning it AFTER releasing the old datablock so USD mesh prim paths never alternate .001 suffixes. Preflight validates the fresh mesh's material slots before publishing a PNG.
- PM VR version -> 2.0.1. signature_for_receivers, same_structure, all signature inputs, SIGNATURE_VERSION and SCHEMA_VERSION are UNCHANGED.
- New self-contained regression: tests/blender_rebake_geometry_smoke.py. Real small CPU bakes reproduce legacy-signature UV repacking, vertex/primary-UV edits with the SAME signature, stable identities/mesh names/hierarchy, current USDZ points and st/UVMap, correct packaged PNG, refusal of new evaluated material-slot mismatches before previous PNG/mesh/materials are touched. Old code: 10 failures including stale USD UVs; fixed code: 0. USD prim paths exactly preserved.
- Existing regressions passed: blender_pipeline_integrity_smoke.py, blender_signature_smoke.py, blender_usd_uv_smoke.py, blender_lightmap_smoke.py. Deliberately failing scenarios inside those tests log expected errors.
- Actual WoodParquette03 repair tested WITHOUT rebaking: C:\Users\papl\Desktop\PMVR_restore_WoodParquette03_UV.py. Only copies SimpleBake coordinates after both current Day/Evening signatures match the source and geometry/topology/UVMap match exactly. Refuses edited sources, missing/duplicated/shared generated meshes, missing images, wrong unit/layer or active operations. No external write or .blend save. Repeat is a no-op; source UV flags and view-layer visibility restored.
- Real copy USDZ proof: before/after prim paths identical, st changes to the correct source layout (serialization tolerance 2.24e-7), UVMap and embedded 2048px PNG unchanged. PNG SHA256 in both packages: 55db37fc158a417131e430df175c2112a0e0cc9be2d98ff95a35a1455821eb45. Both baked 4K PNG files unchanged.
- Evidence: C:\Users\papl\Desktop\PMVR_v3_test\Rebake_WoodParquette03_2026-10-01, wood_uv_diagnosis.json, wood_repair_verification.json, regression_before.log/regression_after.log, four regression logs and USD/WoodParquette03_before.usdz / after.usdz.
- Fresh diagnostic copy captured 19:55:24 +03 from owner's file saved 19:52:30; SHA256 AF47B3E546DB96F7C3EEC3DEE61A6AE140A9472DD60EF22D55AFC9702E4148CB. Only that copy was opened. Production .blend and PNGs were never modified; owner's Blender PID 12020 never controlled.
- Unit 144 / WoodBedroom02 / WoodBedroom0204 NOT investigated or repaired, per owner instruction.
- Owner must save and restart/reload Blender after any running bake to activate disk code. For already-baked WoodParquette03, run the guarded repair script in his Text Editor, inspect and save himself. Updating addon code alone does not retroactively repair saved UVs.

## v3 implementation authorized — reference and stage 1 checkpoint (2026-10-01)

Owner explicitly said to start all agreed Blender v3 work, test independently and resume automatically after five-hour limits reset. Do not ask again. Mac handoff reviewed completely: shared guide updated 21:19:18 +03, SHA256 36AD8ADA93FF89E08A0CEADE10BED6BB750291DD51CDF139124E2C8ABDF0D693. Current Mac is ready; sections 0–15 contract, section17 implementation status. Local review notes/copy in D:/Blender_Python_v3/_environment/.

### Reference snapshot
- Production saved 2026-10-01 21:44:23 +03 after queue completed 21:41:27 (10 ready, 0 skipped, 0 failed). Only READ/copy of production.
- Captured 21:48:01 +03: C:/Users/papl/Desktop/PMVR_v3_test/Uniplace_v2_reference_2026-10-01.blend; 192115112 bytes; SHA256 E0044DA7E477F679E55572E535DF36B2BCDDE40E0DFBFF7BFB16877B3381A97B.
- Existing snapshot Beauty_Bakes refreshed via non-destructive file copying: 1006 files, 12.999 GiB; 271 changed/new files copied, 4.456 GiB. PMVR_Flattened similarly refreshed. Textures/assets not copied.
- Detached v2.0.0 reference checkout: D:/Blender_Python_v2_reference/addons/PM_VR (e985768). Production main stayed v2.0.1, untouched.
- tests/blender_reference_snapshot.py opens ONLY file copies inside test-root; remaps images/libraries/clips/sounds/fonts/cache files, variant paths, modifier cache inputs and output folders. Referenced Sync inputs copied locally as needed. Audits against production Beauty_Bakes/PMVR_Flattened/UniPlace_Sync BEFORE export.
- Isolated saved copy: Uniplace_v2_reference_isolated.blend; 1930 paths audited. Evidence reference_isolation.json and v3_reference_snapshot.json in snapshot.
- Full v2.0.0 Day/Evening + Variants baseline RUNNING to Sync_v2/USD; log D:/Blender_Python_v3/_environment/reference_export.log. First Day LO_Unlit (202 objects) and KitchenMarble Stone variant exported. Confirm final PMVR_REFERENCE_EXPORT_OK and reference_export.json before marking stage0 complete. Current tool exec session95560; if unavailable inspect log and owned process command line, NEVER terminate owner's Blender.
- Command: factory-startup/background/threads2/python-exit-code1 + tests/blender_reference_snapshot.py; args addon-parent D:/Blender_Python_v2_reference/addons, source-root original UniPlace, copy raw reference blend, test-root snapshot, --export. Isolated user config/temp; PYTHONDONTWRITEBYTECODE=1.

### Stage 1 in preparation
- Existing first v3 commit fixed GUI backspaces and tracked tests. New uncommitted log.py adds actual Git worktree commit to environment line; missing/slow Git safely reports unknown. Changelog updated.
- New tests/blender_environment_smoke.py PASSED on Blender5.2.2: actual b6db1479108f commit, version2.0.1, persisted log; missing Git and timeout tested. Evidence _environment/stage1_environment_test.log.
- Full background/GUI suites still required at relevant stage; do not confuse one passing test with whole suite.
- Keep separate commits per agreed stage. Reference tool/docs are stage0; log.py/environment test/changelog are stage1. No stage2/looks/probes/PBR implementation yet.

### Resume and next work
- Heartbeat automation id pm-vr-v3 ACTIVE every30minutes in THIS thread, explicitly requested to resume after usage reset. Use ordinary limits, do not consume reset credits. Remain quiet if unchanged; notify only meaningful completion/problem. Disable automation when implementation is done.
- Stage2: exact per-look schema1 export descriptors with complete lists/atomic update/stale layer pruning; export-time original properties context; Glass authored opacity; human variant group title; Runtime warning-only validation.
- Stage3: new-project PMVR folders, pin legacy paths explicitly, preserve UniPlace folders.
- Stage4: arbitrary looks + migration with DAY/EVENING IDs and dual-writing legacy fields for rollback. Preserve ALL signatures/inputs and SCHEMA_VERSION. Compare actual recorded USDZ to baseline using pxr and packaged texture hashes.
- Stage5: configured probe camera collection, all looks, world-aligned Half RGB ZIP EXR, local JPEG previews. Stage6: PBR diffuse-only switch defaults OFF per explicit prompt; Mac ready, enable on isolated test only and prove old/new atlas difference. Stage7: Runtime and post-bake rules in ?.
- Runtime renaming/zones in UniPlace and cleanup of UI/lightmap/margin-type changes OUT OF SCOPE; owner handles scene authoring.
- No original scene, Sync, preferences or running owner Blender changes. No main merge/publish until acceptance. GPU only if fresh production log + nvidia-smi prove idle.
- Separate new project still unavailable; do not ask path until ready, file backup before first v3 opening.

### Stage 1 verification completed — 2026-10-01 21:57 +03

- Background run_tests.bat: 26/26 PASS, exit0. Includes environment/worktree commit logging, real rebake geometry regression, pipeline/visibility/USD UV/variants/probes/denoise/flatten/material/lightmap and UI smoke tests.
- Evidence D:/Blender_Python_v3/_environment/stage1_background_suite.log. Dedicated environment test evidence stage1_environment_test.log.
- GUI cases still to run (in owned Blender processes), especially the two formerly invalid autosave/Image Editor paths; repeat GUI validation after look UI changes.
- Reference v2.0.0 export remains in progress: full Day completed through Runtime with no failures; Evening underway. Code changes do not affect the detached v2.0.0 reference checkout.
