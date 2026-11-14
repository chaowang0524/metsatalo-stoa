# Stoa: verifiable answers about construction drawings

Current pipeline (flowchart): `PIPELINE.md`. Verda rules: `VERDA_ENDPOINT_GUIDELINES.md`. Challenge briefs: `challenge.md`, `side_quest.md`. UI concept: `UI_IDEA.md`.

## Run

Install the main pipeline dependencies into `.venv` with `uv sync` (Python 3.13).
Copy `.env.example` to `.env` and fill in the Verda URL and API keys. The default
`hybrid` profile needs both `VERDA_INFERENCE_API_KEY` (DeepSeek) and `VERDA_KIMI`.
If only the DeepSeek key is available, set `STOA_ROUTER=deepseek` in `.env`.
The application reads `.env` automatically; keep real keys out of version control.

Windows PowerShell, from the repository root:

```powershell
uv sync
Copy-Item .env.example .env  # first setup only; preserve an existing .env
# Fill in .env, then ask about a drawing in the local data directory:
uv run python -m stoa.ask data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are drawn on this floor plan?"
```

Evaluation additionally requires `junction-dataset-preview/questions.jsonl` and
its referenced drawings under `junction-dataset-preview/data/`. The local
`data/` directory alone is enough for individual drawing questions, but not for
the evaluation command below.

```bash
uv run python -m stoa.eval --per-source 30 --workers 8      # writes out/eval_*.jsonl
```

Every Verda call is logged to `logs/verda_calls.jsonl`.

The experimental CubiCasa model is not part of the main dependency setup. It
also needs the third-party `CubiCasa5k` source repository, pretrained weights,
and its PyTorch/computer-vision dependencies before it can run.

## Layout

| File | Role |
|---|---|
| `stoa/inspect_file.py` | Step 1: file type → which tools apply (no LLM) |
| `stoa/pipeline.py` | Steps 2–4: LLM picks a tool, code answers, vision fallback |
| `stoa/tools/ifc_tools.py` | IFC counts, quantity sums, room areas, element tables |
| `stoa/tools/dxf_tools.py` | DXF rooms, areas, apartments, doors, windows, fixtures |
| `stoa/tools/pdf_tools.py` | PDF text layer (schedules) |
| `stoa/tools/vision_tools.py` | Vision-model fallback for raster drawings |
| `stoa/verda.py` | The only Verda client; logs every call |
| `stoa/eval.py` | Scores against `questions.jsonl` (counts exact, measures ±2%) |

## Baseline (30 random questions per source, seed 0)

| Source | Accuracy | LLM calls/q | Tokens/q | Deterministic |
|---|---|---|---|---|
| swiss_dwellings | 100% | 1.0 | ~1.2k | 100% |
| ifc_models | 100% | 1.1 | ~1.8k | 97% |
| cubicasa5k | 47% | 1.0 | ~0.5k | 0% (vision only) |
| opening_design | 26% | 3.4 | ~14k | 0% |
| ifc_bench (text, unscored) | – | 2.1 | ~6.4k | 3% |

Tuning the tools directly against all English questions gave 513/513 on IFC-backed ifc_models questions and 1168/1179 on swiss_dwellings.

## Known gaps / next steps

- **cubicasa5k (53% of questions)** is raster only and runs on the vision fallback. The next step is a small symbol/room detector fine-tuned on a Verda GPU.
- **opening_design** schedules are read as flattened text, and the LLM misreads table columns. Needs table extraction.
- The `kit-fzk-haus` plan PDF has no source IFC in the preview, so its 24 model-level questions fall back.
- A few swiss_dwellings apartment and total areas are off because unnamed numbered rooms (bathrooms vs shafts) are classified by an area heuristic.
- Image tokens don't seem to show up in `prompt_tokens`, so vision cost is under-reported.
