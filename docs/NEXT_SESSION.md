# Next session

## Unified layer taxonomy

- One authoritative `layer_type` drives both runtime meaning and Blender bake/export behavior; there is no parallel processing-profile entity.
- Types are Unlit, PBR, Alpha, Translucent, Glass, Emissive, Video, and Runtime.
- Behavior: Unlit/Translucent bake unlit Beauty; PBR preserves PBR channels; Alpha preserves Alpha; Glass/Emissive/Video/Runtime export originals.
- New projects initialize these eight general layers instead of the project-specific Scene/Reflect/Translusent/Curtains/Homepod set.
- The model was not yet in production, so no legacy schema or migration layer is retained.
- Generated objects, materials, and images store the flat `pmvr_layer_type` custom property. Runtime source metadata still needs a manifest or export proxy for a uniform Mac-side contract.
- Cameras are now included by both USDZ and GLB exporters, allowing Runtime layers to carry probe-camera transforms directly.

## Implemented, awaiting live-scene feedback

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
