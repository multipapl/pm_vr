# Legacy compatibility

## Object-name texel-density labels

The old Optimize workflow that renamed selected mesh objects with `1K`, `2K`,
or `4K` prefixes/suffixes is superseded by bake-unit resolution setup and is no
longer shown in the PM VR interface.

The implementation remains registered for compatibility and emergency manual
use. Do not build new workflow features on it.

Retained operators:

- `pm_vr.add_texture_suffix`
- `pm_vr.remove_texture_suffix`

Retained legacy-only Scene property:

- `pm_vr_use_texture_prefix`

`pm_vr_target_td` is no longer legacy-only: bake-unit setup and the Texel
Density viewport diagnostic both use it as the project target. Its default is
5 px/cm.

Retained behavior includes SimpleBake UV-area measurement, texel-density
resolution selection, and prefix/suffix cleanup. These APIs may be removed only
after confirming that no saved files, scripts, or operators still call them.
