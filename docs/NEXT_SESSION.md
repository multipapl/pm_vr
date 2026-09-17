# Next session

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
- Scene Debug starts with Ctrl+Shift+D or by clicking any Bake/Layers/UV/TD/Checker channel button. While active, 1 shows Bake Status, 2 shows Render Layers, 3 shows UV Health, 4 shows Texel Density, 5 shows Checker, bracket keys cycle modes, and Esc exits. All unrelated events pass through to Blender. The lower-left HUD includes the controls.
- UV Health validates the reserved first two UV channels (UVMap, SimpleBake). Invalid bake-capable meshes are red, valid meshes green, and explicit Export Original meshes muted.
- Texel Density evaluates only the second UV channel, which must be named SimpleBake, against the project px/cm target. Objects in a valid bake unit use that unit's resolution; objects not yet added to the pipeline use Default Unit Resolution. The color varies continuously from blue (below target), through green (near target), to red (above target); invalid UV or geometry data is magenta.
- Checker is a GPU-only Scene Debug channel on key 5. It uses the shared A1-H8 asset without changing materials, defaults to the second SimpleBake UV channel, can switch to the first UVMap channel, and reuses the existing checker tiling setting. Missing selected UV channels are magenta.
- Scene Debug mode is session-only and is forced Off whenever a blend file loads, so a saved active overlay cannot leave an orphaned HUD without its modal controller.
