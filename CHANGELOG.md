# Changelog

## v3 development

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
