"""Batch job: the standard facts of every drawing, computed once, with no LLM.

For each drawing we write out/facts/<source>/<stem>.json:

    {"id", "source", "file", "credit",
     "view": {"width", "height", "background": url, "svg": url | null},
     "facts": [{"id", "group", "label", "value", "unit", "method", "deterministic",
                "kind", "items": [{"ref", "label", "value", "shape"}]}]}

Every shape is in view coordinates (origin top-left, y down), so the UI draws
highlights straight over the background without knowing the source format:
`{"bbox": [x0, y0, x1, y1]}` or `{"poly": [[x, y], ...]}`.

- swiss_dwellings: geometry from the DXF (exact). The DXF is also rendered to
  SVG here, so the background and the highlights share one coordinate system.
- ifc_models: the plan SVGs were rendered from the IFC and tag every element
  with its GlobalId (data-guid), so shapes come from the SVG and names, areas
  and storeys from the IFC. No projection needed.
- cubicasa5k: icon and room boxes from the CubiCasa5K model cache, in PNG pixels.

    uv run python -m stoa.ui.facts            # every supported drawing
    uv run python -m stoa.ui.facts --source cubicasa5k --limit 20
"""

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox as ezbbox
from PIL import Image

from stoa.inspect_file import _source_ifc
from stoa.tools import dxf_tools, ifc_tools

DATA = Path("junction-dataset-preview/data")
OUT = Path("out/facts")
CUBICASA_CACHE = Path("out/cubicasa_cache")
SCALE = 100  # view units per metre for DXF drawings (1 unit = 1 cm)
MARGIN = 0.5  # metres around the DXF extents

CREDITS = {
    "cubicasa5k": "CubiCasa5K, © 2019 CubiCasa, CC BY-NC-SA 4.0 (Kalervo et al., SCIA 2019)",
    "ifc_models": "IFC models: see junction-dataset-preview/data/ifc_models/LICENSE.md for each model's credit",
    "swiss_dwellings": "Swiss Dwellings v3.0.0, Archilyse AG, CC BY 4.0 (doi:10.5281/zenodo.7788422)",
}
CUBICASA_METHOD = "CubiCasa5K detector (Kalervo et al. 2019), model output — please verify"


# ---- DXF -> SVG ----------------------------------------------------------------

LAYER_STYLE = {
    "WALL": 'fill="#2b2b2b" stroke="#2b2b2b" stroke-width="2"',
    "WINDOW": 'fill="none" stroke="#3a6ea5" stroke-width="2"',
    "DOOR": 'fill="none" stroke="#666" stroke-width="1.5"',
    "FIXTURE": 'fill="none" stroke="#777" stroke-width="1.2"',
    "ROOM_OUTLINE": 'fill="none" stroke="#bbb" stroke-width="1"',
    "UNIT_OUTLINE": 'fill="none" stroke="#999" stroke-width="2" stroke-dasharray="12 6"',
}


class Frame:
    """Maps DXF metres (y up) to view units (y down, origin top-left)."""

    def __init__(self, msp):
        ext = ezbbox.extents(msp)
        self.x0 = ext.extmin.x - MARGIN
        self.y1 = ext.extmax.y + MARGIN
        self.width = round((ext.size.x + 2 * MARGIN) * SCALE, 1)
        self.height = round((ext.size.y + 2 * MARGIN) * SCALE, 1)

    def pt(self, x, y) -> list[float]:
        return [round((x - self.x0) * SCALE, 1), round((self.y1 - y) * SCALE, 1)]

    def bbox(self, b) -> list[float]:
        (ax, ay), (bx, by) = self.pt(b[0], b[1]), self.pt(b[2], b[3])
        return [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]

    def poly(self, polygon) -> list[list[float]]:
        return [self.pt(x, y) for x, y in list(polygon.exterior.coords)[:-1]]


def _path(points, closed) -> str:
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    return d + (" Z" if closed else "")


def dxf_to_svg(msp, frame: Frame) -> str:
    """Plain SVG of the plan; the drawing set uses only these entity types."""
    parts = []
    for e in msp:
        layer, t = e.dxf.layer, e.dxftype()
        style = LAYER_STYLE.get(layer, 'fill="none" stroke="#888" stroke-width="1"')
        if t == "LWPOLYLINE":
            pts = [frame.pt(x, y) for x, y in e.get_points("xy")]
            parts.append(f'<path d="{_path(pts, e.closed)}" {style}/>')
        elif t == "HATCH":
            for p in e.paths:
                verts = getattr(p, "vertices", None)
                if verts:
                    pts = [frame.pt(v[0], v[1]) for v in verts]
                    parts.append(f'<path d="{_path(pts, True)}" fill="#2b2b2b" stroke="none"/>')
        elif t == "CIRCLE":
            (cx, cy), r = frame.pt(e.dxf.center.x, e.dxf.center.y), e.dxf.radius * SCALE
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" {style}/>')
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            p0 = frame.pt(c.x + r * math.cos(a0), c.y + r * math.sin(a0))
            p1 = frame.pt(c.x + r * math.cos(a1), c.y + r * math.sin(a1))
            sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
            # y flips, so a counter-clockwise DXF arc is clockwise in SVG (sweep flag 0).
            parts.append(f'<path d="M{p0[0]},{p0[1]} A{r * SCALE:.1f},{r * SCALE:.1f} 0 '
                         f'{int(sweep > 180)} 0 {p1[0]},{p1[1]}" {style}/>')
        elif t == "TEXT":
            x, y = frame.pt(*dxf_tools._text_point(e).coords[0])
            h = max(e.dxf.height * SCALE, 8)
            txt = (e.dxf.text.replace("&", "&amp;").replace("<", "&lt;"))
            anchor = ("start", "middle", "end")[min(e.dxf.get("halign", 0), 2)] if e.dxf.get("halign", 0) < 3 else "middle"
            base = "middle" if e.dxf.get("valign", 0) == 2 else "auto"
            parts.append(f'<text x="{x}" y="{y}" font-size="{h:.1f}" text-anchor="{anchor}" '
                         f'dominant-baseline="{base}" fill="#333" font-family="Helvetica, Arial">{txt}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {frame.width} {frame.height}" '
            f'width="{frame.width}" height="{frame.height}">'
            f'<rect width="100%" height="100%" fill="#fff"/>' + "".join(parts) + "</svg>")


# ---- facts -------------------------------------------------------------------

def _fact(fid, group, label, items, unit="count", value=None, method="", deterministic=True, kind=None):
    return {
        "id": fid, "group": group, "label": label,
        "value": len(items) if value is None else value, "unit": unit,
        "method": method, "deterministic": deterministic,
        "kind": kind or fid, "items": items,
    }


def swiss_facts(dxf_path: Path) -> dict:
    msp = ezdxf.readfile(dxf_path).modelspace()
    frame = Frame(msp)
    plan = dxf_tools.load_plan(str(dxf_path))
    stem = dxf_path.stem
    svg_path = OUT / "swiss_dwellings" / f"{stem}.svg"
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(dxf_to_svg(msp, frame))

    def ent(e, label):
        return {"ref": e.dxf.handle, "label": label, "shape": {"bbox": frame.bbox(dxf_tools._bounds(e))}}

    facts = []
    symbols = [
        ("window", "Windows", [
            {"ref": ",".join(e.dxf.handle for e in g), "label": "window",
             "shape": {"bbox": frame.bbox(dxf_tools._union([dxf_tools._bounds(e) for e in g]))}}
            for g in plan.windows]),
        ("door", "Doors", [ent(e, "door") for e in plan.doors]),
        ("toilet", "Toilets", [ent(e, "toilet") for e in plan.toilets]),
        ("washbasin", "Washbasins", [ent(e, "washbasin") for e in plan.washbasins]),
        ("bathtub", "Bathtubs", [ent(e, "bathtub") for e in plan.bathtubs]),
        ("shower", "Showers", [ent(e, "shower") for e in plan.showers]),
    ]
    for kind, label, items in symbols:
        facts.append(_fact(kind, "Components", label, items, method=f"DXF {kind} symbols (layer geometry)"))

    def room_item(r):
        return {"ref": r.handle, "label": r.label, "value": round(r.area, 2),
                "shape": {"poly": frame.poly(r.polygon)}}

    numbered = [r for r in plan.rooms if r.number]
    facts.append(_fact("apartment", "Spaces", "Apartments", [
        {"ref": plan.unit_handles[k], "label": f"Apartment {k}",
         "value": round(sum(r.area for r in numbered if r.unit == k and not r.outdoor), 2),
         "shape": {"poly": frame.poly(p)}}
        for k, p in sorted(plan.units.items())], method="DXF unit outlines + labels"))
    facts.append(_fact("room", "Spaces", "Rooms", [room_item(r) for r in numbered],
                       method="DXF room outlines with a room number"))
    for name, n in sorted(Counter(r.name for r in plan.rooms if r.name).items(), key=lambda x: -x[1]):
        rs = [r for r in plan.rooms if r.name == name]
        facts.append(_fact(f"room:{name}", "Rooms by name", name, [room_item(r) for r in rs],
                           method="DXF room labels", kind="room"))
    try:
        tot = dxf_tools.dxf_total_area(str(dxf_path))
        handles = {e.ref for e in tot.evidence}
        facts.append(_fact("area_total", "Areas", "Total floor area (indoor)",
                           [room_item(r) for r in plan.rooms if r.handle in handles],
                           unit="m2", value=tot.value, method=tot.method, kind="room"))
    except dxf_tools.ToolError:
        pass
    for k in sorted(plan.units):
        rs = [r for r in numbered if r.unit == k and not r.outdoor]
        if rs:
            facts.append(_fact(f"area_apt:{k}", "Areas", f"Apartment {k} area", [room_item(r) for r in rs],
                               unit="m2", value=round(sum(r.area for r in rs), 2),
                               method=f"sum of {len(rs)} room areas", kind="room"))

    return {
        "id": f"swiss_dwellings/{stem}", "source": "swiss_dwellings", "file": f"{stem}.pdf",
        "credit": CREDITS["swiss_dwellings"],
        "view": {"width": frame.width, "height": frame.height,
                 "background": f"/facts/swiss_dwellings/{stem}.svg", "units_per_m": SCALE,
                 # DXF metres -> view: x' = (x - x0) * scale, y' = (y1 - y) * scale
                 "dxf_frame": [frame.x0, frame.y1, SCALE]},
        "facts": facts,
    }


IFC_KINDS = {  # IFC class -> (fact id, label, group)
    "IfcWindow": ("window", "Windows", "Components"), "IfcDoor": ("door", "Doors", "Components"),
    "IfcSanitaryTerminal": ("sanitary", "Sanitary fixtures", "Components"),
    "IfcStair": ("stair", "Stairs", "Components"), "IfcStairFlight": ("stair", "Stairs", "Components"),
    "IfcColumn": ("column", "Columns", "Structure"), "IfcBeam": ("beam", "Beams", "Structure"),
    "IfcWall": ("wall", "Walls", "Structure"), "IfcWallStandardCase": ("wall", "Walls", "Structure"),
    "IfcSlab": ("slab", "Slabs", "Structure"), "IfcMember": ("member", "Members (rafters etc.)", "Structure"),
    "IfcRailing": ("railing", "Railings", "Structure"),
    "IfcFurnishingElement": ("furniture", "Furniture", "Components"),
}
SVG_NS = {"s": "http://www.w3.org/2000/svg"}
IFC_SCALE = 10  # view units per SVG unit (mm on the sheet)
_TOKEN = re.compile(r"[MLAZmlaz]|-?\d*\.?\d+(?:e-?\d+)?")


def _svg_points(d: str) -> list[tuple[float, float]]:
    """Vertices of an M/L/A/Z path (arcs by their end points), absolute coordinates."""
    toks, pts, i, cmd = _TOKEN.findall(d), [], 0, "M"
    while i < len(toks):
        if toks[i] in "MLAZmlaz":
            cmd = toks[i].upper()
            i += 1
            continue
        n = 7 if cmd == "A" else 2
        vals = [float(t) for t in toks[i:i + n]]
        i += n
        if len(vals) == n:
            pts.append((vals[-2], vals[-1]))
    return pts


def _hull(pts):
    """Convex hull (monotone chain): a tight outline for rotated elements."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]
    return half(pts) + half(reversed(pts))


def ifc_facts(svg: Path) -> dict | None:
    ifc = _source_ifc(svg)  # None when the model isn't in the dataset: SVG names only
    root = ET.parse(svg).getroot()
    vx, vy, vw, vh = (float(v) for v in root.get("viewBox").split())

    def pt(x, y):
        return [round((x - vx) * IFC_SCALE, 1), round((y - vy) * IFC_SCALE, 1)]

    shapes: dict[str, tuple[str, dict]] = {}  # guid -> (class, shape)
    names: dict[str, str] = {}
    for g in root.iter("{http://www.w3.org/2000/svg}g"):
        guid, cls = g.get("data-guid"), g.get("class")
        if not guid or not cls:
            continue
        names[guid] = g.get("data-name") or cls
        pts = [p for path in g.iterfind(".//s:path", SVG_NS) for p in _svg_points(path.get("d", ""))]
        if not pts:
            continue
        hull = pts if cls == "IfcSpace" else _hull(pts)
        if len(hull) >= 3:
            shape = {"poly": [pt(*p) for p in hull]}
        else:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            shape = {"bbox": pt(min(xs), min(ys)) + pt(max(xs), max(ys))}
        shapes[guid] = (cls, shape)

    f = ifc_tools.open_ifc(str(ifc)) if ifc else None

    def entity(guid):
        try:
            return f.by_guid(guid) if f else None
        except RuntimeError:
            return None

    # The plan's storey: the one most of its elements are contained in.
    storeys = Counter(s.Name for g in list(shapes)[:200]
                      if (e := entity(g)) and (s := ifc_tools._storey_of(e)))
    storey_name = storeys.most_common(1)[0][0] if storeys else None

    groups: dict[str, list] = {}
    for guid, (cls, shape) in shapes.items():
        groups.setdefault(cls, []).append((guid, shape))
    facts, by_kind = [], {}
    for cls, members in groups.items():
        if cls not in IFC_KINDS:
            continue
        fid, label, group = IFC_KINDS[cls]
        for guid, shape in members:
            name = ifc_tools._label(e) if (e := entity(guid)) else names[guid]
            by_kind.setdefault((fid, label, group), []).append({"ref": guid, "label": name, "shape": shape})
    method = (f"IFC elements drawn on this plan, matched to {ifc.name} by GlobalId" if ifc
              else "elements tagged in the plan SVG (source IFC not in the dataset)")
    for (fid, label, group), items in by_kind.items():
        facts.append(_fact(fid, group, label, items, method=method))
    order = {"Components": 0, "Spaces": 1, "Areas": 2, "Structure": 3}

    rooms = []
    for guid, shape in groups.get("IfcSpace", []):
        if (e := entity(guid)) is None:
            rooms.append({"ref": guid, "label": names[guid], "value": None, "shape": shape, "src": "no IFC"})
            continue
        area, src = ifc_tools._quantity(str(ifc), e, "net_floor_area")
        rooms.append({"ref": guid, "label": ifc_tools._label(e),
                      "value": round(area, 2) if area else None, "shape": shape, "src": src})
    if rooms:
        facts.append(_fact("room", "Spaces", "Rooms (IfcSpace)", rooms, method=method, kind="room"))
        with_area = [r for r in rooms if r["value"]]
        srcs = sorted({r.pop("src") for r in rooms})
        if with_area:
            facts.append(_fact("area_total", "Areas", "Net floor area, all rooms", with_area, unit="m2",
                               value=round(sum(r["value"] for r in with_area), 2),
                               method=f"sum of {len(with_area)} IfcSpace net floor areas ({', '.join(srcs)})",
                               kind="room"))
    facts.sort(key=lambda x: order.get(x["group"], 9))
    return {
        "id": f"ifc_models/{svg.stem}", "source": "ifc_models", "file": f"{svg.stem}.pdf",
        "credit": CREDITS["ifc_models"], "storey": storey_name, "ifc": ifc.name if ifc else None,
        "view": {"width": round(vw * IFC_SCALE, 1), "height": round(vh * IFC_SCALE, 1),
                 "background": f"/data/ifc_models/{svg.name}"},
        "facts": facts,
    }


ICON_LABELS = {  # CubiCasa class -> (fact id, plural label)
    "Window": ("window", "Windows"), "Door": ("door", "Doors"), "Toilet": ("toilet", "Toilets"),
    "Sink": ("sink", "Sinks"), "Bathtub": ("bathtub", "Bathtubs"), "Sauna Bench": ("sauna", "Sauna benches"),
    "Fire Place": ("fireplace", "Fireplaces"), "Closet": ("closet", "Closets"),
    "Electrical Appliance": ("appliance", "Appliances"), "Chimney": ("chimney", "Chimneys"),
}


def cubicasa_facts(png: Path) -> dict | None:
    cached = CUBICASA_CACHE / f"{png.stem}.json"
    if not cached.exists():
        return None
    det = json.loads(cached.read_text())
    w, h = Image.open(png).size
    facts = []
    for cls, (fid, label) in ICON_LABELS.items():
        items = [{"ref": f"{fid}-{i}", "label": cls.lower(), "shape": {"bbox": x["bbox"]}}
                 for i, x in enumerate(x for x in det["icons"] if x["class"] == cls)]
        if items or fid in ("window", "door", "toilet", "sink"):
            facts.append(_fact(fid, "Components", label, items, method=CUBICASA_METHOD, deterministic=False))
    rooms = Counter(r["class"] for r in det["rooms"] if r["class"] not in ("Outdoor", "Undefined"))
    facts.append(_fact("room", "Spaces", "Rooms", [
        {"ref": f"room-{i}", "label": r["class"], "shape": {"bbox": r["bbox"]}}
        for i, r in enumerate(det["rooms"]) if r["class"] not in ("Outdoor", "Undefined")],
        method=CUBICASA_METHOD, deterministic=False))
    for cls, n in rooms.most_common():
        facts.append(_fact(f"room:{cls}", "Rooms by type", cls, [
            {"ref": f"room-{i}", "label": cls, "shape": {"bbox": r["bbox"]}}
            for i, r in enumerate(det["rooms"]) if r["class"] == cls],
            method=CUBICASA_METHOD, deterministic=False, kind="room"))
    return {
        "id": f"cubicasa5k/{png.stem}", "source": "cubicasa5k", "file": png.name,
        "credit": CREDITS["cubicasa5k"],
        "view": {"width": w, "height": h, "background": f"/data/cubicasa5k/{png.name}"},
        "facts": facts,
    }


def build(source: str | None = None, limit: int | None = None) -> list[dict]:
    jobs = []
    if source in (None, "ifc_models"):
        jobs += [("ifc_models", p) for p in sorted((DATA / "ifc_models").glob("*.svg"))[:limit]]
    if source in (None, "swiss_dwellings"):
        jobs += [("swiss_dwellings", p) for p in sorted((DATA / "swiss_dwellings").glob("*.dxf"))[:limit]]
    if source in (None, "cubicasa5k"):
        jobs += [("cubicasa5k", p) for p in sorted((DATA / "cubicasa5k").glob("*.png"))[:limit]]
    index = []
    for src, p in jobs:
        try:
            doc = {"swiss_dwellings": swiss_facts, "ifc_models": ifc_facts, "cubicasa5k": cubicasa_facts}[src](p)
        except Exception as e:  # one bad drawing shouldn't stop the batch
            print(f"skip {p.name}: {type(e).__name__}: {e}")
            continue
        if doc is None:
            continue
        out = OUT / src / f"{p.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False))
        summary = {f["id"]: f["value"] for f in doc["facts"] if f["group"] == "Components"}
        index.append({"id": doc["id"], "source": src, "file": doc["file"], "summary": summary})
    # Merge with drawings built in earlier runs of other sources.
    idx_path = OUT / "index.json"
    old = json.loads(idx_path.read_text()) if idx_path.exists() else []
    ids = {d["id"] for d in index}
    index = [d for d in old if d["id"] not in ids] + index
    idx_path.write_text(json.dumps(sorted(index, key=lambda d: d["id"]), ensure_ascii=False))
    return index


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["ifc_models", "swiss_dwellings", "cubicasa5k"])
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    idx = build(a.source, a.limit)
    print(f"{len(idx)} drawings indexed in {OUT}/index.json")
