# Mestari: the onsite construction engineer's co-pilot

## The problem

Onsite engineers make decisions from drawings every day: how many windows are on this floor, what is the area of this apartment, what will this change cost. Counting by hand is slow, and it has to be done for every design. 

## Our approach: deterministic first

If a drawing contains vector or BIM data, code reads the answer straight from the geometry. A cheap LLM only decides which tool to run. Vision models are used only for raster scans, and those answers are marked as needing a human check.

```
drawing + question
   ├─ inspect_file (no LLM): which formats exist? DXF / IFC / PDF text layer / raster
   ├─ vector / BIM ──► DeepSeek picks a tool ──► code computes the answer
   └─ raster only ──► vision model lists each object + location ──► code counts the list
   ▼
answer + unit + method + evidence (IFC GlobalId / DXF handle / PDF table row) + "exact" or "verify" badge
```

- **IFC** (IfcOpenShell): counts, quantity sums, room areas, type breakdowns, material layers and piping.
- **DXF**: rooms, areas, apartments, doors, windows, fixtures and baseboard length, read from the CAD entities. Highlights use the exact CAD geometry.
- **PDF schedules**: door and window schedules are rebuilt from the PDF's ruled grid, so the totals are added up by code, not by a model.
- **Raster plans**: Gemini makes one detection pass over the whole plan, and code counts the objects it found. Every detected object is drawn as a box the engineer can check.

Better frontier models will improve the vision fallback. They won't make the deterministic part obsolete.

## What the engineer gets

1. **Grounded facts at a glance.** Counts and room areas are computed ahead of time for every plan, so they appear the moment the plan opens, with no waiting and no LLM call. Hover over a fact to highlight the exact objects behind it.
2. **Live editing.** Add doors, windows, sockets and fixtures with a click, remove them with a right-click, and undo with ⌘Z. A symbol library collected from real CAD and IFC plans lets you stamp components at their real-world size. You can export the edited plan as PNG, JPEG or PDF, or turn it into a 3D render.
3. **Live cost estimate.** Plan quantities × Finnish material and labour prices. Every price cites its source (Taloon.com, K-Rauta, published hourly rates). The engineer can overwrite any quantity or price. "Submit" freezes a quote with an approval link, where the client can approve it, request changes, print it or download a CSV.
4. **Bring your own drawing.** Upload a DXF, IFC + SVG, PDF or scanned image, and it becomes a plan you can check.
5. **For further questions, ask anything, in any language.** Each answer has an **exact** or **model answer · verify** badge, a "Highlight references" button that shows only the evidence used, and the method, the tools called and the cost.
6. **The human comes first.** Models make mistakes. So do calculations, price lists and even human-labelled data. Everything is visible and editable, and edits are saved per drawing, so the next person sees the corrected plan.

## Results (preview dataset)

| Source | Accuracy | Cost / question | Method |
|---|---|---|---|
| swiss_dwellings | 97.5% | $0.0002 | DXF geometry |
| ifc_models | 100% (excl. one model with no IFC) | $0.0003 | IFC queries |
| opening_design | 47/48 numeric | $0.0045 | Schedules rebuilt from the PDF grid |
| cubicasa5k (raster) | 83% | ~$0.005 | Vision + code counting |
| ifc_bench | text answers | $0.0012 | IFC tools + LLM composition |

These figures come from small samples (30–64 questions per source), scored by the challenge rules: counts must match exactly, and areas within 2%. **Every model call is logged** with its tokens, latency and USD cost, so the cost figures can be checked.

## Tech stack

Python 3.13 · FastAPI · IfcOpenShell · ezdxf · PDF vector/text extraction · DeepSeek (routing, $0.15/$0.60 per M tokens) · Kimi (PDF schedules and vision) · Gemini (raster plans and 3D render), served through Verda inference with per-call cost logging.

## Try it

- Live demo: https://65-108-33-73.sslip.io (login in the README)

*Credits: CubiCasa5K floor plans © Kalervo et al. 2019, CC BY-NC-SA. Other drawings are used under their dataset licences.*
