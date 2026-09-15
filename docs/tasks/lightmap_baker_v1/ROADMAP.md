# PM Baker Roadmap

Status: planning document. This file describes the intended direction after
the working Lightmap Baker v1 prototype. It is not a committed release
schedule.

Related baseline: [TASK.md](TASK.md)

## Product direction

Build a PM-specific baking tool inspired by the useful parts of SimpleBake,
but shaped around the Blender-to-USD/RealityKit pipeline.

The intended end state is a reliable, nearly automatic workflow that:

- prepares and validates a batch of objects;
- chooses appropriate bake settings where they can be derived safely;
- produces only the texture signals required by the project;
- creates pipeline-ready object and material copies;
- exports textures with an explicit color-management contract;
- prepares the result for USD/USDZ delivery;
- preserves transactional replacement and leaves source assets untouched.

SimpleBake should be treated as a reference and feature source, not copied
wholesale. Each imported feature must justify its place in the PM pipeline.

## Planned bake modes

The baker should evolve from a lightmap-only tool into one shared batch system
with three top-level bake modes.

### Lightmap

The current implementation is the baseline:

- classic non-directional diffuse lighting;
- direct and indirect light;
- shadows, World and emissive contribution;
- colored lighting and color bleeding;
- no receiver Base Color, reflection, metallic response, or normal-map detail.

Much later, Lightmap may gain two explicit variants:

- `Full`: direct and indirect diffuse lighting, matching the current output;
- `Indirect`: indirect diffuse lighting only, intended for workflows where
  direct lighting remains dynamic.

This split is not a near-term priority. Its runtime material contract must be
defined before implementation.

### AO

Add a dedicated ambient-occlusion output rather than folding AO into the
Lightmap result.

Before implementation, define:

- whether the output is a scalar factor or RGB;
- whether distance and sampling come only from the active scene or need a
  small mode-specific control;
- whether and how it is connected to the generated material;
- its denoise and color-space requirements;
- how it should be exported and consumed in USD/RealityKit.

### Beauty

Add a Beauty mode for cases that require the rendered surface appearance
rather than a separable lighting signal.

Before implementation, define its exact contribution contract, especially:

- Base Color;
- direct and indirect light;
- emission;
- reflection/specular response;
- transmission and alpha;
- material normal/bump detail;
- view-dependent effects that cannot be represented reliably in a static
  texture.

Beauty should reuse the shared queue, state restoration, denoise, ownership,
replacement, logging, and export layers.

## Shared bake-mode architecture

Do not grow three separate bakers. Introduce a bake-mode abstraction that owns
only the behavior that differs:

- validation additions;
- temporary receiver/material setup;
- Cycles bake type and pass filter;
- denoise guides;
- output image semantics;
- generated material hookup;
- default export interpretation.

The batch runner, object queue, transactional replacement, progress overlay,
state restoration, naming, and asset ownership should remain shared.

## Export formats and color management

Add an export-format selector independent from the selected bake mode.

The export layer must make these choices explicit:

- file format;
- bit depth;
- channel layout;
- compression or quality;
- alpha handling;
- data/scene-linear/display-referred interpretation;
- Blender color-management transform applied during export.

The current 32-bit PIZ OpenEXR path remains the lossless scene-linear
reference. It must not receive a display transform.

Formats intended for display or compact delivery must use a deliberate Blender
color-management path. Saving an image must never accidentally bake or omit
AgX/Standard, Look, exposure, gamma, or another view transform. The UI should
make the selected transform contract understandable and reproducible.

Exact additional formats and their defaults remain to be selected after
pipeline tests.

## Resolution automation

Reuse the texel-density work already present in
`modules/12_vr_project_tools.py` instead of creating a second unrelated
calculation.

The existing logic already derives a suitable 1K/2K/4K class from:

- evaluated world-space mesh area;
- occupied area in the `SimpleBake` UV channel;
- a target texel density in pixels per centimeter.

The baker should eventually consume a shared version of this logic directly:

- offer automatic resolution per object;
- keep the current global and manual per-object override;
- show the derived resolution and reason before baking;
- clamp to supported power-of-two sizes;
- handle missing/invalid UV area as a logged validation result;
- avoid relying on object-name prefixes or suffixes as the data contract.

The texel-density calculation should be extracted into a reusable service
rather than duplicated between VR Project Tools and the baker.

## USD and USDZ handoff

Before adding automatic export, run a focused USD/RealityKit compatibility
spike.

It must determine:

- how Blender exports the `SimpleBake` UV set as a USD primvar;
- whether the generated Base Color multiply graph survives Blender USD export;
- whether RealityKit consumes that graph consistently on visionOS and iOS;
- what the web target can consume from the same asset;
- how the lightmap file is referenced and packaged inside USDZ;
- whether a Blender exporter extension or USD post-process is required;
- whether the lightmap should be represented as a standard material input,
  a custom material contract, or pipeline metadata;
- how rebaked texture names and paths remain stable across export.

The first USDZ implementation should include a small reference asset and a
visual comparison across Blender, visionOS, iOS, and the web target.

Native RealityKit lightmap support, including directional lightmaps, should be
tracked separately. The current texture-multiply workflow remains a useful
compatibility path and should not block later migration to a native solution.

## SimpleBake feature intake

Audit SimpleBake by workflow area and port features selectively. Likely areas
to evaluate include:

- bake-list ergonomics and per-object overrides;
- UV validation and optional preparation;
- output naming, folder, and format controls;
- presets;
- cancellation and long-batch resilience;
- material fallback handling;
- batch summaries and diagnostics.

Features that conflict with PM ownership rules, USD preparation, predictable
automation, or source preservation should be redesigned or omitted.

## Prototype hardening backlog

Near-term work before expanding the feature set:

- test representative production scenes and larger object batches;
- improve cancellation behavior around blocking Blender operations;
- restore Image Editor contents after temporary raw/albedo/normal targets;
- safely release or remap an old generated image retained by an Image Editor
  or another UI user during rebake;
- reduce forced-redraw noise in the console without losing live feedback;
- expand regression coverage for failure and visibility edge cases;
- verify supported behavior on the minimum declared Blender version.

## Suggested implementation order

1. Harden the current Lightmap prototype.
2. Extract a shared bake-mode interface without changing Lightmap output.
3. Add and validate AO.
4. Add and validate Beauty.
5. Add format selection and explicit color-managed export.
6. Extract and integrate automatic texel-density resolution.
7. Complete the USD/USDZ compatibility spike.
8. Implement the selected USDZ handoff.
9. Revisit Full versus Indirect Lightmap and native/directional lightmaps.

This order is directional, not a deadline. Production needs may reorder it.
