# Changelog

## v3 development

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
