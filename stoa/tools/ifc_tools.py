"""Deterministic answers from IFC (BIM) models.

Quantities come from the model's quantity sets when present (BaseQuantities,
Qto_*BaseQuantities) and from the tessellated geometry otherwise. Everything is
converted to metres, m² and m³.
"""

import functools
import json
import re
import threading
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


# ifcopenshell isn't safe to open files or tessellate from several threads at once.
_LOCK = threading.RLock()


@functools.lru_cache(maxsize=8)
def _open(path: str) -> ifcopenshell.file:
    return ifcopenshell.open(path)


def open_ifc(path: str) -> ifcopenshell.file:
    with _LOCK:
        return _open(path)


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
    with _LOCK:
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
        # Some exporters leave the quantity set unnamed (hitos); it still holds BaseQuantities.
        if qset_name and "Quantities" not in qset_name:
            continue
        for k in keys:
            v = qset.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v) * scale, f"{qset_name}.{k}"
    # Fall back to geometry (already in SI units).
    sh = _shape(path, e.id())
    if sh is None:
        return None, "no quantity or geometry"
    try:
        return _geometry_quantity(sh.geometry, quantity, kind)
    except ValueError:  # empty tessellation
        return None, "empty geometry"


def _geometry_quantity(g, quantity: str, kind: str) -> tuple[float | None, str]:
    if kind == "volume":
        return us.get_volume(g), "geometry volume"
    if quantity in ("net_floor_area", "gross_floor_area", "net_area"):
        return us.get_footprint_area(g), "geometry footprint area"
    if quantity in ("net_side_area", "gross_side_area"):
        return us.get_side_area(g), "geometry side area"
    if quantity == "length":
        return max(us.get_x(g), us.get_y(g)), "geometry bounding-box length"
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
    length = _scales(path)["length"]
    storeys = []
    for s in f.by_type("IfcBuildingStorey"):
        elev = round(s.Elevation * length, 3) if s.Elevation is not None else None
        storeys.append({"name": s.Name, "elevation_m": elev})
    spaces = [_label(s) for s in f.by_type("IfcSpace")[:80]]
    facts = {
        "project": [proj[0].Name, proj[0].LongName] if proj else None,
        "schema": f.schema,
        "storeys": storeys,
        "element_counts": counts,
        "spaces_sample": spaces,
        "sites": [{"name": s.Name, "latitude_dms": s.RefLatitude,
                   "longitude_dms": s.RefLongitude, "elevation": s.RefElevation}
                  for s in f.by_type("IfcSite")],
        "buildings": [{"name": b.Name, "long_name": b.LongName,
                       "properties": ue.get_psets(b, psets_only=True)}
                      for b in f.by_type("IfcBuilding")],
    }
    return Answer(value=facts, method="IFC model overview", deterministic=True)


def storey_names(path: str) -> list[str]:
    """Storeys bottom to top, for the router prompt, so it can name the storey a
    question means ("first floor" -> "1. Obergeschoss") without a wasted turn."""
    f = open_ifc(path)
    length = _scales(path)["length"]
    storeys = sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0)
    return [
        f"{s.Name} ({s.Elevation * length:+.2f} m)" if s.Elevation is not None else s.Name
        for s in storeys
    ]


# Numbered copies of one function share a group: "Buero MuellerIII", "Buero Mueller
# VI + VII", "Labor K1" -> "Buero Mueller", "Labor". Qualifiers in brackets stay:
# "PC-areal (indre/ytre sone)" is its own group.
_NUMBER_SUFFIX = re.compile(r"(?<=[a-zà-ÿ\s])(?:[\s+,&]*(?:[IVX]+|[A-Z]?\d+[a-z]?)(?![\w.]))+[\s+*]*$")


def _function(space) -> str:
    name = (space.LongName or space.Name or "").strip()
    base = _NUMBER_SUFFIX.sub("", name).strip()
    return base or name or "(unnamed)"


def _storey_breakdown(path: str, storey, spaces: list, group: bool) -> tuple[list[str], list[Evidence], float]:
    rows = []
    for s in spaces:
        v, _ = _quantity(path, s, "net_floor_area")
        rows.append((s, v or 0.0))
    total = sum(v for _, v in rows)
    title = storey.Name + (f" ({storey.LongName})" if getattr(storey, "LongName", None) else "")
    lines = [f"{title}: {len(rows)} spaces", f"Total area: {total:.1f} m2"]
    if group:
        groups: dict[str, list] = {}
        for s, v in rows:
            groups.setdefault(_function(s), []).append((s, v))
        lines.append("Spaces by function (IfcSpace.LongName):")
        for fn in sorted(groups, key=_norm):
            members = groups[fn]
            area = sum(v for _, v in members)
            n = len(members)
            lines.append(f"  {fn} ({n} space{'s' if n > 1 else ''}, {area:.1f} m2):")
            lines += [f"    - {s.Name}: {v:.1f} m2" for s, v in members]
    else:
        lines.append("Spaces:")
        lines += [
            f"  - {s.Name}: {s.LongName or ''} ({v:.1f} m2)"
            for s, v in sorted(rows, key=lambda r: -r[1])
        ]
    evidence = [Evidence(ref=s.GlobalId, label=_label(s), value=round(v, 2)) for s, v in rows]
    return lines, evidence, total


def ifc_space_breakdown(path: str, storey: str | None = None, group_by_function: bool = True) -> Answer:
    """Spaces of one storey (or every storey) with their net floor areas, grouped by
    function (the space's long name without a trailing roman numeral) or listed."""
    f = open_ifc(path)
    if storey:
        targets = [_find_storey(f, storey)]
    else:
        targets = sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0)
    by_storey: dict = {t: [] for t in targets}
    for s in _elements(path, "IfcSpace", None):
        st = _storey_of(s)
        if st in by_storey:
            by_storey[st].append(s)
    lines, evidence, total, n = [], [], 0.0, 0
    for st, spaces in by_storey.items():
        if not spaces:
            continue
        block, ev, t = _storey_breakdown(path, st, spaces, group_by_function)
        lines += block + [""]
        evidence += ev
        total += t
        n += len(spaces)
    if not n:
        raise ToolError("No IfcSpace found" + (f" on {storey!r}" if storey else ""))
    if not storey:
        lines.insert(0, f"Building: {n} spaces, total area {total:.1f} m2\n")
    # Summarize across the whole requested scope, not separately per floor.
    functions = {}
    for ev_space in (s for spaces in by_storey.values() for s in spaces):
        fn = _function(ev_space)
        area = _quantity(path, ev_space, "net_floor_area")[0]
        entry = functions.setdefault(fn, [0, 0.0])
        entry[0] += 1
        entry[1] += area or 0.0
    overview = [f"Function totals across requested scope: {len(functions)} functions; percentages are of total area."]
    for fn, (count, area) in sorted(functions.items(), key=lambda item: -item[1][1]):
        overview.append(f"- {fn}: {count} spaces, {area:.1f} m2, "
                        f"{100*area/total if total else 0:.1f}%; average {area/count:.1f} m2")
    lines = overview + [""] + lines
    return Answer(
        value="\n".join(lines).strip(),
        unit=None,
        method="IfcSpace net floor area per space, grouped by storey"
        + (" and function (LongName)" if group_by_function else ""),
        evidence=evidence,
    )


def _materials(e) -> list[str]:
    mats = ue.get_materials(e) or []
    if not mats and (t := ue.get_type(e)):
        mats = ue.get_materials(t) or []
    return sorted({m.Name for m in mats if getattr(m, "Name", None)})


def _common_props(e) -> dict:
    """Pset_*Common properties (IsExternal, FireRating, LoadBearing, ...) from the
    element and its type; the element's own values win."""
    out = {}
    for src in (ue.get_type(e), e):
        if src is None:
            continue
        for name, props in ue.get_psets(src, psets_only=True).items():
            if name.startswith("Pset_") and name.endswith("Common"):
                out.update({k: v for k, v in props.items() if k != "id" and v not in (None, "")})
    return out


def _report_name(e) -> str:
    """Keep architectural names as well as IFC type names; strip instance ids only."""
    return re.sub(r":\d+$", "", e.Name or "") or _label(e)


def _material_layers(path: str, e) -> list[dict]:
    material = ue.get_material(e, should_skip_usage=True)
    if material is None or not material.is_a("IfcMaterialLayerSet"):
        return []
    scale = _scales(path)["length"] * 1000
    return [{"material": layer.Material.Name if layer.Material else None,
             "thickness_mm": round(layer.LayerThickness * scale, 3)}
            for layer in material.MaterialLayers]


def _external_classification(e) -> tuple[bool | None, str]:
    explicit = _common_props(e).get("IsExternal")
    if isinstance(explicit, bool):
        return explicit, "Pset_*Common.IsExternal"
    # ArchiCAD can export the classification as a named layer instead of the
    # standard property. Preserve its provenance; never infer from thickness.
    layer = ue.get_psets(e, psets_only=True).get("ArchiCADProperties", {}).get("Ebene", "")
    labels = {"außenwände": True, "aussenwände": True, "exterior walls": True,
              "innenwände": False, "interior walls": False}
    return labels.get(_norm(layer)), f"ArchiCADProperties.Ebene={layer}" if layer else "unavailable"


def ifc_list_elements(path: str, ifc_class: str, storey: str | None = None,
                      is_external: bool | None = None, global_id: str | None = None) -> Answer:
    """Elements of one class grouped by identical type, storey, materials, properties
    and size, with counts and summed quantities. Grouping keeps the table small
    enough to send to the LLM cheaply."""
    elems = _elements(path, ifc_class, storey)
    if global_id:
        elems = [e for e in elems if e.GlobalId == global_id]
        if not elems:
            raise ToolError(f"No {ifc_class} with GlobalId {global_id!r} in this scope")
    classifications = {e.id(): _external_classification(e) for e in elems}
    unknown_external = sum(value is None for value, _ in classifications.values())
    if is_external is not None:
        elems = [e for e in elems if classifications[e.id()][0] == is_external]
    length = _scales(path)["length"]
    groups: dict[str, dict] = {}
    evidence = []
    by_name, by_type, material_counts, sizes = {}, {}, {}, {}
    for e in elems:
        row = {"name": _report_name(e)}
        if getattr(e, "LongName", None):
            row["long_name"] = e.LongName
        if t := ue.get_type(e):
            row["type"] = t.Name
        if s := _storey_of(e):
            row["storey"] = s.Name
        for attr in ("OverallWidth", "OverallHeight"):
            if (v := getattr(e, attr, None)) is not None:
                row[attr] = round(v * length, 3)
        if mats := _materials(e):
            row["materials"] = mats
        if props := _common_props(e):
            row["props"] = props
        classification, classification_source = classifications[e.id()]
        if classification is not None:
            row.setdefault("props", {})["IsExternal"] = classification
            row["external_classification_source"] = classification_source
        if layers := _material_layers(path, e):
            row["material_layers"] = layers
            row["layer_total_thickness_mm"] = round(sum(x["thickness_mm"] for x in layers), 3)
        if global_id:
            row["global_id"] = e.GlobalId
            row["all_properties"] = ue.get_psets(e, psets_only=True)
        by_name[row["name"]] = by_name.get(row["name"], 0) + 1
        type_name = row.get("type", row["name"])
        by_type[type_name] = by_type.get(type_name, 0) + 1
        for material in row.get("materials", []):
            material_counts[material] = material_counts.get(material, 0) + 1
        for attr in ("OverallWidth", "OverallHeight"):
            if attr in row:
                sizes.setdefault(attr, []).append((row[attr], row["name"]))
        key = json.dumps(row, sort_keys=True, default=str)
        g = groups.setdefault(key, {**row, "count": 0})
        g["count"] += 1
        main = None  # the element's own first quantity (a room's area), for per-element questions
        for q in _row_quantities(ifc_class):
            if qv := _quantity(path, e, q)[0]:
                g[f"total_{q}"] = round(g.get(f"total_{q}", 0) + qv, 3)
                main = round(qv, 2) if main is None else main
        evidence.append(Evidence(ref=e.GlobalId, label=_label(e), value=main))
    rows = sorted(groups.values(), key=lambda g: -g["count"])
    size_stats = {}
    for attr, values in sizes.items():
        unique = sorted(set(values), reverse=True)
        size_stats[attr] = {"unit": "m", "count": len(values),
            "min": min(v for v, _ in values), "max": max(v for v, _ in values),
            "instance_weighted_mean": round(sum(v for v, _ in values)/len(values), 6),
            "distinct_name_size_mean": round(sum(v for v, _ in unique)/len(unique), 6),
            "largest_distinct_name_sizes": [{"value": v, "name": name} for v, name in unique[:10]]}
    summary = {"elements": len(elems), "groups": len(groups),
        "filter": {"storey": storey, "is_external": is_external, "global_id": global_id},
        "unknown_external_classification_before_filter": unknown_external,
        "by_name": [{"name": name, "count": count, "percent": round(count/len(elems)*100, 2)}
                    for name, count in sorted(by_name.items(), key=lambda item: -item[1])],
        "by_type": [{"type": name, "count": count, "percent": round(count/len(elems)*100, 2)}
                    for name, count in sorted(by_type.items(), key=lambda item: -item[1])],
        "material_counts": material_counts, "size_statistics": size_stats,
        "rows": rows[:120], "omitted_groups": max(0, len(rows)-120)}
    return Answer(
        value=summary,
        method=f"IFC element table for {ifc_class}",
        evidence=evidence,
    )


def _row_quantities(ifc_class: str) -> tuple[str, ...]:
    if ifc_class == "IfcSpace":
        return ("net_floor_area", "net_volume")
    if ifc_class in ("IfcWall", "IfcBeam", "IfcColumn", "IfcMember", "IfcRailing"):
        return ("length", "net_volume")
    return ("net_volume",)


# ---- grouped quantities ("slab area by type", "wall types with thickness") -----

GROUP_KEYS = ["type", "name", "thickness", "material", "storey", "predefined_type", "is_external"]

# Classes whose thickness is meaningful (layered build-ups).
_LAYERED = ("IfcSlab", "IfcWall", "IfcCovering", "IfcRoof", "IfcPlate", "IfcCurtainWall")
_THICKNESS_PROPS = re.compile(r"^(thickness|default thickness|dicke|standarddicke|gesamtdicke|width)$", re.I)
_REBAR = re.compile(r"reinforc|rebar|bewehr|armier|armatur", re.I)


def _thickness(path: str, e) -> tuple[float | None, str]:
    """Thickness in metres: material layers, then quantity sets, then properties."""
    length = _scales(path)["length"]
    mat = ue.get_material(e, should_inherit=True)
    if mat is not None and mat.is_a("IfcMaterialLayerSetUsage"):
        mat = mat.ForLayerSet
    if mat is not None and mat.is_a("IfcMaterialLayerSet"):
        t = sum(l.LayerThickness or 0 for l in mat.MaterialLayers)
        if t > 0:
            return t * length, "material layer set"
    for src in (e, ue.get_type(e)):
        if src is None:
            continue
        for qset_name, qset in ue.get_psets(src, qtos_only=True).items():
            if qset_name and "Quantities" not in qset_name:
                continue
            v = qset.get("Width")
            if isinstance(v, (int, float)) and v > 0:
                return v * length, f"{qset_name}.Width"
        for pset_name, props in ue.get_psets(src, psets_only=True).items():
            for k, v in props.items():
                if _THICKNESS_PROPS.match(k) and isinstance(v, (int, float)) and v > 0:
                    return v * length, f"{pset_name}.{k}"
    # Last resort: volume over the face area (footprint for slabs, side for walls),
    # rounded to 5 mm since openings and edge cuts skew it slightly.
    sh = _shape(path, e.id())
    if sh is not None and e.is_a() != "IfcCurtainWall":
        try:
            g = sh.geometry
            face = us.get_side_area(g) if e.is_a("IfcWall") else us.get_footprint_area(g)
            if face > 0:
                return round(us.get_volume(g) / face / 0.005) * 0.005 or None, "geometry volume/area (≈)"
        except ValueError:
            pass
    return None, "no thickness"


def _reinforcement(e) -> dict:
    """Rebar properties (bar diameter, spacing, ...) from the element and its type.
    'Reference' and 'id' are skipped: exporters fill Reference with the type name."""
    out = {}
    for src in (ue.get_type(e), e):
        if src is None:
            continue
        for pset_name, props in ue.get_psets(src, psets_only=True).items():
            for k, v in props.items():
                if k in ("id", "Reference") or v in (None, ""):
                    continue
                if _REBAR.search(pset_name or "") or _REBAR.search(k):
                    out[f"{pset_name}.{k}"] = v
    return out


_EXT = re.compile(r"au(ss|ß)en|exterior|external|outer|ulko", re.I)
_INT = re.compile(r"innen|interior|internal|inner|sisä|partition", re.I)


def _is_external(e) -> bool | None:
    """Pset_*Common.IsExternal; else the CAD layer name ('Außenwände', 'A-WALL-EXTR')."""
    v = _common_props(e).get("IsExternal")
    if isinstance(v, bool):
        return v
    layers = [l.Name or "" for l in getattr(e, "LayerAssignments", None) or ()]
    if not layers:
        for rep in getattr(getattr(e, "Representation", None), "Representations", None) or ():
            layers += [l.Name or "" for l in getattr(rep, "LayerAssignments", None) or ()]
    for props in ue.get_psets(e, psets_only=True).values():
        for k in ("Ebene", "Layer"):
            if isinstance(props.get(k), str):
                layers.append(props[k])
    for name in layers:
        if _EXT.search(name):
            return True
        if _INT.search(name):
            return False
    return None


def _group_value(path: str, e, key: str) -> str:
    if key == "type":
        t = ue.get_type(e)
        return (t.Name if t and t.Name else None) or e.Name or "(untyped)"
    if key == "name":
        return e.Name or "(unnamed)"
    if key == "thickness":
        t, how = _thickness(path, e)
        return f"{'≈' if '≈' in how else ''}{round(t * 1000)} mm" if t else "(unknown thickness)"
    if key == "material":
        return ", ".join(_materials(e)) or "(no material)"
    if key == "storey":
        s = _storey_of(e)
        return s.Name if s else "(no storey)"
    if key == "predefined_type":
        v = getattr(e, "PredefinedType", None)
        if v in (None, "NOTDEFINED", "USERDEFINED") and (t := ue.get_type(e)):
            v = getattr(t, "PredefinedType", None) or v
        return v or "NOTDEFINED"
    if key == "is_external":
        return {True: "external", False: "internal"}.get(_is_external(e), "(IsExternal not set)")
    raise ToolError(f"Unknown group_by {key!r}. Use {GROUP_KEYS}")


def _mm_range(ts: list[float]) -> str:
    mm = sorted({round(t * 1000) for t in ts})
    if not mm:
        return ""
    return f"{mm[0]} mm" if len(mm) == 1 else f"{mm[0]}–{mm[-1]} mm (mixed)"


def ifc_group_quantity(
    path: str,
    ifc_class: str,
    group_by: list[str] | str = "type",
    quantity: str | None = None,
    storey: str | None = None,
    predefined_type: str | None = None,
    is_external: bool | None = None,
    only: str | None = None,
) -> Answer:
    """Elements of one class grouped by type, thickness, material, storey, ... with
    count, share, thickness and an optional summed quantity per group. With `only`,
    returns the number (quantity total, else count) for the matching group(s)."""
    keys = [group_by] if isinstance(group_by, str) else list(group_by or ["type"])
    if quantity and quantity not in QUANTITIES:
        raise ToolError(f"Unknown quantity {quantity!r}. Use one of {list(QUANTITIES)}")
    elems = _elements(path, ifc_class, storey)
    if predefined_type:
        want = predefined_type.upper()
        elems = [e for e in elems if _group_value(path, e, "predefined_type") == want]
    if is_external is not None:
        elems = [e for e in elems if _is_external(e) is is_external]
    if not elems:
        raise ToolError(f"No {ifc_class} matched the filters")

    show_thickness = ifc_class in _LAYERED or "thickness" in keys
    length = _scales(path)["length"]
    groups: dict[str, dict] = {}
    rebar_seen = {}
    for e in elems:
        g = groups.setdefault(
            " | ".join(_group_value(path, e, k) for k in keys),
            {"elems": [], "qty": 0.0, "thick": [], "methods": set()},
        )
        g["elems"].append(e)
        if show_thickness:
            t, how = _thickness(path, e)
            if t:
                g["thick"].append(t)
                g["methods"].add(f"thickness from {how}")
        if ifc_class in ("IfcDoor", "IfcWindow") and e.OverallWidth and e.OverallHeight:
            g.setdefault("sizes", set()).add(
                f"{round(e.OverallWidth * length * 1000)}×{round(e.OverallHeight * length * 1000)} mm")
        if quantity:
            v, how = _quantity(path, e, quantity)
            g["methods"].add(f"{quantity} from {how}")
            g.setdefault("per", {})[e.id()] = v
            g["qty"] += v or 0
        if r := _reinforcement(e):
            rebar_seen[e.GlobalId] = r

    kind = QUANTITIES[quantity][1] if quantity else None
    unit = UNITS[kind] if kind else "count"
    if only:
        want = _norm(only)
        hit = [k for k in groups if _norm(k) == want] or [k for k in groups if want in _norm(k)]
        if not hit:
            raise ToolError(f"No group matching {only!r}. Groups: {list(groups)}")
        groups = {k: groups[k] for k in hit}

    n = sum(len(g["elems"]) for g in groups.values())
    total = sum(g["qty"] for g in groups.values())
    rows = sorted(groups.items(), key=lambda kv: (-(kv[1]["qty"] if quantity else 0), -len(kv[1]["elems"]), kv[0]))
    lines = [f"{ifc_class}: {n} elements in {len(rows)} group(s) by {' + '.join(keys)}"
             + (f", total {quantity} {total:.2f} {unit}" if quantity else "")]
    evidence = []
    for key, g in rows:
        c = len(g["elems"])
        line = f"- {key}: {c} ({100 * c / n:.1f}%)"
        if show_thickness and "thickness" not in keys and (tr := _mm_range(g["thick"])):
            line += f", thickness {tr}"
        if sizes := sorted(g.get("sizes", ())):
            line += ", size (W×H) " + ", ".join(sizes[:4]) + (" …" if len(sizes) > 4 else "")
        if quantity:
            share = f" ({100 * g['qty'] / total:.1f}%)" if total else ""
            line += f", {quantity} {g['qty']:.2f} {unit}{share}"
        lines.append(line)
        for e in g["elems"]:
            v = g.get("per", {}).get(e.id())
            evidence.append(Evidence(ref=e.GlobalId, label=f"{key}: {_label(e)}",
                                     value=round(v, 3) if v is not None else None))

    if ifc_class == "IfcSlab" or rebar_seen:
        if rebar_seen:
            distinct: dict[str, int] = {}
            for r in rebar_seen.values():
                k = json.dumps(r, sort_keys=True, default=str, ensure_ascii=False)
                distinct[k] = distinct.get(k, 0) + 1
            lines.append(f"Reinforcement properties on {len(rebar_seen)} element(s):")
            lines += [f"  {c} element(s): {k}" for k, c in sorted(distinct.items(), key=lambda kv: -kv[1])[:5]]
        else:
            try:
                bars = len(open_ifc(path).by_type("IfcReinforcingBar"))
            except RuntimeError:
                bars = 0
            lines.append(f"Reinforcement: no bar data on these elements; {bars} IfcReinforcingBar in the model.")

    method = f"IFC {ifc_class} grouped by {', '.join(keys)}"
    if sources := sorted(set().union(*(g["methods"] for g in groups.values()))):
        method += "; " + ", ".join(sources)
    if only:
        return Answer(value=round(total, 3) if quantity else n, unit=unit,
                      method=method + f"; group {only!r}", evidence=evidence,
                      note="\n".join(lines))
    return Answer(value="\n".join(lines), unit=None, method=method, evidence=evidence)
