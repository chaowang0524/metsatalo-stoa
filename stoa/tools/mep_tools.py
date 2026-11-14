"""Deterministic answers about piping in IFC models: pipe length by system, size
and material; fittings, valves, fixtures and pumps per system.

IFC4 has typed classes (IfcPipeSegment, IfcValve, ...). IFC2X3 only has generic
flow classes (IfcFlowSegment, IfcFlowController, ...) whose type object says what
they are, so both are handled. The system an element belongs to comes from IfcSystem
assignments when the exporter wrote them, else from Revit's "Mechanical" property
set ("System Type: Piping System: Domestic Hot Water 1").
"""

import re

import ifcopenshell.util.element as ue
import ifcopenshell.util.shape as us
import ifcopenshell.util.system as usys

from stoa.answer import Answer, Evidence, ToolError
from stoa.tools.ifc_tools import _label, _norm, _scales, _shape, _storey_of, _find_storey, open_ifc

# kind -> (IFC4 class, IFC2X3 generic class, IFC2X3 type class)
KINDS = {
    "pipe": ("IfcPipeSegment", "IfcFlowSegment", "IfcPipeSegmentType"),
    "fitting": ("IfcPipeFitting", "IfcFlowFitting", "IfcPipeFittingType"),
    "valve": ("IfcValve", "IfcFlowController", "IfcValveType"),
    "fixture": ("IfcSanitaryTerminal", "IfcFlowTerminal", "IfcSanitaryTerminalType"),
    "pump": ("IfcPump", "IfcFlowMovingDevice", "IfcPumpType"),
}
WHAT = [*KINDS, "all"]
GROUP_KEYS = ["system", "system_name", "size", "material", "storey", "type"]


def _elements(path: str, kind: str) -> list:
    f = open_ifc(path)
    ifc4_cls, generic, type_cls = KINDS[kind]
    if f.schema != "IFC2X3":
        try:
            return list(f.by_type(ifc4_cls))
        except RuntimeError:
            return []
    out = []
    for e in f.by_type(generic):
        t = ue.get_type(e)
        if t is not None:
            if t.is_a(type_cls):
                out.append(e)
        elif kind == "pipe" and "pipe" in (e.ObjectType or e.Name or "").lower():
            out.append(e)
    return out


def _mech(e) -> dict:
    return ue.get_psets(e).get("Mechanical", {})


_TRAILING_NUMBER = re.compile(r"(\s+\d+)+$")


def system_names(e) -> list[str]:
    """Names of the piping systems the element belongs to (a fixture can be on several)."""
    names = [s.Name for s in usys.get_element_systems(e) if s.Name]
    if names:
        return names
    m = _mech(e)
    # System Type is per element and reliable; System Classification is sometimes
    # copied from the connector (a hot-water fitting classified as cold water).
    if isinstance(st := m.get("System Type"), str) and st.strip():
        return [st.split(":", 1)[-1].strip()]
    for key in ("System Name", "System Classification"):
        if isinstance(v := m.get(key), str) and v.strip() and v.strip() != "Undefined":
            return [x.strip() for x in v.split(",") if x.strip()]
    if (t := ue.get_type(e)) and t.Name and ":" in t.Name:
        # Revit type names like "Pipe Types:Hot Water:699832".
        return [t.Name.split(":")[1].strip()]
    return ["(no system)"]


def _system(name: str) -> str:
    """'Domestic Hot Water 1' -> 'Domestic Hot Water'; 'Domestic Cold Water 1 2' too."""
    return _TRAILING_NUMBER.sub("", name).strip() or name


def _size(path: str, e) -> str:
    for src in (e, ue.get_type(e)):
        if src is None:
            continue
        for props in ue.get_psets(src).values():
            for k in ("Size", "Overall Size", "NominalDiameter", "Nominal Diameter"):
                v = props.get(k)
                if isinstance(v, str) and re.search(r"\d", v) and k != "NominalDiameter" or isinstance(v, (int, float)) and v > 0:
                    if isinstance(v, (int, float)):
                        return f"{round(v * _scales(path)['length'] * 1000)} mm"
                    return re.sub(r"\s*ø", "", v).strip().replace("mm", " mm").replace("  ", " ")
    return "(no size)"


def _material(e) -> str:
    m = _mech(e)
    for k in ("Pipe Segment", "Material"):
        if isinstance(v := m.get(k), str) and v.strip():
            return v.strip()
    mats = ue.get_materials(e) or ([] if not (t := ue.get_type(e)) else ue.get_materials(t) or [])
    return ", ".join(sorted({x.Name for x in mats if getattr(x, "Name", None)})) or "(no material)"


def _length(path: str, e) -> tuple[float | None, str]:
    scale = _scales(path)["length"]
    for name, props in ue.get_psets(e).items():
        if name and ("Quantities" in name or name == "Dimensions"):
            v = props.get("Length")
            if isinstance(v, (int, float)) and v > 0:
                return v * scale, f"{name}.Length"
    sh = _shape(path, e.id())
    if sh is None:
        return None, "no length"
    g = sh.geometry
    # Longest bounding-box side, vertical included so risers count.
    return max(us.get_x(g), us.get_y(g), us.get_z(g)), "geometry bounding box"


def storey_name(e) -> str | None:
    """Revit's host level (Constraints.Level / Reference Level), else spatial
    containment. Revit exports put many fittings on the lowest storey
    (the footing) although their Level is Level 1."""
    for k in ("Level", "Reference Level"):
        v = ue.get_psets(e).get("Constraints", {}).get(k)
        if isinstance(v, str) and (n := v.split(":", 1)[-1].strip()):
            return n
    s = _storey_of(e)
    return s.Name if s else None


def _type_name(e) -> str:
    t = ue.get_type(e)
    name = (t.Name if t is not None else None) or e.ObjectType or e.Name or e.is_a()
    return name


def _keys(path: str, e, key: str) -> list[str]:
    if key == "system":
        return sorted({_system(n) for n in system_names(e)})
    if key == "system_name":
        return system_names(e)
    if key == "size":
        return [_size(path, e)]
    if key == "material":
        return [_material(e)]
    if key == "storey":
        return [storey_name(e) or "(no storey)"]
    if key == "type":
        return [_type_name(e)]
    raise ToolError(f"Unknown group_by {key!r}. Use {GROUP_KEYS}")


def _fixture_units(e, combo: list[str], keys: list[str]) -> tuple[str, float] | None:
    """Water supply fixture units: HWFU in a hot-water group, CWFU in a cold-water
    group, total WFU otherwise. Drainage groups get none (DFU isn't exported)."""
    m = _mech(e)
    name = "WFU"
    if "system" in keys or "system_name" in keys:
        sys_ = combo[keys.index("system" if "system" in keys else "system_name")].lower()
        name = "HWFU" if "hot" in sys_ else "CWFU" if "cold" in sys_ else None
    v = m.get(name) if name else None
    return (name, float(v)) if isinstance(v, (int, float)) else None


def _size_mm(label: str) -> float:
    m = re.search(r"[\d.]+", label)
    return float(m[0]) if m else 0.0


def piping_summary(path: str) -> str:
    """One line for the router prompt; empty when the model has no piping."""
    counts = {k: len(_elements(path, k)) for k in KINDS}
    if not counts["pipe"] and not counts["fitting"]:
        return ""
    return "Piping in model: " + ", ".join(f"{n} {k}s" for k, n in counts.items() if n)


def ifc_piping(
    path: str,
    what: str = "all",
    group_by: list[str] | str = "system",
    system: str | None = None,
    size: str | None = None,
    storey: str | None = None,
    only: str | None = None,
) -> Answer:
    """Pipes (count and length), fittings, valves, fixtures or pumps grouped by system,
    size, material, storey or type. what='all' gives a per-system overview."""
    if what not in WHAT:
        raise ToolError(f"Unknown what {what!r}. Use {WHAT}")
    keys = [group_by] if isinstance(group_by, str) else list(group_by or ["system"])
    kinds = list(KINDS) if what == "all" else [what]
    if what == "all":
        keys = ["system"]

    f = open_ifc(path)
    target = _find_storey(f, storey) if storey else None
    elems = {k: _elements(path, k) for k in kinds}
    if not any(elems.values()):
        msg = "No piping elements (pipe segments, fittings, valves, fixtures, pumps) in this model"
        return Answer(value=msg, method="IFC piping classes " + ", ".join(v[0] for v in KINDS.values()))

    def keep(e) -> bool:
        if target is not None and storey_name(e) != target.Name:
            return False
        if system and not any(_norm(system) in _norm(n) for n in system_names(e)):
            return False
        if size and _norm(_size(path, e)).replace(" ", "") != _norm(size).replace(" ", ""):
            return False
        return True

    # group -> kind -> {"elems": [...], "length": m}
    groups: dict[str, dict[str, dict]] = {}
    evidence, methods, fixture_units = [], set(), {}
    for kind in kinds:
        for e in elems[kind]:
            if not keep(e):
                continue
            combos = [[]]
            for k in keys:
                combos = [c + [v] for c in combos for v in _keys(path, e, k)]
            length = None
            if kind == "pipe":
                length, how = _length(path, e)
                methods.add(f"pipe length from {how}")
            for combo in combos:
                gkey = " | ".join(combo)
                slot = groups.setdefault(gkey, {}).setdefault(kind, {"elems": [], "length": 0.0})
                slot["elems"].append(e)
                slot["length"] += length or 0.0
                if kind == "fixture" and (fu := _fixture_units(e, combo, keys)):
                    fixture_units[gkey] = (fu[0], fixture_units.get(gkey, (None, 0.0))[1] + fu[1])
            evidence.append(Evidence(ref=e.GlobalId, label=f"{kind} [{', '.join(' | '.join(c) for c in combos)}]: {_label(e)}",
                                     value=round(length, 3) if length is not None else None))

    if only:
        want = _norm(only)
        hit = [k for k in groups if _norm(k) == want] or [k for k in groups if want in _norm(k)]
        if not hit:
            raise ToolError(f"No group matching {only!r}. Groups: {sorted(groups)}")
        groups = {k: groups[k] for k in hit}
        ids = {e.GlobalId for g in groups.values() for s in g.values() for e in s["elems"]}
        evidence = [ev for ev in evidence if ev.ref in ids]
    if not groups:
        raise ToolError("No piping element matched the filters")

    def order(item):
        k, g = item
        if keys[0] == "size":
            return (-_size_mm(k),)
        return (-g.get("pipe", {}).get("length", 0), -sum(len(s["elems"]) for s in g.values()), k)

    unique = lambda kind: len({e.id() for g in groups.values() for e in g.get(kind, {}).get("elems", [])})
    total_len = sum(g.get("pipe", {}).get("length", 0) for g in groups.values())
    head = [f"{kind}s: {unique(kind)}" for kind in kinds if unique(kind)]
    lines = [f"Piping grouped by {' + '.join(keys)}: " + ", ".join(head)
             + (f"; pipe length {total_len:.1f} m" if "pipe" in kinds else "")]
    for k, g in sorted(groups.items(), key=order):
        parts = []
        if p := g.get("pipe"):
            parts.append(f"{len(p['elems'])} pipe segments, {p['length']:.1f} m")
        for kind in ("fitting", "valve", "fixture", "pump"):
            if s := g.get(kind):
                parts.append(f"{len(s['elems'])} {kind}s")
        if k in fixture_units:
            name, units = fixture_units[k]
            parts.append(f"{units:g} {name}")
        lines.append(f"- {k}: " + ", ".join(parts))
        if what == "fitting" and len(keys) == 1 and keys[0] != "type":
            # Fitting mix per group: elbows, tees, transitions.
            mix: dict[str, int] = {}
            for e in g["fitting"]["elems"]:
                t = _type_name(e)
                mix[t] = mix.get(t, 0) + 1
            lines += [f"    {t}: {n}" for t, n in sorted(mix.items(), key=lambda x: -x[1])[:6]]
    if what in ("fixture", "all") and any(len(system_names(e)) > 1 for e in elems.get("fixture", [])):
        lines.append("Fixtures connected to several systems (water supply and drainage) count in each.")

    method = "IFC piping elements grouped by " + ", ".join(keys)
    if methods:
        method += "; " + ", ".join(sorted(methods))
    if only:
        if what == "pipe":
            value, unit = round(total_len, 3), "m"
        else:
            value, unit = len({e.id() for g in groups.values() for s in g.values() for e in s["elems"]}), "count"
        return Answer(value=value, unit=unit, method=method + f"; group {only!r}",
                      evidence=evidence, note="\n".join(lines))
    return Answer(value="\n".join(lines), unit=None, method=method, evidence=evidence)
