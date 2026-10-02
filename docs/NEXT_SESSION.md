# Next session

**Current v3 status (2026-10-02): all authorized implementation stages0–7
complete and verified. Real UniPlace v3 save/reopen and v2 rollback are now
verified too (2026-10-02 03:21). Owner's VisionPro acceptance remains pending;
no v3 release tag or main merge. Latest final evidence is at the end.
Production main stays v2.0.1. Use ONLY isolated v3 worktree and test copies.**

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

## Stage0 complete; stage2 validation running — usage-reset checkpoint 2026-10-01 22:12 +03

- FULL v2.0.0 baseline finished, process exit0, PMVR_REFERENCE_EXPORT_OK31. 14 layers succeeded and2 state-specific layers skipped PER look; 28 main USDZ +2 variant USDZ + materialVariants.json; 4.42 GiB. reference_export.json in snapshot retains hashes/sizes and all results. NO original file opened/written.
- Stage1 committed/pushed 59d39e8 after 26/26 background tests passed. Production main unchanged.
- Stage2 CODE currently in worktree: platform.py export-time source properties context (including deleted-property removal and complete rollback); warning-only Runtime names in validation and export; export_description.py exact schema1 atomic complete partial inventory, stale renamed/deleted/type-changed entries pruned; variant group display title preserves existing variant IDs/files. USD validated BEFORE replacement to retain previous valid package on failure.
- Targeted real Blender/pxr test PASSED: tests/blender_platform_export_smoke.py, _environment/stage2_platform_export_test.log, artifact _temp/pmvr_platform_export_rhwl1fdk. Proves actual userProperties for all6 fields (Unicode/integer/floats), late changes/removal without rebake, Glass opacity.18, scene reflectionIntensity1.7, two-look metadata, partial exports retain other files, variants listed/typeVARIANT/human group title, stale layer pruning, injected invalid USD and failed JSON publish preserve previous files and clean temporary files. Generated IDs/names/tags/signature and original baked PNG SHA unchanged. Initial TEST bug using an invalidated RNA layer pointer after collection.remove was corrected by re-fetching current layer.
- Important code review caught stale TYPE metadata: previous inventory entries now retained only if type/layer still match current entries.
- Reference helper parameterized with --sync-name and --isolated-name so v3 NEVER overwrites v2 copy/export. New tests/compare_reference_exports.py compares actual Sdf prim/UV/shader data, all embedded media SHA and variant JSON (ZIP dates ignored).
- REAL v3 export currently running with new stage2 code: owned exec session70252; _environment/stage2_reference_export.log; output snapshot/Sync_v3_stage2/USD; isolated saved snapshot/Uniplace_v3_stage2.blend. Expected final Sync_v3_stage2_export.json and marker PMVR_REFERENCE_EXPORT_OK. If session unavailable inspect owned Blender command line/log; don't relaunch duplicate or stop owner's Blender.
- Stage2 FULL background suite currently running, owned exec session72961; _environment/stage2_background_suite.log. Expected27 PASS incl new platform test. Inspect results and fix any failures BEFORE claiming stage2 complete.
- Next after export: run private verification Python D:/Blender_Python_v3/_verification_python/Scripts/python.exe tests/compare_reference_exports.py C:/Users/papl/Desktop/PMVR_v3_test/Sync_v2/USD C:/Users/papl/Desktop/PMVR_v3_test/Sync_v3_stage2/USD C:/Users/papl/Desktop/PMVR_v3_test/stage2_reference_comparison.json. Exact original USD/media/variant JSON comparison should pass unless approved source-property additions differ; investigate ANY difference, no blanket acceptance.
- Update Changelog/README with owner behavior, then stage2 commit and PUSH v3. GUI validation still pending (test runner GUI14 cases; former autosave/Image Editor paths especially). Can run GUI-only cases in owned instances; no need repeat unchanged background just for runner option.
- Next stages3–7 remain entirely UNIMPLEMENTED. Follow authorized original prompt. Mac guide in Sync authoritative, format schema1 unchanged, generated SCHEMA_VERSION/signature functions and all inputs UNTOUCHED. DAY/EVENING legacy dual-write required for future look migration. No main merge, Runtime scene renaming/zones, source folder migration or online publishing.
- Rate limits tool at22:11 reported99% five-hour usage; reset2026-10-02 00:34:20 +03. Heartbeat pm-vr-v3 ACTIVE every30minutes in this chat, explicitly requested by owner. Resume after ordinary limit reset, do not consume reset credits. Local test/export jobs may finish while model unavailable; inspect logs before continuing.

## Stage2 complete — 2026-10-02 after ordinary usage reset

- Resumed automatically after reset; no reset credits consumed. Full background suite27/27 PASS, exit0; stage2_background_suite.log. Full isolated v3 export completed:33 files (30 USDZ, variants manifest,2 descriptions), no failures; Sync_v3_stage2_export.json.
- Exact semantic reference comparison PASS for all30 USDZ: every authored spec path, metadata, attribute value (including points/topology/st/UVMap/shaders/connections), packaged media SHA256 and parsed variant JSON match v2.0.0. stage2_reference_comparison.json and _environment/stage2_reference_comparison.log, PMVR_REFERENCE_COMPARE_OK30.
- First comparator compared serialized USDA text, exposing nondeterministic sibling/attribute serialization order. Corrected to compare ALL authored fields by spec path with native USD value equality. Explicit prim/property order and array order remain checked. Self-test proves incidental sibling order ignored but reordered points and authored primOrder detected. No real content difference was waived.
- README/CHANGELOG document stage2 behavior. Protected signature/schema code untouched. Next stage3 working directories, then4 arbitrary looks; GUI tests still pending. Production and owner instance untouched.

## Stage3 complete — 2026-10-02

- New Initialize records separate working-directory format1 and creates PMVR/Bakes, Flattened, Logs, ProbePreviews beside a saved blend; unsaved projects create them on first save. Custom configured paths remain honored. Sync export defaults unchanged.
- Load/register pin absent legacy paths explicitly to Beauty_Bakes, PMVR_Flattened and PMVR_Logs, preserving existing custom output paths. Migration records legacy layout and NEVER moves/copies/creates working folders. Log and Flatten use project settings. Relative output paths declare Blender5.2 PATH_SUPPORTS_BLEND_RELATIVE; verified using installed RNA enum documentation.
- tests/blender_working_directory_smoke.py PASS: initialize unsaved/save/reopen, explicit custom path persistence, legacy implicit defaults, untouched sentinel atlas, legacy custom path preservation and no new PMVR folder on migration. Corrected save-post to migrate legacy metadata before checking whether to create new folders.
- Background suite28 cases:27 PASS; Flatten case initially failed only its new test assertion because Windows paths use backslashes. Normalized separators in test assertion, reran real Flatten test PASS (stage3_flatten_test.log). Working-directory and UI cases rerun after final path-option changes, both PASS. Evidence stage3_background_suite.log and stage3_blender_*_smoke.py.log. All28 cases validated; no need repeat unchanged tests.
- Stage4 next; arbitrary-look integration not started yet. Signatures/schema and production untouched.

## Stage4 validation checkpoint — 2026-10-02

- Arbitrary project lighting implemented in looks.py, separate lighting_format_version1; generated SCHEMA_VERSION1 and entire bake_scene.py/constants.py unchanged against v2.0.0. New Initialize creates one Day look; old initialized files migrate to DAY/default and EVENING/_Evening. Legacy fields and IDs remain present. Legacy reads are authoritative for DAY/EVENING; successful bakes dual-write new result collections and old fields. Build history retains old enum plus new look_id; queue completion has per-look persistence plus old flags.
- Activation validates unique Latin names, default count, distinct/non-nested collections inside Source Root and filename collisions (including variants). All nested active lighting collections turn on. Scenarios lock every configured lighting subtree. Bake/variants/results/queue resume, preview, exported file/variant names/descriptors and debug status use configured look IDs. Look rename removes its former descriptor only after new metadata publication succeeds.
- New real blender_lighting_looks_smoke PASS: one/two/three looks, real CPU bakes and variant USDZ per look, unchanged structure signatures and canonical generated ID/mesh names, nested lights enabled, legacy pointers/settings/results mirrored, save/reopen custom result/queue resume, name refusals, look rename metadata cleanup and filename collision rejection. Evidence stage4_lighting_looks_test.log; latest fixture _temp/pmvr_lighting_looks_o24gtlx8.
- Actual v2.0.0 code opened/exported an earlier v3 three-look fixture without rebake: stage4_v2_rollback.log, PMVR_V2_ROLLBACK_OK. Exact authored USD comparisons match4 Day/Evening main/variant packages. Fixture _temp/pmvr_lighting_looks__awdbmzw, rollback_expected.json and Rollback_v2/. No production code or scene used for writing.
- Full background suite29/29 PASS exit0, stage4_background_final.log. Initial new operator attempted datablock PointerProperty, which Blender operators do not support; corrected to searchable collection/World names in the dialog, actual stored look retains real pointers. Initial suite failure was corrected before final full suite.
- Owned GUI tests PASS: autosave and Image Editor (both previously invalid runner paths), plus new third-look modal queue (3 units, no legacy result overwrite); stage4_blender_gui_* logs and stage4_gui_lighting_looks.log. Added new GUI case to runner. Remaining legacy GUI cases still to run at final validation, especially probe cancellation after stage5.
- REAL UniPlace stage4 all-layer export RUNNING: owned Blender PID32672, exec session46746; output snapshot/Sync_v3_stage4/USD; log _environment/stage4_reference_export.log. DAY done14success2skip; EVENING in progress. MUST finish and compare all30 packages against Sync_v2 before stage4 completion/commit.
- Extended isolation helper audits all new folder settings and variant look-result paths; fresh audited copy Uniplace_v3_stage4_audited.blend,1932paths PASS (stage4_extended_isolation.log). Main export uses same fresh raw source snapshot and only its own output folder; unused mirrored relative paths resolve inside snapshot, no forbidden production references.
- Next: exact reference comparison with compare_reference_exports.py after PMVR_REFERENCE_EXPORT_OK; then stage4 commit/push. Stages5–7 still unimplemented. No production/owner instance changes and no GPU bakes.

### Stage4 complete — 2026-10-02 01:26 +03

Full UniPlace export finished exit0,33 files,0 failures. Exact authored USD/media/variant JSON comparison PASS all30 packages: PMVR_REFERENCE_COMPARE_OK30, stage4_reference_comparison.json and _environment/stage4_reference_comparison.log. Day/Evening migration changes no original exported geometry, UV, shaders, properties or embedded textures. Stage4 committed/pushed after this verification. Next stage5 probes.

## Stage5 implementation/validation in progress — 2026-10-02

- Stage4 committed/pushed4efa117. Stage5 currently UNCOMMITTED: configured probe_collection including nested cameras of ANY type; legacy initialized files auto-select Probes inside Source Root (one-time separate version). One button renders EVERY configured lighting look independent of Bake checks. Existing unconfigured legacy Runtime panoramic-camera fallback retained.
- Probe render uses a new full equirectangular camera at source position, ignoring source orientation/lens/type; removes owned camera data on cleanup. RGB Half ZIP EXR; same Render Result -> atomic local JPEG under PMVR/ProbePreviews (default). Image settings restored; EXR remains usable if JPEG preview writing fails with a warning. Relative preview paths require a saved blend. Runtime assignment warning included in Validate/render, without source changes.
- probe_export.py temporary Empty proxies preserve camera name, authored custom props, parent and WORLD POSITION with identity world orientation/scale. Original authoring cameras/data/constraints/transforms untouched; names borrowed/restored, cleanup on failures. Used only Runtime export, USDZ/GLB.
- New blender_probe_collection_smoke PASS: configured/nested4 cameras including perspective and an unassigned warning,3 looks with all Bake checks OFF ->12 EXR1024x512 Half RGB ZIP +12localJPEG; scene/camera restoration; actual USD Empties/parented world position and identity world basis; injected export failure restores names/data/parenting and removes temporary objects. Evidence stage5_probe_collection_test.log, _temp/pmvr_probes_yv_tt61k.
- Full background suite30/30 PASS exit0; stage5_background_suite.log. Existing probe orientation/linear color/isolation tests also PASS. Owned GUI probe finish PASS for6 Day/Evening files (updated old3-file expectation to new all-look behavior). GUI button test owned process timed out at external180s while original test used2048samples/1024px on2CPU threads; no evidence of a code failure. Reduced deterministic cancellation test workload to256samples/512px with2fixedCPU threads; rerun still required. Real Esc requires its own foreground window; never send keys to another app/owner process.
- Actual UniPlace Runtime review PASS for both looks: stage5_real_probe_review.log and snapshot/Probe_v3_review/probe_review.json, 11 probes per look preserve world positions/properties; all NON-PROBE authored spec values and packaged media identical to v2. Panoramic cameras were already Xforms in v2 because USD camera schema cannot represent panoramas; test now locates source probe Xforms by name rather than requiring Camera children. New perspective-camera fixture separately proves Camera schema removal.
- Stage5 GPU real-scene render not yet run; only CPU tiny fixtures and actual Runtime USD exports so far. Optional real-review script --render-one --gpu can prove one real probe; MUST read fresh production log and nvidia-smi first. Current owner Blender appears closed; verify again, never assume.
- Next: finish GUI cancel verification, optional real-scene probe, stage5 README/changelog/docs + commit/push. Stage6 diffuse-only PBR switch (defaultOFF), real one-unit atlas comparison and original PBR USD channels proof still UNIMPLEMENTED. Stage7 Runtime dictionary/post-bake rules/help tabs and final remaining GUI checks still UNIMPLEMENTED. New project acceptance pending artist, no need ask path now. No production/owner/preferences changes, no main merge, no online publishing.

### Stage5 complete — 2026-10-02 01:56 +03

- GUI finish, Cancel button and real Esc all PASS. Real Esc initially refused to send keys because Windows denied foreground activation. Test briefly attaches its input queue to activate ONLY its own verified GHOST window, detaches, rechecks foreground PID, then sends Esc. No keys ever sent to another process. Reduced cancellation fixture workload256samples/512px; rendering cancelled with0 artifacts and settings restored. Evidence stage5_gui_probes_finish.log, stage5_gui_probes_button.log, stage5_gui_probes_esc.log (PM_VR_GUI_PROBES_OK).
- Actual UniPlace diagnostic probe PASS: Probe_Bedroom01 Day, GPU OptiX,1024x512,1sample, HalfRGBZIP EXR plus same-render JPEG. Diagnostic sampling is not final visual acceptance. Snapshot/Probe_v3_review/probe_review.json, probes/Probe_Bedroom01.exr and PMVR/ProbePreviews/Probe_Bedroom01.jpg; stage5_real_probe_render.log.
- GPU guard recorded before job: freshest production queue log ended21:41:27 with10ready0failed; nvidia-smi20%1554MiB/24576; no owner Blender process. stage5_gpu_guard.json. Production log only read, GPU settings only own factory-startup process. No production/preferences/source changes.
- Full30 background cases, new configured-camera fixture, actual Runtime exports and all3 modal probe cases validated. README/changelog updated. Commit/push stage5 now; next stage6 default-OFF PBR diffuse bake, one real unit comparison, then stage7 help/final GUI regressions.

## Stage6 verification checkpoint — 2026-10-02 02:07 +03

- Stage5 committed/pushed c2b5a78. Stage6 uncommitted: Project Settings PBR Diffuse Only Bool defaultsOFF; only PBR Beauty uses DIFFUSE with DIRECT/INDIRECT/COLOR, other layers/default use original COMBINED. Runtime captures the mode before each unit, UI prevents changing it during operations, logs/stage feedback identify mode. No signature/schema/material exporter changes.
- blender_pbr_diffuse_smoke PASS: real CPU bakes demonstrate emission removal,20x authored emission does not enter flat receiver's diffuse atlas, original linked metallic/roughness/normal and coat/emission values preserved. Exact authored USD+bothUV+allnon-atlasmedia unchanged; only packaged atlas changes. Canonical generated object and signature preserved, settings restored, non-PBR stays Combined. _temp/pmvr_pbr_diffuse_acewgtpz/report.json; stage6_pbr_diffuse_test.log.
- Real FaucetMetal PASS on GPU OptiX,8samples,1024px (unit resolution exceeds requested512). Both branches apply the original LO bake scenario and write ONLY PBR_v3_review/Combined and Diffuse; own reference Beauty_Bakes unchanged and every other unit record unchanged. Original material channels, all authored USD fields and packaged source maps exactly equal; only LO_PBR_FaucetMetal_Beauty.png differs. Mean linear RGB atlas delta.0641953. Evidence snapshot/PBR_v3_review/pbr_review.json and stage6_real_pbr_review.log.
- Important isolation correction: Blender's blend_paths does NOT list external shader IES paths. Earlier diagnostic render logged unread IES profiles under snapshot/assets. Reference helper now traverses node groups and embedded materials/worlds/lights/scenes and remaps external node.filepath too; checks external IES exists. Fresh fully audited copy Uniplace_v3_stage6_audited.blend,1952paths (20more), Sync_v3_stage6_isolation.json. Production source IES read ONLY. Earlier render was a diagnostic with incomplete light profiles; replaced its products/reports with corrected renders. Full historical USD baseline still valid: IES files affect lighting renders, not original baked-package exports.
- Repeated real Probe_Bedroom01 render plus Runtime Day/Evening review on corrected copy PASS, no unread IES errors; stage6_corrected_probe_render.log, snapshot/Probe_v3_review/probe_review.json (1952paths). HalfRGBZIP1024x512 EXR and same-render localJPEG. Guard files stage6_gpu_guard.json and stage6_probe_gpu_guard.json confirm production queue ended and no owner Blender; only own CPU tests running.
- Full31 background suite RUNNING (session39188, stage6_background_suite.log). Finish before stage6 commit/push. Stage7 help tabs/Runtime dictionary/post-bake rules and remaining legacy GUI cases still pending. Protected bake_scene.py/constants.py remain identical to v2.0.0. Production main/scene/prefs untouched.

### Stage6 complete — 2026-10-02 02:08 +03

Full31/31 background tests PASS, exit0 (stage6_background_suite.log). Real FaucetMetal/Runtime/probe results above use corrected1952-path copy with all IES profiles present. README/changelog updated. Stage6 commit/push now. Remaining stage7 help/Runtime rules and final GUI validation; owner acceptance still later.

## Stage7 / final validation checkpoint — 2026-10-02 02:25 +03

- Stage6 committed/pushed73b7be8. Stage7 UNCOMMITTED: ? tabs Artist Rules/Runtime Names/After Bake, current contract dictionary and PC post-bake rules, docs/ARTIST_RULES.md. Rules tab has topic selector so the popup fits; other addon panels/settings not reorganized.
- Added generic Runtime data-only mesh hiding (Zone_/Navmesh/Collision assigned to Runtime inside Source Root) through the existing visibility snapshot for renders. Restores authored flags and crash recovery markers; exports unchanged. No UniPlace Runtime authoring/renaming/zones added and no Lightmap design change. Protected bake_scene.py/constants.py still identical to v2.0.0.
- New blender_runtime_rules_smoke PASS: real pixel-equal bake with/without3 large occluding helper cubes, same signature, visibility/geometry rollback, probe begin/end and failed/prepared bake cleanup, all3 helpers in actual Runtime USD. All help tabs/topics draw with valid Blender RNA/icons. stage7_runtime_rules_test.log.
- Background32/32 PASS exit0, stage7_background_suite.log. GUI15/15 PASS exit0, stage7_gui_suite.log: bake cancel Esc/button, scenarios finish/Esc, selection, duplicate undo, unit list, failed start, resume, autosave, Image Editor, custom looks, probes finish/button/nativeEsc. New actual GUI help test additionally PASS (4 popup configurations, every draw recorded/no exceptions), stage7_gui_help.log. Total48 verified cases. Runner now gui-only plus individual persistent case logs; original GUI paths fixed in first v3 commit.
- Real high-quality FaucetMetal comparison rerun1024px256samples Guided forbothmodes on FINAL audited copy; PASS, stage7_real_pbr_quality.log. Same LO scenario, no unreadIES. Exact all USD/material/UV data and all source maps equal; only baked atlas pixels differ. PBR_v3_review/pbr_review.json and FaucetMetal_comparison.png. Private plotting dependency matplotlib3.11.2 installed only verification venv; its pinned requirements updated. No installed Blender/Python changes.
- Real scene exposed a null/invalidated collection-cache entry while hiding helpers. Helper now materializes tuple BEFORE visibility changes and skips null entries. Actual256-sample test and Runtime regression rerunPASS; targeted final PBR/probe/lightmap checks running. Never modified protected signature inputs.
- Final fresh full export RUNNING, owned BlenderPID19376/session84048, final_reference_export.log. Copy Uniplace_v3_final_audited.blend audited1952paths (including IES); own Sync_v3_final/USD. Await33 files/0failure and exact comparison. New comparator supports explicit --probe-review: only reviewed probe transform/schema changes allowed, world positions/non-transform properties preserved, no Camera prims. Self-test rejects moved probes, changed source properties and any unrelated prim changes. All30 original stage4 packages still compare exact under this updated comparator.
- Original .blend stillSHA E0044DA7E477F679E55572E535DF36B2BCDDE40E0DFBFF7BFB16877B3381A97B, main7e890086 clean. Production userpref last saved21:44:28 (before implementation capture21:48), currentSHA B9D4E1F7F6F4F975589CFE6DA0F8A4A931371D927F764D8E686AF9AF0ED1D49F; older preparation backup16:44 has02867EFF...E2CD. Do not claim its hash still matches the earlier preparation: owner/runtime saved it before implementation. Current work never writes original preferences. Test config remains isolated with original copied42addons/OptiX.
- Heartbeat config currently showsPAUSED when inspected (automation idpm-vr-v3); leave disabled after completion. Final owner guidance only isolated launcher and short tip. No main merge/v3release tag/online publishing. Artist's new project still unavailable; backup by plain file copy before first v3 opening once ready. VisionPro appearance/real-new-project acceptance pending owner, not automatic implementation.

### Final edge-case review — 2026-10-02 02:40 +03

- Full export finished exit0:33 files,14success2skip per look,0fail (PMVR_REFERENCE_EXPORT_OK33). Final exact comparison passed30 packages:28 ALL authored fields identical; both Runtime packages differ only in11 validated probe representations per look. EVERY embedded media SHA and materialVariants.json equal v2.0.0. final_reference_comparison.json / final_reference_comparison.log. Runtime differences are explicitly reported, never marked exact USD equality.
- Additional test proved a rotated parent with non-uniform scale corrupts probe basis through USD TRS decomposition, even with full matrix_parent_inverse. Correct solution for position-only probe data: temporary export Empty is ROOT-level with evaluated WORLD translation; no camera parent rig. Original camera parenting/rotation/scale/constraints untouched. Existing UniPlace probes already root-level, so final intended differences stay unchanged.
- Extended configured-camera test: rotated parent scale(2,3,4), perspective camera child of panoramic probe. Actual USDZ AND GLB PASS world positions/identity basis/no Camera schema, source rollback and injected export failure. tests/blender_probe_collection_smoke.py -- --export-only; stage7_probe_parent_regression.log. Full12EXR/12JPEG mode already validated after final helper changes; render code unaffected by representation fix.
- Re-exported both real Runtime packages with final root-level probe code into Sync_v3_final, partial export descriptions retain EVERY other existing entry; final_runtime_refresh.log. Repeated real-copy review PASS11probes per look and all non-probe fields/media exact. Full final comparator repeated afterwards. Earlier Stage5 parent-hierarchy note is superseded: cameras keep their AUTHORING parents, exported probe point data is intentionally root-level.
- Latest PBR proof:1024px256samples Guided, mean linear RGB delta.0324322; exact authored USD/source maps and other units/reference atlases unchanged. Plot visually inspected: clear removed highlights, labels/legend readable. snapshot/PBR_v3_review/FaucetMetal_comparison.png. Original/preferences/main guard recorded in _environment/final_production_guard.json; protected entire bake_scene.py/constants.py remain unchanged against v2.0.0.

## Implementation complete — 2026-10-02 02:43 +03

- Final comparison repeated after final probe-parent fix: PASS30 packages,28 exact authored USD,2Runtime with exactly11validated probe points per look; ALL media hashes and variant JSON exact. Every Day/Evening descriptor schema1 lists15existing current files (14main +1variant); full inventory33files. Updated final export hashes after Runtime/descriptor refresh; final_acceptance_summary.json. All owned Blender jobs exited; no owner instance controlled.
- Final verification:32background +16GUI cases passed. Additional targeted final PBR, configured probes12EXR/12JPEG and Lightmap PASS after helper-cache correction; non-uniform/camera-parent USDZ/GLB export-only regression PASS after probe fix. Only material defect uncovered by final checks was repaired before completion. No production files/prefs/code changed during v3 implementation, confirmed guard hashes/timestamps; original main remainsclean7e890086.
- Snapshot root C:/Users/papl/Desktop/PMVR_v3_test; captured21:48:01 +03 onOct1 from owner's saved21:44:23 reference. Own Beauty_Bakes(1006files12.999GiB), own PMVR_Flattened; source textures/assets/IES absolute read-only originals. v2baseline Sync_v2/USD; finalv3 Sync_v3_final/USD; safe authoring copy Uniplace_v3_final_audited.blend; full audited1952paths. Raw Uniplace_v2_reference_2026-10-01.blend is provenance ONLY, NEVER bake/export it without isolation.
- Evidence summary: final_reference_comparison.json, Sync_v3_final_export.json, Sync_v3_final/probe_review.json, Probe_v3_review/probe_review.json with actual EXR/JPEG, PBR_v3_review/pbr_review.json and FaucetMetal_comparison.png. Detailed logs D:/Blender_Python_v3/_environment/ and individual tests _temp/pmvr_test_logs/.
- Owner launcher D:/Blender_Python_v3/Blender_v3.bat uses separate config and42copied addon preferences/OptiX. PBR Diffuse Only defaultsOFF; Mac ready, owner may enable in test Blender and intentionally rebake only desiredPBRunits/alllooks. Ordinary original Blender stays productionv2. No automatic UniPlace rebakes or Runtime scene authoring.
- No remaining authorized code stages. Commit/push stage7 and leave heartbeatpm-vr-v3 PAUSED. Future work only after owner feedback: file-backup of artist's new project BEFORE first v3 opening (artist file not ready), actual new-project acceptance, VisionPro visual/end-to-end acceptance, then explicitly authorized release/main merge. Do not turn automatic continuation back on merely because those human acceptance steps remain.

## Actual UniPlace transition review — 2026-10-02 03:21 +03

Owner asked whether existing UniPlace can move to v3 without needless scene/UV
work. Completed real-file verification; a separate new artist project is NOT a
prerequisite for testing this transition. Production isolation remains in force;
this review does not authorize a main merge or edits to original UniPlace.

- Original saved file still SHA256 E0044DA7E477F679E55572E535DF36B2BCDDE40E0DFBFF7BFB16877B3381A97B, identical to captured reference. Every one of1064 copied Beauty_Bakes/PMVR_Flattened files matches original size/mtime. Full SHA256 comparison before/after tests proves all14215891655 bytes unchanged. Existing snapshot already covers this exact saved project AND its external bakes; a .blend-only backup would not roll back later overwritten atlases.
- New tests/blender_real_transition_review.py opens ONLY audited copies under test-root, audits external/output paths (including shader IES and variants), fingerprints original v2 RNA fields recursively and actual mesh topology/UV/material indices, source/generated identities/parent transforms/bindings, material/world/light/node-group graphs and image references. No unit rebake. Optional USD export may produce tiny temporary CPU colour maps through the existing exporter, never overwrite unit atlases.
- Fresh baseline made with actual production v2.0.1 code in isolated hotfix checkout.1314 objects,893 mesh datablocks,760 materials,964 image datablocks,250 units,16 layers,1 material variant.249Ready per look; the two remaining look-specific blank records are expected: LampDaylight_PBR has no Evening bake, LampEvening_PBR no Day bake. Both sets remain valid for their respective exports.
- Real v3 migration plus TWO saves/reopens: all three checkpoints have ZERO fingerprint differences. Every configured layer resolves its Day/Evening objects with strict generated material binding, no missing/incompatible bake, no missing generated mesh. Full post-roundtrip export:14successful+2expected skipped layers each look,30USDZ plus variant JSON and2 descriptors. Fingerprint remains EXACT after export too.
- ALL30 roundtrip v3 packages compare exact to accepted Sync_v3_final/USD: every authored USD field/UV/shader/property, all packaged media SHA256, variant manifest. Earlier accepted v3-v2 difference remains intentional probe representation only; this new roundtrip comparison has NO allowed differences, including Runtime.
- Saved v3 file reopened with actual v2.0.0 AND current v2.0.1: ZERO legacy/geometry/material fingerprint differences, full Day/Evening binding resolution passes. v2.0.1 full rollback export ALL30 packages compares exact to original tag-v2.0.0 Sync_v2/USD, including Runtime, embedded media and variants. v2.0.0 reopening/bindings checked; its full export was not repeated in this review.
- Actual GUI process with isolated owner-style preferences/42 enabled addons (no factory-startup): loaded v3 file, exact fingerprint and both-look resolution PASS. No PM VR errors. Existing third-party Sweep Modifier unregister NameError and BlenderKit unsubscribe timeout on shutdown are also in preparation-era startup_verification.log; unrelated to v3 file migration. Production preferences unchangedSHA B9D4E1F7F6F4F975589CFE6DA0F8A4A931371D927F764D8E686AF9AF0ED1D49F. All owned Blender jobs exited.
- Protected entire bake_scene.py/constants.py STILL identical to v2.0.0; no schema/signature code changed. Production main remains clean7e890086. This work adds verification/documentation only, no addon behavior change. Heartbeat remainsPAUSED.

Evidence root: C:/Users/papl/Desktop/PMVR_v3_test/Transition_UniPlace_2026-10-02.
Baseline/transition_review.json; V3/transition_review.json and
V3/UniPlace_v3_saved_2.blend; GUI/transition_review.json;
Rollback_v2_0_0/transition_review.json; Rollback_v2_0_1/transition_review.json;
v3_roundtrip_usd_comparison.json; v2_rollback_usd_comparison.json;
assets_before.json/assets_after.json; source_asset_inventory.json;
V3_inventory/transition_review.json; new_mac_contract_inventory.json.
Detailed logs: D:/Blender_Python_v3/_environment/transition_*.log/.err.

### Concrete remaining work is the NEW MAC data contract, not file migration

- PBR Diffuse Only remainsOFF. Installing/opening v3 does NOT invalidate bakes and requires no UV rework/full-scene rebake. EnablingON does NOT convert existing atlases automatically; for the new Mac shader intentionally rebake only16 PBR units for their applicable Day/Evening looks (30 results). Existing non-PBR bakes stay usable. Keep old external atlases in the snapshot for rollback.
- This scene has LO_StartPosition/TR_StartPosition but no exact StartPosition; SkyboxDaylight/SkyboxNight and8 legacy Runtime names do not meet the new role dictionary. V3 deliberately preserves them. Known mappings/properties from the authoritative guide can be prepared on a copy; do not guess/remove unknown objects or claim the unmodified scene is already new-AM-ready.
- Current Glass USD opacity is0 with NO object opacity override. New Mac no longer inserts the old0.18, so simply feeding unchanged data to the new Mac can make glass invisible. Current Translucent has NO opacity/brightness overrides; set the guide's authored values for the desired appearance. These properties reach generated export objects without rebaking.
- Old probe renderer already used world-aligned +90X panoramas, as new renderer does. Do not invent a mandatory full probe rebake merely because exported probe cameras now become position-only points; original22EXR files exist. Actual headset appearance/new Mac assembly still need validation with a prepared new-contract export. Source-original, its Sync and bake folders remain read-only.

## Shared Mac handoff updated — 2026-10-02 03:37 +03

- Owner explicitly requested writing all information important to Mac into the shared block. Appended section18 (PC/Blender v3) to F:/CURRENT_PROJECTS/SUBURBIA/UniversityPlace/02_3d/UniPlace/UniPlace_Sync/PMVR_правила_платформи.md. This is the authorized DOCUMENT-ONLY exception; original blend, bakes, USD/probes and production code/preferences remain untouched. All existing sections0–17 preserved byte-for-byte. Original document backup in _environment/PMVR_platform_rules_before_PC_handoff_2026-10-02.md.
- Shared document newSHA256 34F5532658BD61491AB8974D45D82166692EADA30F74241A4A391BA1587306E3; priorSHA36AD8ADA...D693 remains historical review of Oct1, not current hash. Write/readback evidence _environment/shared_handoff_write_2026-10-02.json. Added section identical to tracked docs/MAC_HANDOFF_2026-10-02.md (line ending normalization only).
- Handoff covers implemented export/look/property/probe contracts; intentional2Runtime/11probe differences; default-OFF PBR and16units/30results; no mode marker in signatures/descriptions (Ready or JSON alone does NOT establish diffuse-only readiness); real migration/rollback/full-media comparisons; unresolved original Runtime/glass/translucent/emission authoring; local-only evidence location; first AM/VisionPro checks and backup/external-atlas limits. Original22EXR not automatically invalidated by point-only exports. No Mac agent messaged, no data export/sync/publishing performed.
- Ordinary Blender still productionv2.0.1 from D:/Blender_Python/addons/PM_VR. Restart does NOT load the isolated v3 folder. Owner uses D:/Blender_Python_v3/Blender_v3.bat for v3. Dev bl_info still2.0.1 until release; identify by branch/path/look list. No switch/main merge authorized by this request. Owner asked for concrete things to inspect, not a production deployment.

## Artist workflow tools complete — 2026-10-02

Owner accepted contextual custom-property UI, clear exclusive Sources/Generated
preview, list-to-viewport selection and compact Runtime convention tools. Added
the requirement that generated PBR/Alpha materials select the actual Beauty
texture rather than their preserved source normal/opacity texture. All changes
are in v3 only. No release, main merge or original scene authoring authorized.

- New authoring.py: one Object Tools box in Setup/Bake/Export. Exact optional source properties by semantic layer/Runtime role; generated selection resolves a UNIQUE original. Set inherits existing appearance, X removes the override, explicit per-field copy to compatible selected originals. Linked data and active operations protected; existing values preserved. Scene reflectionIntensity in Project Settings. SFX title is an optional audio mixer GROUP label; conflicting group values reported, numeric instances share media.
- One Runtime dialog creates correctly typed Empty/Zone box/probe camera or names an existing object; type-filtered roles with STABLE enum numbers, exact name preview, zone/look choices, occupied/normalized name checks, singleton StartPosition, numeric music instances. Cannot move a bake-unit member to Runtime. Source IDs preserved on existing objects; no geometry/UV conversion. Probe cameras join the configured collection. Sky-specific names include _Day too and agree with lighting collection membership; All is a truly shared sky. Editing/showing a Runtime original returns Sources view. No automatic media generation/rename or scene-wide conversion.
- Check Runtime lists type/name/helper/pair/zone/media/probe-panorama/property/group issues; click a name to select. AM still validates final exported completeness/placement. It does not block existing exports. Unknown roles/RK shader geometry are not guessed.
- preview.py: Sources/Generated exclusive by default, explicit Both. Legacy Both/Neither migrates to Sources; an existing Generated-only choice remains Generated. Active scene view reapplied on load, other scene settings cannot override the current viewport. Preview metadata has its OWN format1, entirely separate from SCHEMA_VERSION.
- Queue records successful base commits by unit/look/mode/source IDs (not consumed queue entries, old Ready states, variants or skipped jobs). After all evaluation/scenario restoration, show Generated + Last Queue; prefer the former look only if actually committed, otherwise show a freshly baked look. Partial/cancelled queues show only committed results; all-failed queues show ZERO fresh results instead of old successes. Filter can be removed and NEVER limits render/export membership. A visible fresh unit is selected so the Shader Editor follows generated materials instead of a hidden original.
- List indices select visible source/generated unit members in Setup and Bake; reversed scene selection updates lists without expanding a manual single-object selection. Navigation guards distinguish programmatic target changes from a user's list click; running bake receiver selection never disturbed. Find-in-Viewport magnifier; selecting an out-of-scope generated unit releases last-queue focus.
- Generated Beauty node is active and uniquely selected on construction, state binding and loading existing editable generated materials. Source shaders untouched. Entire protected bake_scene.py/constants.py still match v2.0.0; no signature/schema/ID/legacy DAY/EVENING changes.

Verification:33 background cases and18 GUI cases validated. Full background
run had one old variants TEST assuming index assignment preserved a selected
different source; updated that scripted assignment to the explicit navigation
guard and reran PASS. All other32 cases passed in the suite. New actual small
CPU PBR/Alpha bakes, original/normal/opacity node preservation, late property
edits, safe role creation/scoping, multi-scene preview, save/reopen and actual
USDZ export with a hidden focused-out mesh PASS. Actual modal two-look queue,
Evening-only partial and all-failed queues PASS; cancelled/resumed/autosave/
Image Editor/scenario/probe regressions PASS. New real-window drawing test
covers contextual custom-property rows and Runtime/check/settings dialogs.
Final targeted runs after edge fixes PASS; no GPU unit bake was used.

Real existing UniPlace snapshot: save/reopen fingerprint of1314objects,
893meshes and source/generated/material/UV/ID/legacy records exact, with only
the expressly allowed legacy Both->Sources switch in Scene.001. Main Scene's
Generated-only switch retained. Full Day/Evening export14success2expectedskip
per look,30USDZ+variants manifest+descriptions. ALL30 packages compare EXACT
to Sync_v3_final, every authored field and embedded media SHA, no allowed
differences. Final reopen after final view migration changes also PASS.

Evidence: _environment/authoring_acceptance_summary.json (33/18/30),
authoring_background_suite.log; authoring_final_smoke.log;
authoring_gui_suite.log; authoring_final_blender_gui_*.log;
authoring_gui_tools.log; authoring_transition.log;
authoring_final_reopen.log; authoring_reference_comparison.log.
Snapshot/Authoring_UniPlace_2026-10-02 contains saved private blend, full USD,
transition_review.json, Final_reopen/transition_review.json and
usd_comparison.json. Existing snapshot still originates Oct1 21:48, not a
fresh capture of today's owner saves. The CURRENT original and normal prefs
hash/mtime are recorded in authoring_acceptance_summary.json and differ from
early Oct2 historical provenance; do not claim they still match old E004/B9D
hashes. All own Blender inputs were private copies/fixtures; original paths
were read/hashed only. Production main remains clean7e890086. Heartbeat stays
PAUSED. Owner activates code by restarting the isolated Blender_v3.bat;
ordinary Blender remains production v2.0.1.

## 2026-10-02 — Original-object lists and Setup organization

Owner reported that Glass/Runtime object lists could not select or scroll and
requested separate Setup blocks, with property/Runtime authoring in Setup only.
This follow-up supersedes the earlier single Object Tools box in all stages.

- Setup original objects now use PMVR_UL_OriginalObjects, a native clickable,
  scrollable alphabetical list with a compact search field. All Glass,
  Emissive, Runtime and legacy unbaked members remain accessible; the previous
  eight-label truncation is removed. No export membership code changed.
- The selected row tracks an object session identity in memory and resolves its
  CURRENT native collection index. Rename/delete/reorder/layer switches cannot silently
  target a different object. Clicking shows Sources and selects that original;
  reverse viewport sync highlights it without replacing manual multi-selection.
  Navigation/bake/Edit Mode guards remain. Excluded collections stay excluded;
  render flags, source IDs, layer ownership and bake IDs stay unchanged.
  No saved object pointer or extra datablock user: deleting a focused original
  really removes it, and a new object with the same name cannot steal its row.
- Setup has separate Viewport, Runtime and Object Properties boxes. Contextual
  source properties, Create/Set Runtime and Check are absent from Bake/Export.
  Bake retains its existing preview controls for reviewing committed results.
  Exact platform property keys and inheritance/bulk-copy behavior unchanged.
- Real-window verification of a brand-new project exposed probe initialization
  deferred until read-only Bake drawing. Initialize Project now finishes the
  existing probe migration before drawing. Probe behavior/formats unchanged.

Verification for this UI follow-up: seven targeted background cases and four
real-window cases pass (not a claim of rerunning the whole previous suite).
Background: original-list navigation, UI wiring, semantic schema, Setup,
export visibility, artist tools with real small CPU PBR/Alpha bakes and USDZ,
and probes with real CPU renders. Real windows: 30 Glass/45 Runtime rows clicked
and wheel-scrolled through their last entries, search, Setup/Runtime/settings
dialogs with Bake/Export authoring absent, existing unit operations and
selection sync before/after File Open. Save/reopen, rename/delete, no wrong-layer
highlight, running-operation guard, excluded collection and stable membership
checks pass. No GPU bake or new full-UniPlace export needed for this UI change.

Evidence: D:/Blender_Python_v3/_environment/original_list_* logs;
original_list_acceptance_summary.json records the final commit and push.
Screenshots are private generated fixtures under _temp/pmvr_original_gui_* and
_temp/pmvr_setup_ui_*. run_tests.bat includes the new original-list GUI case;
the new background smoke is discovered automatically. Entire bake_scene.py
and constants.py still match v2.0.0; no signature/schema/legacy-field edits.
All edits remain in v3 only; production main is clean7e890086. No owner scene,
Sync/bake folders, normal preferences or running Blender were modified by us.
Heartbeat remains PAUSED. Restart the isolated Blender_v3.bat to load changes.

## 2026-10-02 — Simple property editing and compact Setup/Bake

Owner reported selection jumping while switching Set/Inherit and requested
only adding missing applicable fields and editing existing fields in Setup.
This supersedes all older descriptions of Set/Inherit/Bulk/copy/remove UI.

- Object Properties now has Add for a missing field, or its editable value
  when present. Only the active original is edited (a uniquely mapped original
  when generated geometry is selected). Existing values and selection remain.
  Missing properties still inherit material/platform defaults until Add.
  Add takes unambiguous material values; otherwise the documented initial
  numeric value is 1, order is 0 and title is suggested. Linked/ambiguous Glass
  Alpha is NOT evaluated: its initial opacity is 1, editable immediately.
- Removed the property mode dialog and adjustable-last-operation registration.
  Hidden legacy scripted actions remain compatible, but no modes, Bulk, copy
  or removal buttons appear in UI. The Add button captures target session UID
  and cancels if its source has changed instead of editing another selection.
- Native collapsible secondary sections remember expansion in Blender's
  sidebar region, without adding saved scene settings. Setup layers and
  object/unit work lists stay visible; small original lists use fewer empty
  rows. Viewport, Runtime and Material Variants start collapsed. Object
  Properties starts open for discoverability and can also be collapsed.
- Bake lighting/look choices, queue, Test and Bake/Cancel stay visible first.
  Bake Scenarios (all collections/defaults included), Probes and Viewport
  start collapsed below the queue/operation result. Authoring remains only
  in Setup. Preview choices fit one horizontal row inside Viewport.

Verification: five targeted background and five real-window cases PASS,
not a rerun of the preceding full acceptance suite. Background: UI wiring,
original navigation/property targeting, actual small CPU PBR/Alpha authoring
bakes and USDZ, export visibility with USDZ/GLB, and variants. Real windows:
Add/edit/Undo/Redo with linked-opacity Glass and a previously selected PBR
unit (selection and IDs preserved), real Setup/Bake drawing/default folds and
Runtime/settings dialogs, unit list operations, actual modal authoring queues
including partial/all-failed results, and a CPU scenario queue with restoration.
Repeated Add preserves the value, multi-selection remains intact and only the
active original receives the field; a stale Add target cancels safely.

Evidence: D:/Blender_Python_v3/_environment/compact_ui_blender_*.log and
compact_ui_acceptance_summary.json (final commit/push and ten case markers).
Actual inspected fixture screenshots: _temp/pmvr_setup_ui_t7h0jql4/setup.png
and _temp/pmvr_bake_ui_uzl_5oo6/bake.png. New real-window property-selection
case is included in run_tests.bat; existing UI regressions cover folds.

Entire bake_scene.py and constants.py still match v2.0.0. Export membership,
signature/schema/IDs/legacy fields unchanged. Production main remains clean
7e890086; original/normal preference hashes were not rechecked in this UI
follow-up and no own writes target those paths. All own Blender inputs are
private fixtures, isolated config/temp, CPU; no GPU bake or new full-UniPlace
export needed. Heartbeat remains PAUSED. Owner restarts only the isolated
D:/Blender_Python_v3/Blender_v3.bat; ordinary Blender remains v2.0.1.

## 2026-10-02 — Pre-release review and folder organization (active)

Owner authorized: prepare existing-project folder organization before promotion,
fresh whole-addon review, update the shared Sync guide. Production promotion/main
merge/release tag remain unauthorized. Explicit shared-guide write is the only
exception to the production Sync write restriction. No subagents used.

Implemented and verified on private fixtures: explicit Organize Local Files,
SHA256 copies + live blend backup + path remapping + save-before-archive + journal
rollback; no automatic move on load. Recognized Beauty_Bakes/PMVR_Flattened/
PMVR_Logs/Lightmaps/PM_Selected_Textures -> PMVR/Bakes/Flattened/Logs/Lightmaps/
Textures. Complete pre-migration blend and old folders remain in PMVR/Backups.
Collision, save failure, multi-scene, save/reopen, repeat no-op, closed-project
rollback, linked-node refusal and real GUI confirmation tested. Source data,
Sync and unknown/manual/other-addon folders stay where they are.

Fresh review fixes: default-look rename partial inventory retention; deleted-look
descriptor cleanup; malformed unrelated metadata handling; Ready-only atomic
variant manifests; final metadata errors in summary; RK shader-name protection
and Runtime naming exemption in Optimize; project configuration locks during
operations. New Lightmaps/Optimize externalized textures use PMVR. Independent
working-directory format2 pins implicit old paths from v2 and earlier v3 before
new defaults are applied. No generated SCHEMA_VERSION/signature input changes.

Full fixture suite: 35 background +21 real-window cases PASS (56 total). First
run found a new TEST had assumed 'v2:' rather than the ACTUAL protected '2:'
signature prefix; corrected the test to import SIGNATURE_VERSION, then full
suite passed. Latest linked-node/reparse protection also has targeted PASS.
Static inventory covers61 Python files/whole addon including selection_targets;
no duplicate registration IDs or invalid log keywords. Semantic scope/findings
are documented in docs/RELEASE_CANDIDATE_REVIEW_2026-10-02.md.

Fresh snapshot: C:/Users/papl/Desktop/PMVR_v3_test/ReleaseReview_2026-10-02.
Raw_reference.blend is a FILE COPY of the current saved original; provenance
is in _environment/release_production_guard.json. Raw copy was isolated with
load-time disk logging disabled. Own Beauty/Flattened/Logs copied from production;
textures/assets/IES remain absolute read-only originals. Every output path
audited inside the private root. Do not bake/export Raw_reference.blend.

Before/ contains full Day/Evening USDZ+Variants and scene fingerprint (PASS).
UniPlace_review.blend was then successfully organized and reopened: 1070 files,
14217044288 bytes, all stored DAY/EVENING bake fields exactly preserved. Journal
phase COMPLETE; before blend and original folders are under private PMVR/Backups.
The Before exporter loaded path format1 before the independent format2 upgrade;
allow that one deliberate project field change plus exact remapped file paths
in the before/after fingerprint comparison, nothing else.

PENDING: After/ save/reopen + full USDZ export is running in the isolated process
(release_after.log). Let it finish; compare all packages with Before/ using
tests/compare_reference_exports.py, and normalize only verified moved-file paths
and path-format1->2 in the fingerprint. Then append the new Mac/history section
to the CURRENT shared guide (backup its bytes outside Sync first), record final
acceptance/production hashes, commit/push review docs. Do not redo snapshot or
rerun full fixture suite without new changes/failures. Original production main
is clean7e890086; bake_scene.py/constants.py still match v2.0.0. Heartbeat PAUSED.

Follow-up during final review: real Save As to an After/ subfolder exposed
a test harness error (relative_remap=False) and hidden variant file strings
which Blender never remaps. The helper now uses normal relative_remap=True;
load/new results pin variant files absolutely without changing signatures or
legacy fields, and organization pins custom lightmap metadata absolutely.
Unsaved relative references stay relative until a saved root exists. Native
image paths still follow Blender remapping. Folder smoke includes actual
Save As/reopen/rollback. Seven background +three GUI targeted cases PASS.
Final After cycles0/1 both PASS (1952 audited paths). Before/After fingerprints
match exactly with only 502 verified folder-path remaps and path format1->2.
Actual full export/comparison and shared-guide append remain pending.
