# Changelog

## v3 development

- Logs identify the addon commit, including isolated Git worktrees.
- The GUI test runner now runs the autosave and Image Editor tests that had invalid paths.
- Tests and an isolated reference export tool are tracked in Git.

## 2.0.1 — 2026-10-01

- Every successful rebake refreshes generated geometry and both UV channels from the source; object IDs, names, parent links and USD paths stay stable.
- Validate the fresh mesh before replacing the previous bake result.
