# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Junction hackathon entry for the **Stoa** challenge (full brief in `challenge.md`): build a program that answers natural-language questions about construction drawings and BIM models **accurately and verifiably**. The answer should show where in the drawing it came from so a human can check it in seconds.

Judging is split evenly (25% each) across: answer accuracy, visualization of reasoning for human verification, creativity, and **low inference cost**. Stoa wants approaches that next year's frontier models won't make obsolete: hybrids of LLMs with computer vision, 3D/geometry, IFC parsing, or small fine-tuned models. Favor deterministic extraction (IFC queries, vector geometry from PDF/SVG/DXF) over sending everything to a vision LLM.

## Environment

- Python 3.13 venv in `.venv`, managed with `uv` (`uv pip install <pkg>`, `uv run python <script>`). No `pyproject.toml` or tests exist yet.
- LLM access goes through an OpenAI-compatible endpoint (Verda inference). `inference.py` is the minimal example: it reads `VERDA_INFERENCE_BASE_URL` and `VERDA_INFERENCE_API_KEY` from the environment and calls model `deepseek-4-1-flash-1017`. Responses may put the text in `reasoning_content` rather than `content`. Log `resp.usage`, since cost is a judging criterion.
- `secreat.toml` holds credentials. Don't print it or commit it.

## Dataset (`junction-dataset-preview/`)

A preview subset of the full dataset (~14k drawings, ~120k questions). Treat it as read-only.

- `data/<source>/<file>`: drawings. Files with the same stem and a different extension are the same drawing in another format (e.g. `.pdf`/`.dxf`/`.png` in `swiss_dwellings`). The `ifc_models` plans `<model>_<plan-level>.pdf/.svg` were rendered from `<model>.ifc`, so questions about those PDFs can be answered exactly from the IFC.
- `questions.jsonl`: one `{"path", "question", "answer", "unit", "type"}` per line, with `path` relative to `junction-dataset-preview/`. This is the eval set. The preview has 7,990 questions over 320 files:
  - by type: count 6076, area 1599, text 265, volume 34, length 16
  - by source: cubicasa5k (PNG raster floor plans, 4270), swiss_dwellings (PDF, 2358), ifc_models (PDF+IFC, 1049), ifc_bench (IFC, 249), opening_design (PDF, 64)
  - Questions are often duplicated across languages (e.g. English and Finnish pairs for cubicasa5k), so the question text can't be assumed to be English.
- **Scoring:** counts must match exactly. Area, length and volume must be within 2%. Text answers are free-form.
- Formats on disk: jpg, png, pdf, dxf, dwg, svg, ifc. Sources without questions (habs, finna, europeana, wikimedia) are historic or scanned drawings, useful only for qualitative demos.
- Licences are per file in `data/<source>/LICENSE.md`. Keep the credit when displaying a drawing. cubicasa5k is NC-SA.
- `junction-dataset-preview.zip` (~478 MB) is the original archive. Don't unzip it over the extracted folder.
