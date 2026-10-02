# Changelog

## 3.0.0-rc.1 — 2026-10-02

- Explicit Organize Local Files consolidates verified legacy working data into PMVR with live path remapping, save-before-archive, complete backups and rollback. New Lightmaps/externalized textures also live inside PMVR; old implicit paths remain pinned on load.
- Partial export after a default-look rename preserves other committed layers; removed-look descriptions are retired. Variant manifests include only ready current bakes and publish atomically. Final metadata errors reach the export summary.
- Optimize preserves RK shader names and recognizes Runtime naming conventions. Project configuration cannot change mid-operation.
- Variant file references and legacy lightmap metadata survive Save As into another folder; native image paths retain Blender's standard relative remapping.

- Object Properties now has only Add for a missing field and direct editing for an existing one; no mode/redo dialog or bulk/copy/remove controls. Selection and existing values are preserved.
- Secondary Setup/Bake sections use native collapsible panels. Work lists and the Bake/Cancel controls stay visible; scenarios, probes and preview sit below the queue.
- Original-export layers use a native clickable/scrollable object list with search and stable object targets; viewport selection highlights the matching row.
- Setup separates Viewport, Runtime and Object Properties. Runtime authoring and contextual properties no longer clutter Bake or Export.
- Contextual Object Properties edit exact platform properties on originals, including when a generated mesh is selected; properties stay optional.
- One Runtime dialog creates markers, zone boxes and probe cameras or names existing meshes; exact role/zone/look names, object types, conflicts and linked media are checked. Sky look names and collection membership agree.
- Sources/Generated are exclusive by default; Both is explicit. Finished queues show only committed results in an appropriate look, with a removable Last Queue filter that never limits export.
- Clicking a Setup or Bake queue unit selects its visible members; reverse selection stays narrow and running bake receiver selection is protected. Find in Viewport frames a unit.
- Generated Beauty is the active selected texture node for PBR/Alpha and other Beauty materials, including existing saved results; source shaders and bake signatures are unchanged.
- Help has Artist Rules, Runtime Names and After Bake tabs, with a matching artist guide in docs.
- Data-only Runtime Zone/Navmesh/Collision meshes do not shade Beauty/Lightmap/probe renders; original visibility and export membership are preserved.
- Probe Empties export at world positions without camera ancestry; rotated/non-uniformly scaled parents preserve positions in USDZ and GLB.
- Test runner supports gui-only and keeps a separate log for each case.
- Optional PBR Diffuse Only bake (default off) excludes receiver highlights and emission, preserving original exported PBR channels and all compatibility signatures.
- Probe cameras come from a configured collection, including nested cameras of any type; legacy Probes is selected automatically.
- Render Probes covers every lighting look, writes world-aligned RGB Half ZIP EXRs and local JPEG previews from the same render.
- Runtime exports represent probes as Empties at their world positions; original cameras are restored even on export failure.
- Lighting is a project list with one default and arbitrary additional looks; single-look projects work without an empty Evening.
- DAY/EVENING IDs, result fields, generated tags and signatures remain compatible with v2. New bakes dual-write legacy results.
- Bake/variants/queue resume, scene scenarios, preview and export support every configured look; validate suffix/variant filename collisions before a bake.
- New projects initialize PMVR/Bakes, Flattened, Logs and ProbePreviews; legacy projects retain their existing folders and explicit defaults.
- USDZ exports write atomic per-look descriptions, retaining other current files during partial exports and removing stale layer entries.
- Export uses current source object properties, including Glass opacity, without rebaking or changing stored generated objects.
- Material variant groups have an optional display title; IDs and filenames stay stable.
- Runtime naming validation reports warnings without renaming objects or blocking export.
- Logs identify the addon commit, including isolated Git worktrees.
- The GUI test runner now runs the autosave and Image Editor tests that had invalid paths.
- Tests and an isolated reference export tool are tracked in Git.

## 2.0.1 — 2026-10-01

- Every successful rebake refreshes generated geometry and both UV channels from the source; object IDs, names, parent links and USD paths stay stable.
- Validate the fresh mesh before replacing the previous bake result.
