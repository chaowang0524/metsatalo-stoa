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

- `uv run python -m stoa.ui.facts` pre-computes the facts for every swiss_dwellings and cubicasa5k drawing (~10 s, no LLM) into `out/facts/`. Swiss plans are rendered from the DXF to SVG, so highlights use exact CAD geometry. CubiCasa plans use the cached detector boxes.
- `source ~/.zshrc && uv run uvicorn stoa.ui.server:app --port 8000` starts the server. The engineer's edits (added/rejected objects) are saved to `out/edits/`. "Ask" runs `stoa.pipeline`. "3D" calls Gemini (`GEMINI_API_KEY`).

## Verda endpoints

- Every LLM call goes through `stoa.verda.chat(purpose, messages, endpoint=...)`, which appends one line per call (tokens, latency, `cost_usd`) to `logs/verda_calls.jsonl`. Don't call the OpenAI client directly. Each endpoint is one module in `endpoints/verda/` with `MODEL`, prices and `DEFAULTS`.
- `deepseek` (`deepseek-4-1-flash-1017`, $0.15/$0.60 per M tokens): cheap and reliable at tool calling. It **does** read images sent as `image_url` data URIs, even though `usage` reports only a few hundred prompt tokens for them. The gateway has no `/v1/files`, so DeepSeek's `file_id` image input isn't available.
- `kimi` (`kimi-k3-1-pro`, $2/$10): better at reading drawings and schedules. Thinking is enabled with `reasoning_effort="low"` and `max_tokens=4096` by default for routing, schedule answers and vision. The token budget includes reasoning and the final answer. DeepSeek remains the hybrid router; Kimi previously skipped tools with thinking disabled. Unrestricted effort used up to 12k tokens.
- `nemotron` (Nemotron 3.5 Super VL, a dedicated container, no per-token price): calls have taken ~250 s at times (cold start or overload), so the benchmark left it out.
- The text may arrive in `reasoning_content` instead of `content`. Use `verda.text_of(resp)`.

## Pipeline and evaluation

- `stoa/pipeline.py`: file inspection → the router LLM picks a tool → deterministic tool → answer with evidence. A `Profile` sets which endpoint routes, which composes answers from PDF schedule text, and which reads drawings. The default is `hybrid` (deepseek routes, kimi does schedules and vision at low effort). Set `STOA_ROUTER` or `--router` to change it.
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