# UI idea: pre-processed drawing review

## The idea

All the blueprints are processed **before** the construction engineer opens them. When the engineer opens a drawing, the basic facts about it are already there, for example "5 windows on this floor", "7 doors", "living room 24.3 m²".

- **One page:** the drawing on one side and the list of results next to it.
- **Clickable results:** clicking a result (e.g. "Windows: 5") highlights those elements on the drawing.
- **Numbered annotations:** each highlighted element gets a number (1, 2, 3, 4, 5) so the engineer can count along and check the answer in seconds.
- The engineer's job changes from "count everything yourself" to "confirm what the machine found". This is the verification the challenge brief asks for.

## Sketch

```
┌──────────────────────────────────────┬─────────────────────────┐
│                                      │ Floor 3: site1000_…pdf  │
│     [drawing, pan/zoom]              │                         │
│                                      │ ▶ Windows        5  ✓   │
│        ①──┐        ②                 │   Doors          7      │
│           │                          │   Rooms          6      │
│     ③     └──── ④         ⑤          │   Living room 24.3 m²   │
│                                      │   Apartment   78.1 m²   │
│                                      │                         │
│                                      │ Ask a question… [    ]  │
└──────────────────────────────────────┴─────────────────────────┘
  Clicking "Windows 5" draws numbered boxes ①–⑤ on the windows.
  Method: "DXF window symbols", deterministic, 0 LLM tokens.
```

## Why it fits the judging

- **Visualization (25%):** every number points at exactly the geometry it was computed from.
- **Cost (25%):** the standard facts are computed once per drawing, deterministically (IFC/DXF), with no LLM calls. The LLM is only needed for free-form questions typed into the box.
- **Accuracy (25%):** the engineer sees immediately if a box is missing or extra.

## How it maps onto what we have

- `stoa/answer.py`: `Evidence` already carries `ref`, `label`, `value`, `bbox` and `page`. That is exactly the data an annotation needs.
- DXF tools (swiss_dwellings) already fill `bbox` for rooms, doors, windows and apartments. The PDF and PNG are the same drawing, so we only need a DXF→PDF/PNG coordinate transform.
- IFC tools (ifc_models) return GlobalIds. To place them on the plan PDF/SVG we need either element placement projected to plan coordinates or matching to the SVG elements.
- Vision fallback (cubicasa5k) returns only a page and has no boxes. A detector would need to return boxes for this to work there.

## Open steps

1. Batch job: for each drawing, run a fixed set of "standard questions" (counts per element type, room areas, total area) and cache the `Answer` + evidence as JSON.
2. Coordinate mapping from source geometry (DXF/IFC) to the rendered image.
3. Single-page viewer: drawing with an SVG overlay, results list, click → numbered highlights, free-text question box that goes through `stoa.pipeline`.
