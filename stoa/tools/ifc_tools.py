"""Deterministic answers from IFC (BIM) models.

Quantities come from the model's quantity sets when present (BaseQuantities,
Qto_*BaseQuantities) and from the tessellated geometry otherwise. Everything is
converted to metres, m² and m³.
"""

import functools
import re
import unicodedata

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.shape as us
import ifcopenshell.util.unit as uu

from stoa.answer import Answer, Evidence, ToolError

# Classes the LLM may ask for. Subtypes are included by by_type().
COUNTABLE = [
    "IfcDoor", "IfcWindow", "IfcWall", "IfcSlab", "IfcColumn", "IfcBeam",
    "IfcStair", "IfcRailing", "IfcRoof", "IfcSpace", "IfcCovering",
    "IfcFurnishingElement", "IfcSanitaryTerminal", "IfcFlowTerminal",
    "IfcBuildingElementProxy", "IfcOpeningElement", "IfcCurtainWall", "IfcPlate",
    "IfcMember", "IfcFooting", "IfcPile", "IfcRamp", "IfcLightFixture",
    "IfcAirTerminal",
]

QUANTITIES = {
    # name -> (quantity keys to try in order, kind)
    "net_floor_area": (["NetFloorArea", "GrossFloorArea", "NetFootprintArea"], "area"),
    "gross_floor_area": (["GrossFloorArea", "NetFloorArea", "GrossFootprintArea"], "area"),
    "net_volume": (["NetVolume", "GrossVolume"], "volume"),
    "gross_volume": (["GrossVolume", "NetVolume"], "volume"),
    "length": (["Length"], "length"),
    "net_side_area": (["NetSideArea", "GrossSideArea"], "area"),
    "gross_side_area": (["GrossSideArea", "NetSideArea"], "area"),
    "net_area": (["NetArea", "GrossArea", "NetFootprintArea"], "area"),
    "height": (["Height"], "length"),
}

IFC2X3_ALIASES = {
    "IfcSanitaryTerminal": ("IfcFlowTerminal", "IfcSanitaryTerminalType"),
    "IfcLightFixture": ("IfcFlowTerminal", "IfcLightFixtureType"),
    "IfcAirTerminal": ("IfcFlowTerminal", "IfcAirTerminalType"),
}

UNITS = {"area": "m2", "volume": "m3", "length": "m"}


@functools.lru_cache(maxsize=8)
def open_ifc(path: str) -> ifcopenshell.file:
    return ifcopenshell.open(path)


@functools.lru_cache(maxsize=8)
def _scales(path: str) -> dict[str, float]:
    f = open_ifc(path)
    return {
        "length": uu.calculate_unit_scale(f, "LENGTHUNIT"),
        "area": uu.calculate_unit_scale(f, "AREAUNIT"),
        "volume": uu.calculate_unit_scale(f, "VOLUMEUNIT"),
    }


_GEOM = ifcopenshell.geom.settings()


@functools.lru_cache(maxsize=4096)
def _shape(path: str, step_id: int):
    try:
        return ifcopenshell.geom.create_shape(_GEOM, open_ifc(path).by_id(step_id))
    except Exception:
        return None


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = re.sub(r"[\"'„“”«»]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _storey_of(e):
    """Walk containment and aggregation up to the building storey."""
    seen = 0
    while e is not None and seen < 10:
        if e.is_a("IfcBuildingStorey"):
            return e
        e = ue.get_container(e) or ue.get_aggregate(e)
        seen += 1
    return None


def _find_storey(f, name: str):
    storeys = f.by_type("IfcBuildingStorey")
    want = _norm(name)
    for s in storeys:
        if _norm(s.Name) == want or _norm(s.LongName) == want:
            return s
    for s in storeys:
        if want and (want in _norm(s.Name) or _norm(s.Name) in want):
            return s
    raise ToolError(
        f"No storey named {name!r}. Storeys: {[s.Name for s in storeys]}"
    )


def _elements(path: str, ifc_class: str, storey: str | None):
    f = open_ifc(path)
    type_filter = None
    if ifc_class in IFC2X3_ALIASES and f.schema == "IFC2X3":
        # IFC2X3 has no typed MEP terminals; the type object carries the meaning.
        ifc_class, type_filter = IFC2X3_ALIASES[ifc_class]
    try:
        elems = f.by_type(ifc_class)
    except RuntimeError:
        raise ToolError(f"Unknown IFC class {ifc_class!r}. Try one of {COUNTABLE}")
    if type_filter:
        elems = [e for e in elems if (t := ue.get_type(e)) and t.is_a(type_filter)]
    # Count assemblies, not their parts (a stair aggregates its flights and risers).
    elems = [
        e for e in elems
        if not ((agg := ue.get_aggregate(e)) and agg.is_a(e.is_a()))
    ]
    if storey:
        target = _find_storey(f, storey)
        elems = [e for e in elems if _storey_of(e) == target]
    return elems


def _label(e) -> str:
    parts = [p for p in (getattr(e, "LongName", None), e.Name) if p]
    return " ".join(parts) or e.is_a()


def _quantity(path: str, e, quantity: str) -> tuple[float | None, str]:
    keys, kind = QUANTITIES[quantity]
    qtos = ue.get_psets(e, qtos_only=True)
    scale = _scales(path)[kind]
    for qset_name, qset in qtos.items():
        if not qset_name or "Quantities" not in qset_name:
            continue
        for k in keys:
            v = qset.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v) * scale, f"{qset_name}.{k}"
    # Fall back to geometry (already in SI units).
    sh = _shape(path, e.id())
    if sh is None:
        return None, "no quantity or geometry"
    g = sh.geometry
    if kind == "volume":
        return us.get_volume(g), "geometry volume"
    if quantity in ("net_floor_area", "gross_floor_area", "net_area"):
        return us.get_footprint_area(g), "geometry footprint area"
    if quantity in ("net_side_area", "gross_side_area"):
        return us.get_side_area(g), "geometry side area"
    if quantity == "length":
        x, y = us.get_x(g), us.get_y(g)
        return max(x, y), "geometry bounding-box length"
    if quantity == "height":
        return us.get_z(g), "geometry height"
    return None, "unsupported"


# ---- tools exposed to the LLM -------------------------------------------------


def ifc_count(path: str, ifc_class: str, storey: str | None = None) -> Answer:
    elems = _elements(path, ifc_class, storey)
    return Answer(
        value=len(elems),
        unit="count",
        method=f"count of {ifc_class}" + (f" on storey {storey!r}" if storey else ""),
        evidence=[Evidence(ref=e.GlobalId, label=_label(e)) for e in elems],
    )


def ifc_sum_quantity(
    path: str, ifc_class: str, quantity: str, storey: str | None = None
) -> Answer:
    if quantity not in QUANTITIES:
        raise ToolError(f"Unknown quantity {quantity!r}. Use one of {list(QUANTITIES)}")
    elems = _elements(path, ifc_class, storey)
    if not elems:
        raise ToolError(f"No {ifc_class} found" + (f" on {storey!r}" if storey else ""))
    total, ev, methods = 0.0, [], set()
    for e in elems:
        v, how = _quantity(path, e, quantity)
        methods.add(how)
        if v is not None:
            total += v
            ev.append(Evidence(ref=e.GlobalId, label=_label(e), value=round(v, 4)))
    kind = QUANTITIES[quantity][1]
    return Answer(
        value=round(total, 3),
        unit=UNITS[kind],
        method=f"sum of {quantity} over {len(ev)} {ifc_class}: " + ", ".join(sorted(methods)),
        evidence=ev,
    )


def ifc_space_area(path: str, space_name: str, storey: str | None = None) -> Answer:
    """Net floor area of one named space (room)."""
    spaces = _elements(path, "IfcSpace", storey)
    want = _norm(space_name)
    m = re.match(r"(.*)\((.+)\)\s*$", space_name)
    hits = []
    for s in spaces:
        names = {
            _norm(s.Name), _norm(s.LongName),
            _norm(f"{s.LongName} ({s.Name})"), _norm(f"{s.Name} ({s.LongName})"),
        }
        if want in names:
            hits.append(s)
        elif m and _norm(s.Name) == _norm(m[2]) and _norm(s.LongName) == _norm(m[1]):
            hits.append(s)
    if not hits:
        sample = [_label(s) for s in spaces[:30]]
        raise ToolError(f"No space matching {space_name!r}. Some spaces: {sample}")
    # Several hits can happen when only the long name matched; report all.
    total, ev, methods = 0.0, [], set()
    for s in hits:
        v, how = _quantity(path, s, "net_floor_area")
        methods.add(how)
        total += v or 0
        ev.append(Evidence(ref=s.GlobalId, label=_label(s), value=round(v or 0, 4)))
    return Answer(
        value=round(total, 3),
        unit="m2",
        method=", ".join(sorted(methods)),
        evidence=ev,
        note="" if len(hits) == 1 else f"{len(hits)} spaces matched; summed",
    )


def ifc_model_facts(path: str) -> Answer:
    """Overview of the model for open-ended questions: storeys, element counts, spaces."""
    f = open_ifc(path)
    proj = f.by_type("IfcProject")
    counts = {}
    for c in COUNTABLE:
        try:
            n = len(f.by_type(c))
        except RuntimeError:
            continue
        if n:
            counts[c] = n
    storeys = []
    for s in f.by_type("IfcBuildingStorey"):
        storeys.append({"name": s.Name, "elevation": s.Elevation})
    spaces = [_label(s) for s in f.by_type("IfcSpace")[:80]]
    facts = {
        "project": [proj[0].Name, proj[0].LongName] if proj else None,
        "schema": f.schema,
        "storeys": storeys,
        "element_counts": counts,
        "spaces_sample": spaces,
    }
    return Answer(value=facts, method="IFC model overview", deterministic=True)
