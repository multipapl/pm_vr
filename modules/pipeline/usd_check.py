"""Check a written USD file against what the Mac side reads from it."""

import os

from .constants import BAKE_UV_NAME, PRIMARY_UV_NAME, TAG_GENERATED


BAKED_UV_SETS = frozenset({"st", PRIMARY_UV_NAME})


def _uv_sets(mesh):
    from pxr import Sdf, UsdGeom

    kinds = (Sdf.ValueTypeNames.TexCoord2fArray, Sdf.ValueTypeNames.Float2Array)
    return {
        primvar.GetPrimvarName() for primvar in UsdGeom.PrimvarsAPI(mesh).GetPrimvars()
        if primvar.GetTypeName() in kinds
    }


def _material_reads(material, cache):
    """UV sets a material reads and its textures missing from the package."""
    from pxr import Usd, UsdShade

    key = material.GetPath()
    if key not in cache:
        reads, missing = set(), set()
        for prim in Usd.PrimRange(material.GetPrim()):
            shader = UsdShade.Shader(prim)
            if not shader:
                continue
            kind = shader.GetIdAttr().Get()
            if kind == "UsdPrimvarReader_float2":
                varname = shader.GetInput("varname")
                if varname and varname.Get():
                    reads.add(str(varname.Get()))
            elif kind == "UsdUVTexture":
                file_input = shader.GetInput("file")
                asset = file_input.Get() if file_input else None
                if asset and asset.path and not asset.resolvedPath:
                    missing.add(os.path.basename(asset.path))
        cache[key] = (reads, missing)
    return cache[key]


def check_usd(path):
    """Problems in a written USD file, one short sentence each.

    A baked mesh carries exactly the UV sets "st" (the atlas, SimpleBake in
    Blender) and "UVMap" (the authored layout); every material reads only UV
    sets its mesh has; every texture is inside the package."""
    from pxr import Usd, UsdGeom, UsdShade

    stage = Usd.Stage.Open(path)
    if not stage:
        return [f"{os.path.basename(path)} cannot be opened as USD"]
    problems = []
    cache = {}
    reported = set()
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        owner = prim.GetParent()
        uv_sets = _uv_sets(prim)
        tag = owner.GetAttribute(f"userProperties:{TAG_GENERATED}")
        if tag and tag.Get() and uv_sets != BAKED_UV_SETS:
            problems.append(
                f"{owner.GetName()}: UV sets {', '.join(sorted(uv_sets)) or 'none'} "
                f"instead of st ({BAKE_UV_NAME}) and {PRIMARY_UV_NAME}"
            )
        targets = [prim, *(subset.GetPrim() for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim)))]
        for target in targets:
            material, _relationship = UsdShade.MaterialBindingAPI(target).ComputeBoundMaterial()
            if not material:
                continue
            reads, missing = _material_reads(material, cache)
            absent = sorted(reads - uv_sets)
            if absent:
                problems.append(
                    f'{owner.GetName()}: material "{material.GetPrim().GetName()}" reads '
                    f'UV set {", ".join(absent)}, which the mesh does not have'
                )
            if missing and material.GetPath() not in reported:
                reported.add(material.GetPath())
                problems.append(
                    f'material "{material.GetPrim().GetName()}": texture '
                    f'{", ".join(sorted(missing))} is not in the file'
                )
    return list(dict.fromkeys(problems))
