# Stoa: verifiable answers about construction drawings

Ask a question about a drawing or BIM model in natural language. The answer
comes with the evidence it was computed from (IFC `GlobalId`, DXF entity
handle, PDF table row), so an onsite engineer can check it on the plan in
seconds. Deterministic extraction comes first; vision models are used only
where there is no vector or BIM data.

Pipeline flowchart: `PIPELINE.md`. Endpoint decisions and experiment log:
`VERDA_LOG.md`. Remaining gaps, ranked by lost questions: `GAPS.md`. Challenge
briefs: `challenge.md`, `side_quest.md`. Verda rules: `VERDA_ENDPOINT_GUIDELINES.md`.

## Setup

Python 3.13, dependencies managed with `uv`:

```bash
uv sync
cp .env.example .env        # PowerShell: Copy-Item .env.example .env (first setup only)
```

Fill in `.env`. The application reads it automatically; keep real keys out of
version control.

| Variable | Used for |
|---|---|
| `VERDA_INFERENCE_BASE_URL`, `VERDA_INFERENCE_API_KEY` | DeepSeek (routing, answer composition, text grading) |
| `VERDA_KIMI` | Kimi (PDF schedules, non-CubiCasa vision) |
| `GEMINI_API_KEY` (or `Gemini_API_KEY`) | Gemini 4 Hydrogen for CubiCasa drawings, and the UI's 3D render |
| `STOA_ROUTER` | Model profile, default `hybrid`. Use `deepseek` if only the DeepSeek key is available |

Keep `GEMINI_API_KEY` in `.env` only: an exported shell variable takes
precedence over `.env`.

## Ask a question

```bash
uv run python -m stoa.ask junction-dataset-preview/data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are drawn on this floor plan?"
```

The output is JSON with the answer, unit, method, tool calls, evidence and a
`deterministic` flag (false for vision-model answers, which need a human check).

## Web UI

```bash
uv run python -m stoa.ui.facts                  # once, ~10 s, no LLM: pre-compute facts into out/facts/
uv run uvicorn stoa.ui.server:app --port 8000   # open http://localhost:8000
```

- Swiss dwellings plans are rendered from the DXF to SVG, so highlights use the
  exact CAD geometry. CubiCasa plans use cached detector boxes.
- **Ask** runs the pipeline and highlights only the evidence the answer cited.
- The engineer can add objects by tapping and reject them by right-clicking.
  Edits are saved to `out/edits/`.
- **3D** renders an isometric view of the plan with Gemini `gemini-nano-banana-2.1`
  (`STOA_IMAGE_MODEL` overrides it).

## Model profiles

| Profile | Routing | PDF schedules | Raster drawings |
|---|---|---|---|
| `hybrid` (default) | DeepSeek | Kimi | Gemini 4 Hydrogen for `cubicasa5k`, Kimi otherwise |
| `hybrid_gemini` | DeepSeek | Kimi | Gemini 4 Hydrogen for all |
| `deepseek` | DeepSeek | DeepSeek | DeepSeek |

Vision calls use `reasoning_effort="low"`, a 4096-token budget and a 1600-pixel
image cap. `GEMINI_MODEL` overrides the Gemini model id.

Every LLM call, including Gemini (`provider="google"`), goes through
`stoa.verda.chat` and is logged with tokens, latency and `cost_usd` to
`logs/verda_calls.jsonl`. Gemini costs use the paid-tier prices checked on
2026-11-14 ($1.50 / $7.50 per million input/output tokens, thinking included).
When Google's usage reports a total larger than prompt + completion, the gap is
counted as output. The raw fields are kept for auditing.

## Evaluation

Needs `junction-dataset-preview/questions.jsonl` and its drawings.

```bash
uv run python -m stoa.eval --per-source 40 --router hybrid --workers 8   # writes out/eval_<profile>_<time>.jsonl
uv run python -m stoa.eval --source cubicasa5k --per-source 30 --seed 1024
uv run python -m stoa.eval --score-existing out/eval_hybrid_<time>.jsonl # grade saved answers only
uv run python -m unittest discover -s tests -v                           # regression tests
```

Keep `--workers` at 8 or fewer: the Verda gateway returns `429 tenant rpm` above that.

- Counts must match exactly. Area, length and volume must be within 2%.
- Text answers are graded by DeepSeek against the reference (0, 0.5 or 1)
  after answering (`--judge-workers 2`). `num_acc` and `text_score` are
  reported separately. Judge failures show as `jerr` and can be retried with
  `--score-existing`, which writes a new `*_scored_<time>.jsonl` file and
  reuses rows already graded.
- Usage totals include judging; `judge$` shows its cost separately.
- `stoa.compare` compares runs on shared questions. Use the same seed and
  `--per-source` so runs are comparable.

## Current results

Small samples (30–64 questions per source), so a few points either way is not significant.

| Source | Questions in preview | Accuracy | Cost per 1k questions | How |
|---|---|---|---|---|
| swiss_dwellings | 2358 | 97.5% | $0.19 | DXF geometry |
| ifc_models | 1049 | 100% (excluding `kit-fzk-haus`, which has no IFC) | $0.29 | IFC queries |
| opening_design | 64 | 47/48 numeric (the one miss was a 429) | $4.51 | Schedules rebuilt from the PDF's ruled grid |
| cubicasa5k | 4270 | 25/30 (83%) with Gemini, vs 17/30 with Kimi | ~$5 | Raster vision |
| ifc_bench | 249 | text, graded by `text_score` | $1.24 | IFC tools + LLM composition |

Sources: `GAPS.md` (hybrid, 40 questions per source, seed 0) and `VERDA_LOG.md`
(CubiCasa Gemini vs Kimi, 30 questions, seed 1024).

## Layout

| Path | Role |
|---|---|
| `stoa/inspect_file.py` | File type and sibling IFC/DXF → which tools apply (no LLM) |
| `stoa/pipeline.py` | Router LLM picks a tool, code answers, vision fallback; model profiles |
| `stoa/answer.py` | `Result` / `Evidence` types shared by every tool |
| `stoa/tools/ifc_tools.py` | IFC counts, quantity sums, room areas, space breakdown, element tables |
| `stoa/tools/dxf_tools.py` | DXF rooms, areas, apartments, doors, windows, fixtures |
| `stoa/tools/pdf_tools.py` | PDF text layer (notes, legends) |
| `stoa/tools/pdf_tables.py` | Schedules rebuilt from the PDF's ruled grid: exact counts/sums per row, ft²↔m² |
| `stoa/tools/vision_tools.py` | Vision-model reading of raster drawings |
| `stoa/tools/cubicasa_tools.py` | Experimental CubiCasa5K CNN, not wired into the pipeline |
| `stoa/verda.py` | The only LLM client; logs every call |
| `endpoints/verda/*.py`, `endpoints/gemini.py` | One module per endpoint: model id, prices, defaults |
| `stoa/ask.py` | CLI: one question about one drawing |
| `stoa/eval.py`, `stoa/judge.py`, `stoa/compare.py` | Evaluation, text grading, run comparison |
| `stoa/ui/` | Verification web UI: `facts.py` (pre-compute), `server.py`, `render.py` (3D) |
| `stoa/cubicasa_probe.py` | Compares the pipeline with the CubiCasa5K CNN on test/val drawings |

The experimental CubiCasa5K model needs the third-party repository cloned to
`./CubiCasa5k`, the pretrained weights and PyTorch. 197 of the 241 preview
drawings are in its train split, so judge it only with `stoa.cubicasa_probe`.
Weights are CC BY-NC 4.0 (Kalervo et al. 2019).

## Data licences

Drawings come from `junction-dataset-preview/`. Licences are per source in
`data/<source>/LICENSE.md`; keep the credit when displaying a drawing.
cubicasa5k is NC-SA.
