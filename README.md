# PM VR

A collection of internal tools for working on VR projects in Blender.

The addon currently includes VR project preparation tools, a lightmap baking workflow,
and persistent collection-based batch export to USDZ and GLB.

## Baked PBR material preparation

Select one or more meshes named `Name_Baked`, then run **Prepare Baked Material
Pairs** in the VR Project section. PM VR finds each `Name` object globally,
including in hidden collections, and creates `Name_M` in the Scene Collection.
It restores `UVMap` and `SimpleBake` from the original mesh and builds a material
that uses the baked Base Color on `SimpleBake` while the original supporting
textures use `UVMap`. The original Base Color branch is discarded. Objects whose
names contain `Leaf` or `Alpha` use a restricted mode that retains only the
original Alpha branch alongside the baked Base Color.
Disconnected Base Color, normal, mix, mapping, and other intermediate nodes are
pruned from the generated material. Source objects, meshes, UVs, materials,
node graphs, transforms, collection membership, and selection remain unchanged.

Running the operation again safely replaces only a previous `Name_M` generated
by PM VR. Untagged name collisions, incompatible topology, ambiguous materials,
and missing textures are skipped and reported without overwriting user data.

## Collection batch export

Select collections in Blender's Outliner and use **Add to PM VR Export List** from
the collection context menu. Each row is stored in the `.blend` file. Its output
name starts as the collection name and can be edited permanently in the list.

USDZ and GLB use separate export directories and independent checkboxes. Use the
format buttons to create one file per checked collection. USDZ uses the PM VR
Apple Vision Pro preset; GLB uses WebP textures at quality 75 and Draco mesh
compression with the project preset.

Interactive exports show the current collection, format, completed job count,
and a progress indicator. A persistent summary is shown when the batch finishes.

## Installation

Install or link this directory as the `PM_VR` Blender addon, then enable **PM VR** in Blender preferences. The tools appear in **View3D > N-Panel > PM VR**.
