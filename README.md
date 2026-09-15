# PM VR

A collection of internal tools for working on VR projects in Blender.

The addon currently includes VR project preparation tools, a lightmap baking workflow,
and persistent collection-based batch export to USDZ and GLB.

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
