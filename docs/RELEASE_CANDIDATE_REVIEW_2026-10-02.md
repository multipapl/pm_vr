# PM VR v3 — review before production promotion

Status: isolated v3 only. Owner requested folder organization, a fresh whole-addon
review and an updated shared Mac/history document. Production promotion and a
release tag are not authorized yet. Original UniPlace and production settings
remain untouched; the shared guide is the sole explicitly authorized Sync write.

## Review scope

The static inventory covers every Python file at addon root and in modules,
including legacy Optimize, collection export, material rebuild and lightmap
tools. Syntax, duplicate registration IDs and log-call signatures are checked.
Semantic review followed registration/load/save/undo recovery; identity and
membership; generated geometry/material commits; queue cancellation, resume
and autosave; UV/color/Alpha/PBR processing; temporary export rollback; probes;
Runtime naming/authored properties; descriptors/variants; and every file-writing
destination. Static checks are one part of the review, not proof of all behavior.

## Issues addressed

- A partial export after renaming the default look discarded other committed
  layers from its new descriptor. Reproduced with actual USDZ exports before
  the fix; current entries now transfer by stable look ID. Removed-look
  descriptions are retired, while old USDZ packages remain untouched. Malformed
  unrelated metadata is ignored; incompatible current metadata is reported.
- A variant manifest could advertise an old package after its current bake
  became unavailable/incompatible. It now requires Ready default-look results
  and writes atomically. Final metadata failures reach the export summary.
- Optimize Sync Names could destroy RK shader identity by renaming its material
  to the object's name. RK names are preserved; Runtime names are exempt from
  the older PascalCase/object-mesh-material matching conventions.
- New-project Lightmap and Optimize externalized-texture paths were still
  outside PMVR. They now default to PMVR/Lightmaps and PMVR/Textures. Existing
  implicit paths are pinned before the RNA default changes; custom settings
  remain authoritative. Working-directory format 2 is an independent path
  migration version, NOT generated SCHEMA_VERSION or export-description schema.
- Source-root, bake/output/probe configuration is disabled during operations;
  the log folder can still be opened. This prevents changing job destinations
  or project ownership while a modal queue/render is running.

## Existing-project organization

Project Settings has one maintenance action, Organize Local Files. Opening a
project never performs this file operation. Recognized project-local folders:

| Previous folder | Organized folder |
| --- | --- |
| Beauty_Bakes | PMVR/Bakes |
| PMVR_Flattened | PMVR/Flattened |
| PMVR_Logs | PMVR/Logs |
| Lightmaps | PMVR/Lightmaps |
| PM_Selected_Textures | PMVR/Textures |

The action serializes the live pre-migration scene, including unsaved edits,
then copies and SHA256-verifies files. It remaps editable images, variant paths,
settings and supported external references; an unsupported remaining reference
blocks saving/archiving. Linked datablocks and linked/reparse folders are refused.
It saves the new blend before archiving old folders under
PMVR/Backups/BeforeOrganization_<time>_<id>. No bake, UV, geometry, naming or ID
conversion is involved. Existing target directories are never overwritten.

The journal records phases, complete file hashes and the backup blend hash.
A failed save restores old paths/files; an archive failure leaves a valid new
blend plus both verified copies. Rollback runs with the project closed, restores
the old folders and blend to its original location, and preserves organized
files and the newer blend. The backup is not opened directly from its subfolder
because its relative paths describe the original location. No automatic backup
deletion or unknown-folder cleanup is performed.

Sync, textures/assets, SimpleBake_Bakes, renders, old/manual backups and unknown
files are excluded. Owner-held previews/media/models in Sync remain untouched.
The owner should not have v2 and v3 editing/baking the same project during the
one-time production migration; promotion already requires saving/closing Blender.

## Validation and evidence

Final fixture suite and fresh real-UniPlace checks are recorded in
D:/Blender_Python_v3/_environment/release_acceptance_summary.json.
Detailed logs, static inventory and test results are alongside that summary.
The private snapshot is
C:/Users/papl/Desktop/PMVR_v3_test/ReleaseReview_2026-10-02;
Before and After contain actual full Day/Evening USDZ exports and fingerprints.
organization_review.json and the backup journal prove the file move and hashes.

No GPU bake is required for this follow-up. Entire bake_scene.py/constants.py
must still match tag v2.0.0; signature functions/inputs, source/unit IDs, legacy
DAY/EVENING storage and generated SCHEMA_VERSION=1 are protected. Final production
guard checks the original blend and normal user preferences against this task's
fresh starting hashes, not the older October 1 snapshot provenance.

PBR conversion and Runtime/content preparation remain the owner's next content
step: Ready/v3 does not prove diffuse-only atlases, and Blender tests cannot
replace Asset Manager/Vision Pro acceptance. No main merge, online publishing,
production export or normal Blender preference update occurs in this task.
