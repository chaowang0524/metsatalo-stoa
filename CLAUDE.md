# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Junction hackathon entry for the **Stoa** challenge (full brief in `challenge.md`): build a program that answers natural-language questions about construction drawings and BIM models **accurately and verifiably**. The answer should show where in the drawing it came from so a human can check it in seconds.

Judging is split evenly (25% each) across: answer accuracy, visualization of reasoning for human verification, creativity, and **low inference cost**. Stoa wants approaches that next year's frontier models won't make obsolete: hybrids of LLMs with computer vision, 3D/geometry, IFC parsing, or small fine-tuned models. Favor deterministic extraction (IFC queries, vector geometry from PDF/SVG/DXF) over sending everything to a vision LLM.

## Environment

- Python 3.13 venv in `.venv`, managed with `uv` (`uv sync`, `uv add <pkg>`, `uv run python <script>`). Main pipeline dependencies are declared in `pyproject.toml` and locked in `uv.lock`. Run evaluation regression tests with `uv run python -m unittest discover -s tests -v`. The experimental CubiCasa5K model needs separate source, weights and PyTorch/computer-vision dependencies.
- `PIPELINE.md` describes the current answering pipeline and benchmark results. `VERDA_LOG.md` records each endpoint decision and finding with a date. Add a line there when you learn something about an endpoint.
- Credentials: `secreat.toml` and `.env` (gitignored, holds `NEMOTRON_API_KEY`, loaded by `stoa/verda.py`). `VERDA_KIMI` is exported in `~/.zshrc`, so run `source ~/.zshrc` before Kimi calls from a non-interactive shell. Never print or commit any of these.

## Web UI (`stoa/ui/`)

- `uv run python -m stoa.ui.facts` pre-computes the facts for every swiss_dwellings and cubicasa5k drawing (no LLM) into `out/facts/`. It also draws a piping plan per storey for IFC models that have pipes (the clinic model takes ~2 min to tessellate). Swiss plans are rendered from the DXF to SVG, so highlights use exact CAD geometry. CubiCasa plans use the cached boxes in `out/cubicasa_cache/`, which now hold Gemini full-plan detections (`stoa.cubicasa_gemini`); the CNN is out of the main flow.
- `uv run uvicorn stoa.ui.server:app --port 8000` starts the server (Kimi still needs `source ~/.zshrc` first for `VERDA_KIMI`). The engineer's edits (added/rejected objects) are saved to `out/edits/`. "Ask" runs `stoa.pipeline`. "3D" calls Gemini `gemini-nano-banana-2.1` (`endpoints/gemini.py` `IMAGE_MODEL`, same `GEMINI_API_KEY` from `.env`).
- Symbol library (`stoa/ui/library.py`, `GET /api/library`, cached in `out/facts/library.json`, rebuilt by `stoa.ui.facts` or `uv run python -m stoa.ui.library`): up to 8 distinct real symbols per component kind collected from the Swiss DXF and IFC plans, with real-world sizes, plus the standard symbols in `app.js` `GENERIC`. "▦ Library…" in the add palette lets the engineer stamp any of them on any plan. Added objects store `lib: <variant id>`.
- "€ Cost" is a tab in the side panel, so the plan stays visible. It multiplies plan quantities by `static/prices.json`, and every material and labour price carries its own source (`material_src` / `labour_src`): Taloon.com category medians and fixed installation prices, the K-Rauta window guide, or assumed hours × a published hourly rate (`rates`), all converted to excl. VAT and checked 2026-11-14. The engineer can type over any quantity or unit price and add lines; these are saved to `out/edits/<source>/<stem>.cost.json`. "Send quote…" (dialog button "Send for approval" or "Send to client") freezes a quote (lines, sources, warnings and the edited plan image) to `out/quotes/<id>.json`. The approver or client opens `/quote/<id>` to approve or request changes, print it or download a CSV.
- "Upload" in the top bar (or drag and drop) takes one or many drawings (`stoa/ui/uploads.py`). Uploads have no dataset path, so the kind is decided from the files: DXF (+ its PDF) → exact CAD facts; plan SVG + IFC → BIM facts; PNG/JPG or a scanned PDF → one Gemini full-plan detection call (~$0.10–0.18, ~1 min, background) and Ask treats it as a CubiCasa-style floor plan via `pipeline.answer(..., floor_plan=True)`; other vector PDFs → the same Gemini detection on rendered page 1 for components, rooms from text-layer room stamps when present (exact), and Ask keeps the PDF (a PDF rendered from an uploaded IFC/DXF gets no detection; Ask answers from the model). Files go to `out/uploads/<batch>_<name>`, facts to `out/facts/uploads/`. Needs `python-multipart`.

## Verda endpoints

- Every LLM call goes through `stoa.verda.chat(purpose, messages, endpoint=...)`, which appends one line per call (tokens, latency, `cost_usd`) to `logs/verda_calls.jsonl`. Don't call the OpenAI client directly. Each endpoint is one module in `endpoints/verda/` with `MODEL`, prices and `DEFAULTS`.
- `deepseek` (`deepseek-4-1-flash-1017`, $0.15/$0.60 per M tokens): cheap and reliable at tool calling. It **does** read images sent as `image_url` data URIs, even though `usage` reports only a few hundred prompt tokens for them. The gateway has no `/v1/files`, so DeepSeek's `file_id` image input isn't available.
- `kimi` (`kimi-k3-1-pro`, $2/$10): better at reading drawings and schedules. Thinking is enabled with `reasoning_effort="low"` and `max_tokens=4096` by default for routing, schedule answers and vision. The token budget includes reasoning and the final answer. DeepSeek remains the hybrid router; Kimi previously skipped tools with thinking disabled. Unrestricted effort used up to 12k tokens.
- `nemotron` (Nemotron 3.5 Super VL, a dedicated container, no per-token price): calls have taken ~250 s at times (cold start or overload), so the benchmark left it out.
- `gemini` is a separate Google Developer API endpoint (`endpoints/gemini.py`, the only Gemini module: OpenAI-compatible chat for vision and a `google-genai` `client()` for the 3D render), not served by Verda. `hybrid_gemini` uses `gemini-4-hydrogen` for raster drawings, retaining DeepSeek routing and Kimi PDF handling. Put `GEMINI_API_KEY` (also accepts `Gemini_API_KEY`) in `.env`; the `.env` value overrides a `GEMINI_API_KEY` exported in the shell (`verda.DOTENV_WINS`), since an old unfunded key in `~/.zshrc` returned 402. Calls still go through `stoa.verda.chat` and its log, with `provider="google"`. Defaults: low reasoning and 4096 output tokens. Google output usage includes any positive gap between total and prompt + reported completion tokens; raw fields are retained for auditing.
- The text may arrive in `reasoning_content` instead of `content`. Use `verda.text_of(resp)`.

## Pipeline and evaluation

- `stoa/pipeline.py`: file inspection → the router LLM picks a tool → deterministic tool → answer with evidence. A `Profile` sets which endpoint routes, which composes answers from PDF schedule text, and which reads drawings. The default is `hybrid`: DeepSeek routes, Gemini 4 Hydrogen reads CubiCasa raster drawings (under a `cubicasa5k` directory), and Kimi handles schedules and other vision, with low effort. `hybrid_gemini` additionally sends other raster sources to Gemini. Set `STOA_ROUTER` or `--router` to change it.
- `uv run python -m stoa.eval --per-source 40 --router hybrid` writes `out/eval_<profile>_<time>.jsonl` and automatically grades text answers with DeepSeek after answering (default `--judge-workers 2`). `num_acc` and `text_score` are separate metrics; costs include judging. `--score-existing <results.jsonl>` grades saved answers into a new file without rerunning the pipeline. `stoa.compare` compares runs on shared questions and reuses saved text scores, judging legacy rows as needed. Use the same seed and `--per-source` so runs are comparable.
- CubiCasa5K model (`stoa/tools/cubicasa_tools.py`, still experimental and not wired into the pipeline): needs the repo cloned to `./CubiCasa5k` (gitignored) and `model_best_val_loss_var.pkl` in it. It caches results per drawing in `out/cubicasa_cache/`. Inputs are capped at 1024 px because the authors' post-processing blows up on big plans. **Leakage:** 197 of the 241 preview drawings are in the model's train split. Judge it only on test/val drawings (`uv run python -m stoa.cubicasa_probe`). Weights are CC BY-NC 4.0, so credit Kalervo et al. 2019.

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

## Target users:
Onsite construction engineers 