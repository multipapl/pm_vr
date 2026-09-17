# Next session

## Implemented, awaiting live-scene feedback

- Bake isolation now excludes every active View Layer instance of `PMVR_GENERATED`, restores its previous state after success/failure/cancellation, and keeps per-object `hide_render` as a fallback.
- Setup list selection follows the active viewport object through a deferred Blender message-bus update. Registered sources and generated outputs resolve to their semantic render layer and bake unit; Export Original resolves only to its layer.
- Bake-unit batch selection now uses native independent Bool checkboxes. Every checkbox can be cleared, LMB-drag selection is available, and resolution propagation remains limited to checked units in the same render layer.

## Removed after live-scene feedback

- The synchronized Base Color/Roughness/Alpha texture preview was removed. Updating every material in a real scene made the interaction too slow to be useful.

## Optimize checker preview

- Checker Preview uses the supplied A1-H8 image at 1024 x 1024 px with adjustable UV tiling.
- Global Checker applies it to all mesh objects in the scene. Selected Checker persists only on selected objects, and Clear All restores every checker-managed object in the scene.
- Overrides are temporary object-level material-slot assignments, so source material node trees and mesh material assignments remain untouched. The add-on does not change viewport shading mode.
- Standard renders, PM VR bakes, and PM VR exports temporarily suspend every checker override and restore it afterward. Nested render/bake callbacks are handled without re-enabling the checker early.
