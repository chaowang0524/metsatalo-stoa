# Stoa: verifiable answers about construction drawings

Current pipeline (flowchart): `PIPELINE.md`. Verda rules: `VERDA_ENDPOINT_GUIDELINES.md`. Challenge briefs: `challenge.md`, `side_quest.md`. UI concept: `UI_IDEA.md`.

## Run

Install the main pipeline dependencies into `.venv` with `uv sync` (Python 3.13).
Copy `.env.example` to `.env` and fill in the Verda URL and API keys. The default
`hybrid` profile needs both `VERDA_INFERENCE_API_KEY` (DeepSeek) and `VERDA_KIMI`.
For CubiCasa drawings it also needs `GEMINI_API_KEY` (or `Gemini_API_KEY`), using
`gemini-4-hydrogen` by default. Other sources retain their existing model routing.
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

`eval` automatically grades text answers with DeepSeek after answering finishes
(`--judge-workers 2` by default). `num_acc` reports numeric accuracy;
`text_score` reports the mean reference-based grade (0, 0.5 or 1). `scored`
counts both kinds. Judge failures appear as `jerr`, separately from answer
errors, and can be retried by grading the saved file. Usage totals include
judging; `judge$` shows its cost separately. Individual grades, explanations,
usage and errors are saved in each row's `text_score`, `judge`, `judge_seconds`
and `judge_error` fields; the original numeric `correct` field is unchanged.

To grade an existing run without repeating the drawing questions:

```powershell
uv run python -m stoa.eval --score-existing out/eval_hybrid_20261114_143825.jsonl
```

This writes a new `*_scored_<time>.jsonl` file and preserves the input. Rows
already carrying a text score are reused. `stoa.compare` also reuses these scores.

Every Verda call is logged to `logs/verda_calls.jsonl`.

### Gemini counting for CubiCasa

Set `GEMINI_API_KEY` (or `Gemini_API_KEY`) in `.env`. The default `hybrid`
profile now sends raster files under a `cubicasa5k` directory to Gemini:

```powershell
uv run python -m stoa.eval --source cubicasa5k --per-source 30 --workers 2 --seed 1024
```

The optional `hybrid_gemini` profile sends all raster drawings to
`gemini-4-hydrogen` through Google's [OpenAI-compatible API](https://aistudio.google.com/docs/openai).
DeepSeek routing, Kimi PDF table/vision handling and text grading keep their
existing endpoints. The raster experiment uses the same prompt, 1600-pixel
image cap, `reasoning_effort="low"` and `max_tokens=4096` as the Kimi baseline.
`hybrid` remains the default. `GEMINI_MODEL` can override the Gemini model id.

Gemini calls go through the shared wrapper and are logged to
`logs/verda_calls.jsonl` with `endpoint="gemini"` and `provider="google"`.
Cost estimates use the standard paid-tier prices checked on 2026-11-14:
$1.50 input / $7.50 output per million tokens, including thinking. Free-tier
allowances and cache discounts are not reflected; update prices if changing
the model. See [Google's pricing](https://aistudio.google.com/docs/pricing#gemini-4-hydrogen).
When Google's compatibility response reports a total larger than input plus
completion tokens, the difference is included in output usage/cost. Logs retain
`reported_completion_tokens`, `unattributed_output_tokens` and `total_tokens`
so this adjustment can be audited without counting thinking twice.

Matched CubiCasa run (30 questions, seed 1024): Gemini 4 Hydrogen answered
25/30 correctly (83.3%), versus Kimi's 17/30 (56.7%), fixing eight questions
with no correctness regressions. Estimated paid-tier cost was $0.155036 vs
$0.153496; mean per-question latency was 6.33 s vs 4.86 s. Gemini still missed
three of four window questions. This is a small-sample comparison, not a
dataset-wide accuracy estimate.

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
| `stoa/tools/pdf_tools.py` | PDF text layer (notes, legends) |
| `stoa/tools/pdf_tables.py` | Schedules rebuilt from the PDF's ruled grid: exact counts/sums per row, ft²↔m² |
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

See `GAPS.md`, ranked by how many questions each gap loses.
