# Mestari: the on-site engineer's co-pilot

Our entry for the Junction 2026 Stoa challenge (brief in `challenge.md`).
Mestari is Finnish for the site foreman (*työmaamestari*), the person an
engineer turns to with a question. Every number it gives comes with the
evidence from the drawing.

Onsite engineers lose hours every day hunting through drawings and BIM models
for a number: how many windows are on this floor, what is the area of this
apartment, how thick is that wall. How much do these cost. General-purpose AI models often get these wrong, and nothing shows the engineer *why* they got a number. 

Mestari answers natural-language questions about a drawing or BIM model, in any
language. Every answer comes with the evidence it was computed from, such as an
IFC `GlobalId`, a DXF entity handle or a PDF table row, and that evidence is
highlighted on the plan. An engineer can check the answer in seconds and fix it
if it is wrong.

The design rule is **deterministic first**. If a drawing has vector or BIM data
(DXF, IFC, a PDF text layer), code reads the answer straight from the geometry,
and a cheap LLM only picks which tool to run. Vision models are used only for
raster scans, and their answers are marked as needing a human check. This keeps
answers exact and cheap. Better frontier models will improve the vision
fallback, but they won't replace the deterministic part.

## Try it: live demo

Open **https://65-108-33-73.sslip.io** and log in with user `reviewer` and
password `junction2026`.

The preview dataset's drawings are already loaded. "Ask", "3D" and image
uploads call paid models, so each click costs a few cents (image generation takes ~30s). Everything else runs
without an LLM.

A three-minute tour:

1. **Read the Facts tab.** Pre-computed counts and areas for the plan (rooms,
   doors, windows, fixtures and so on). **Hover a fact to highlight its objects
   on the plan, click it to list them, then click an object in the list to zoom
   to it. Click the same object again to zoom back out.**
2. **Edit live.** Add or remove objects right on the plan:
   - **click a component kind in the Facts panel, then click the plan to add one;**
   - **right-click an object to remove it** (or to reject a detected one as
     "not on site");
   - "▦ Library…" stamps real symbols collected from the CAD and IFC plans, at
     real-world size.

   Counts, highlights and the cost estimate update immediately, and ⌘Z undoes
   any edit.
3. **Human in the loop.** The engineer has the last word on every annotation.
   On raster scans the objects come from a vision model, which makes mistakes,
   and so do people. Edits are saved on the server per drawing, so the
   corrected plan is what the next person sees, and what Ask, Cost and Export
   use.
4. **Price it.** The "€ Cost" tab multiplies the plan's quantities by Finnish
   material and labour prices (excl. VAT). Each price links to its source
   (Taloon.com, K-Rauta, or published hourly rates). You can overwrite any
   quantity or price, then "Send quote…" freezes a quote for internal approval
   or for the client. Its `/quote/<id>` link lets the recipient approve it, ask
   for changes, print it or download a CSV.
5. **Share it.** "⤓ Export" saves the edited plan as PNG, JPEG or PDF (A3).
6. **Bring your own drawing.** "⤒ Upload" (or drag and drop) accepts one or many
   files:
   - DXF: exact CAD facts;
   - plan SVG + IFC: BIM facts;
   - PNG, JPG or a scanned PDF: one vision-model pass in the background (about
     1 minute, $0.10–0.18);
   - other PDFs: the same vision pass on page 1 for the components, plus exact
     rooms from the PDF text layer when it has room stamps; Ask still reads the
     PDF itself.

   See [Check the results yourself](#check-the-results-yourself) for how to use
   this to test us on your own drawings.
7. **Ask a question.** For further questions, type a question such as *"Are there enough doors for this plan?"* "Highlight references" shows only the
   evidence the answer used on the plan. "Sources and calculation details"
   shows whether the answer is **exact** (computed from vector or BIM data) or
   a **model answer · verify** (vision), plus the method, the tools called and
   the cost. Answers that include your plan edits are marked "✎ includes
   engineer corrections".

## Check the results yourself 

Try it yourself in our interactive UI. Take drawings from the dataset, upload
them in the live demo, and compare what the UI says with the reference answers
in `questions.jsonl`. The preview drawings are already loaded, so for a fair
test use drawings from the **full dataset** that we have not seen.

1. **Pick drawings.** Files with the same name and a different extension are
   the same drawing, so upload them together, the way the dataset ships them:

   | Source | Upload | What you get |
   |---|---|---|
   | swiss_dwellings | `<stem>.pdf` + `<stem>.dxf` | Exact facts from the CAD geometry, in seconds, no LLM |
   | ifc_models | `<model>.ifc` + `<model>_<level>.svg` (and the `.pdf`) | Exact facts from the BIM model, linked by `GlobalId` |
   | cubicasa5k | `<stem>.png` | One vision pass in the background (about 1 minute, ~$0.18), then checkable boxes |
   | opening_design, other PDFs | `<stem>.pdf` | One vision pass on page 1 for checkable component boxes, exact rooms from the text layer when it has room stamps, and Ask on the PDF itself |

2. **Upload.** Click "⤒ Upload" or drag files onto the page. You can drop many
   drawings at once, and each one appears in the drawing list when it is ready.
3. **Look up the reference answers.** `questions.jsonl` has one line per
   question, with `path`, `question`, `answer` and `unit`:

   ```bash
   grep '<stem>' questions.jsonl
   ```

4. **Compare.** Most count and area questions ("How many doors…", "What is the
   area of the kitchen…") can be read straight from the **Facts** tab. For any
   other question, use **Ask** as it is. Any language works, because the
   models that read the question are multilingual. 
5. **Check why.** Hover a fact or click "Highlight references" under an answer
   to see the objects it counted. If a number is wrong, you can usually see the
   cause on the plan, such as a missed window or a neighbouring flat counted
   too, and fix it with a click.


## Run the full evaluation (for reviewers who want to test the evaluation set)

```bash
uv run python -m stoa.eval --dataset /path/to/full-dataset
```

`--dataset` takes any folder laid out like `junction-dataset-preview/`: the
drawings under `data/<source>/`, and a `questions.jsonl` next to it with the
questions and their reference answers, one per line:

```json
{"path": "data/cubicasa5k/colorful-11397.png", "question": "How many windows are shown on this floor plan?", "answer": 13, "unit": "count", "type": "count"}
```

- `path` is relative to the dataset folder, and its second part (`cubicasa5k`)
  is the source the results are grouped by;
- `answer` is the reference that the run scores against: counts must match
  exactly, areas, lengths and volumes must be within 2%, and `text` answers are
  graded by an LLM judge;
- `type` is one of `count`, `area`, `length`, `volume` or `text`.

The run needs the setup and API keys from [Run it locally](#run-it-locally).
It writes every answer to `out/eval_<profile>_<time>.jsonl` and prints
accuracy, cost and latency per source at the end.

## Features

| Feature | What it gives the engineer |
|---|---|
| Grounded facts, at a glance | Engineers can component counts and room areas for every plan as they are pre-computed and straightforwardly shown on the plan. They come from tools first (DXF geometry, IFC queries, PDF tables), so every number points to the exact CAD entity, IFC element or table row it came from |
| Live editing on site | Engineers get to add, remove or stamp doors, windows, fixtures and other components on the plan. Counts update straight away |
| Cost estimate, faster | Engineers get the cost at once with quantities × cited Finnish material and labour prices which are also editable. "Send quote…" turns it into a quote with an approval link, so the estimate is ready minutes after the plan is checked |
| Human in the loop | The counting, model, the cost and even previous human annotations can be wrong. Every object stays visible and editable, edits are saved per drawing, and ⌘Z undoes them |
| Upload | Any DXF, IFC, PDF or scanned plan becomes a checkable plan |
| Cost logging | Every model call is logged with its tokens, latency and USD cost (`logs/verda_calls.jsonl`) |

## How it works

```
drawing + question
   │
   ├─ inspect_file (no LLM): what formats exist for this drawing? (DXF, IFC, PDF text, raster)
   │
   ├─ vector / BIM data ──► DeepSeek picks a tool ──► code computes the answer
   │                         (IFC queries, DXF geometry, PDF tables rebuilt from ruled lines)
   │
   └─ raster only ────────► vision model lists the objects and their locations ──► code counts the list
   │
   ▼
answer + unit + method + evidence + deterministic flag
```

- **IFC** (`stoa/tools/ifc_tools.py`): counts, quantity sums, room areas, type
  breakdowns and material layers, read from the model with IfcOpenShell.
- **DXF** (`stoa/tools/dxf_tools.py`): rooms, areas, apartments, doors,
  windows, fixtures and baseboard length, from the CAD entities.
- **PDF schedules** (`stoa/tools/pdf_tables.py`): door and window schedules
  rebuilt from the PDF's ruled grid, so totals are summed by code.
- **Raster plans**: Gemini 4 Hydrogen lists every requested object with its
  location, and code counts the list. This is still an estimate, so the UI shows
  the boxes for the engineer to check.

Models: DeepSeek routes and composes text answers ($0.15/$0.60 per M tokens),
Kimi reads PDF schedules and other images, and Gemini reads raster floor plans.
`PIPELINE.md` has the full flowchart, and `VERDA_LOG.md` records why each endpoint was chosen.

## Results

These come from small samples (30–64 questions per source), so a few points
either way is not significant.

| Source | Questions in preview | Accuracy | Cost per question | How |
|---|---|---|---|---|
| swiss_dwellings | 2358 | 97.5% | $0.0002 | DXF geometry |
| ifc_models | 1049 | 100% (excluding `kit-fzk-haus`, which has no IFC) | $0.0003 | IFC queries |
| opening_design | 64 | 47/48 numeric (the one miss was a rate-limit error) | $0.0045 | Schedules rebuilt from the PDF's ruled grid |
| cubicasa5k | 4270 | 25/30 (83%) with Gemini | ~$0.005 | Raster vision |
| ifc_bench | 249 | text answers, graded by `text_score` | $0.0012 | IFC tools + LLM composition |

Sources: `GAPS.md` (hybrid profile, 40 questions per source, seed 0) and
`VERDA_LOG.md` (CubiCasa, Gemini vs Kimi, 30 questions, seed 1024). Scoring
follows the challenge: counts must match exactly, and areas, lengths and
volumes must be within 2%.

## Run it locally

Python 3.13, with dependencies managed by `uv`:

```bash
uv sync
cp .env.example .env        # then fill in the keys
```

| Variable | Used for |
|---|---|
| `VERDA_INFERENCE_BASE_URL`, `VERDA_INFERENCE_API_KEY` | DeepSeek (routing, answer composition, text grading) |
| `VERDA_KIMI` | Kimi (PDF schedules, other vision) |
| `GEMINI_API_KEY` | Gemini 4 Hydrogen for raster plans, and the 3D render |
| `STOA_ROUTER` | Model profile, default `hybrid`. Use `deepseek` if you only have the DeepSeek key |

The `.env` value takes precedence over an exported `GEMINI_API_KEY`. The
dataset goes in `junction-dataset-preview/`, which is not in the repository.


## Quick evaluation from the command line

`stoa.eval` samples questions from a dataset's `questions.jsonl`, answers them,
scores them with the challenge rules and prints accuracy, cost and latency per
source. It needs the setup from [Run it locally](#run-it-locally), including the
API keys.

**Quick check (25 questions, about 30 seconds, about $0.12):**

```bash
uv run python -m stoa.eval --per-source 5             # add --seed N for a different sample
```

Our run on 2026-11-14:

```
source               n  scored  num_acc  text_score  llm/q   tok/q   $/1kq   judge$   s/q  err  jerr  det%
cubicasa5k           5       5    80.0%           -   1.00    1927   5.685  0.00000   4.6    0     0    0%
ifc_bench            5       5        -       60.0%   3.20   20122   3.223  0.00202  10.0    0     0   40%
ifc_models           5       5    80.0%           -   1.60    4594   4.041  0.00000   4.9    0     0   80%
opening_design       5       5   100.0%       87.5%   2.80    7812   9.773  0.00021   5.3    0     0   20%
swiss_dwellings      5       5   100.0%           -   1.00    2232   0.362  0.00000   5.5    0     0  100%
ALL                 25      25    87.5%       72.2%   1.92    7338   4.617  0.00224   6.1    0     0   48%
```

Five questions per source is a very small sample. Here the one ifc_models miss
is `kit-fzk-haus`, which has no IFC. Use `--per-source 40` for numbers you can
compare.

- `num_acc`: share of numeric answers that are correct (counts exact; areas,
  lengths and volumes within 2%).
- `text_score`: the mean grade of free-text answers. DeepSeek grades each one
  against the reference as 0, 0.5 or 1.
- `$/1kq`: the cost per 1,000 questions, including grading.
- `det%`: share of answers computed by code, not by a vision model.

Each answer, with its evidence, is saved to `out/eval_<profile>_<time>.jsonl`.

**Reproduce the [Results](#results) table:**

```bash
uv run python -m stoa.eval --per-source 40 --seed 0                     # all sources (GAPS.md run)
uv run python -m stoa.eval --source cubicasa5k --per-source 30 --seed 1024  # cubicasa, 25/30
uv run python -m stoa.eval --per-source 50 --seed 123 --type text       # text questions only
```

**Test on your own or held-out data:** point `--dataset` at any folder in the
dataset's layout (`questions.jsonl` plus `data/<source>/...`), for example the
full dataset:

```bash
uv run python -m stoa.eval --dataset path/to/junction-dataset --per-source 20 --seed 7
```

Other options:

| Option | Effect |
|---|---|
| `--source <name>` | One source only, e.g. `swiss_dwellings` |
| `--skip a,b` | Leave sources out (the rest keep the same questions) |
| `--type count\|area\|text\|...` | One question type, chosen after sampling so it matches a full run |
| `--limit N` | N random questions instead of N per source |
| `--router deepseek` | Run with only the DeepSeek key (other profiles below) |
| `--score-existing <file>` | Re-grade saved answers without answering again |

- `uv run python -m stoa.compare a.jsonl b.jsonl` compares two runs on their
  shared questions. Use the same `--seed` and `--per-source` for both runs.
- `uv run python -m unittest discover -s tests -v` runs the regression tests.
- Keep `--workers` (default 8) at 8 or fewer, because the Verda gateway
  rate-limits above that.

### Model profiles

| Profile | Routing | PDF schedules | Raster drawings |
|---|---|---|---|
| `hybrid` (default) | DeepSeek | Kimi | Gemini for `cubicasa5k`, Kimi otherwise |
| `hybrid_gemini` | DeepSeek | Kimi | Gemini for all |
| `deepseek` | DeepSeek | DeepSeek | DeepSeek |

Vision calls use low reasoning effort, a 4096-token budget and a 1600 px image
cap. `GEMINI_MODEL` overrides the Gemini model id.

### Raster detection cache

The CubiCasa plans in the UI use one Gemini full-plan detection per drawing,
cached in CubiCasa class names with pixel bounding boxes:

```bash
uv run python -m stoa.cubicasa_gemini --workers 8    # writes out/cubicasa_gemini_cache/<stem>.json
```

Valid results are reused on restart (`--overwrite` reruns them). Raw responses,
hashes and the usage of every attempt are kept under `_audit/`. Truncated
responses are retried with a larger budget.

## Repository layout

| Path | Role |
|---|---|
| `stoa/inspect_file.py` | File type and sibling IFC/DXF → which tools apply (no LLM) |
| `stoa/pipeline.py` | Router LLM picks a tool, code answers, vision fallback; model profiles |
| `stoa/answer.py` | `Result` / `Evidence` types shared by every tool |
| `stoa/tools/` | IFC, DXF, PDF text, PDF tables and vision tools |
| `stoa/verda.py` | The only LLM client; logs every call |
| `endpoints/verda/*.py`, `endpoints/gemini.py` | One module per endpoint: model id, prices, defaults |
| `stoa/ask.py` | CLI: one question about one drawing |
| `stoa/eval.py`, `stoa/judge.py`, `stoa/compare.py` | Evaluation, text grading, run comparison |
| `stoa/ui/` | Web UI: `facts.py` (pre-compute), `server.py`, `uploads.py`, `library.py`, `render.py` (3D), `static/` |
| `stoa/cubicasa_probe.py` | Compares the pipeline with the CubiCasa5K CNN on test/val drawings |


## Data licences

Drawings come from the Junction dataset. Licences are per source in
`data/<source>/LICENSE.md`. Keep the credit when displaying a drawing.
cubicasa5k is CC BY-NC-SA.
