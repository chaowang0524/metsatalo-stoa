# Verda endpoint guidelines

We are entering the Stoa challenge and Verda's **"Best use of Verda"** bounty (`side_quest.md`). Every call to the Verda endpoint counts as evidence for both:

- **Verda bounty** (25% each): ambition, fit (right Verda products, used well), working prototype with a **documented process**, path to scale. We also have to post a summary link in Verda's Discord.
- **Stoa**: **low inference cost** is 25% of the score.

## 1. Send every call through one wrapper

Don't call `client.chat.completions.create` directly. Use a shared wrapper that appends one line per call to `logs/verda_calls.jsonl` with:

- timestamp, model, and **purpose** (e.g. `"room-count fallback"`, `"question-type classifier"`)
- `prompt_tokens`, `completion_tokens`, latency, and estimated cost
- question id or path, and whether the answer turned out correct once checked against `questions.jsonl`

That log gives us the cost per question for Stoa, the timeline for our process write-up, and real numbers to extrapolate from in the scale story.

## 2. Keep LLM calls cheap and few

- Use deterministic extraction (IFC queries, PDF/SVG vector geometry) first, and call the LLM only as a fallback or a router.
- Set a tight `max_tokens`.
- The text may come back in `reasoning_content` instead of `content`, and reasoning tokens still cost money.

## 3. Record why we picked each Verda product as we go

Add one line per decision to `VERDA_LOG.md`, e.g. *"Inference API for routing because…"* or *"tried model X → accuracy Y, cost Z"*.

So far we only use the Inference API, which is our weakest "fit" story. Products that would strengthen it:

- **Batch jobs**: run the full eval set, or the full ~120k questions, in the background.
- **A GPU instance**: fine-tune a small model (question parser, cubicasa room counter) on the dataset. "Compute let us do what a laptop couldn't" is exactly what the bounty rewards.
- **Auto-scaling containers**: serve that small model. This is our path-to-scale answer.

## 4. Keep secrets out

Read `VERDA_INFERENCE_BASE_URL` and `VERDA_INFERENCE_API_KEY` from the environment, as `inference.py` does. Never log, print or commit keys or `secreat.toml`.

## 5. The answering pipeline

Users don't know about our dataset. They hand us **a drawing plus a question**, in any language. The LLM's job is small: understand the question and pick a tool. Code does the answering.

```
drawing + question
   │
   ├─ code: inspect file → which tools apply        (free)
   ├─ LLM: question → tool call                     (small, cheap)
   ├─ code: run tool → exact answer + evidence      (free, verifiable)
   └─ fallback: vision LLM, only if no tool fits    (expensive, rare)
```

**Step 1: inspect the file (no LLM).** The extension and contents decide which tools are available:

| File | What we can read | Tools |
|---|---|---|
| `.ifc` | BIM objects (walls, doors, spaces, storeys) with geometry and properties | IFC queries: counts, areas, volumes, lengths |
| PDF rendered from an IFC (`<model>_<plan>.pdf`) | the source `.ifc` | same IFC queries |
| `.dxf`, or a PDF with a sibling `.dxf` | CAD layers: room labels, outlines, doors, windows, fixtures | vector counts and areas |
| PDF with a text layer | text and tables | text and schedule lookups |
| `.png` / `.jpg` / scanned PDF | pixels only | vision fallback (later: a small detector fine-tuned on Verda GPUs) |

**Step 2: interpret the question (cheap LLM call).** Send the question plus *only the tools that apply to this file*, using function calling. The LLM doesn't decide whether a deterministic answer exists. It picks a tool, and if none fits it picks `ask_vision_model`.

```
Question: "Kuinka monta saunaa pohjapiirustuksessa on?"  (Finnish: how many saunas are on the floor plan?)
File: plan.png (raster)
Tools: ask_vision_model(question)
```

**Step 3: run the tool.** Every tool returns the value **and the evidence**: which IFC objects (GlobalIds), which polygons or text labels, and which page. The UI highlights that evidence on the drawing so a human can check the answer in seconds.

**Step 4: fallback.** Only when no deterministic tool fits, render the drawing and ask the vision model. Mark these answers as lower confidence.

In the eval set, the "drawing" is each question's `path`, so the same pipeline serves both the eval and real users.
