"""The answering pipeline (see VERDA_ENDPOINT_GUIDELINES.md, section 5).

    drawing + question
       ├─ inspect file → which tools apply        (code, free)
       ├─ LLM: question → tool call               (one small call)
       ├─ run tool → exact answer + evidence      (code, free)
       └─ vision fallback if no tool fits         (expensive, rare)

Terminal tools return the final answer directly, so a typical question costs one
LLM call. Info tools (model facts, room tables, page text) go back to the LLM,
which then composes an answer.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from stoa import verda
from stoa.answer import Answer, ToolError, parse_llm_answer
from stoa.inspect_file import FileInfo, inspect_file
from stoa.tools import dxf_tools, ifc_tools, pdf_tools, vision_tools

MAX_TURNS = 4


@dataclass(frozen=True)
class Profile:
    """Which Verda endpoint does each job."""

    route: str  # picks tools, composes answers from IFC/DXF tables
    table: str  # composes answers from PDF schedule text (pdf_page_text)
    vision: str  # reads rendered drawings
    vision_effort: str | None = None  # None inherits the endpoint's default effort


# Benchmark on 200 questions (VERDA_LOG.md, 2026-11-14): deepseek routes
# reliably and cheaply, but kimi reads drawings (cubicasa 70% vs 45%) and
# schedules (opening_design text 55% vs 27%) better. "hybrid" combines them.
PROFILES = {
    "deepseek": Profile("deepseek", "deepseek", "deepseek"),
    "kimi": Profile("kimi", "kimi", "kimi"),
    "nemotron": Profile("nemotron", "nemotron", "nemotron"),
    # Default effort ran a median 4.5k reasoning tokens per drawing and hit the
    # 12k cap on 25% of calls; "low" uses ~250.
    "hybrid": Profile(route="deepseek", table="kimi", vision="kimi", vision_effort="low"),
}
ROUTER = os.environ.get("STOA_ROUTER", "hybrid")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    source: str  # which FileInfo attribute the tool reads: "ifc" | "dxf" | "pdf" | "image"
    fn: Callable
    terminal: bool = True

    def spec(self) -> dict:
        props = {
            k: {kk: vv for kk, vv in v.items() if kk != "_optional"}
            for k, v in self.parameters.items()
        }
        required = [k for k, v in self.parameters.items() if not v.get("_optional")]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        }


def _s(desc: str, optional: bool = False, **kw) -> dict:
    d = {"type": "string", "description": desc, **kw}
    if optional:
        d["_optional"] = True
    return d


STOREY = "Storey/level name exactly as written in the question, e.g. '1.korrus'. Omit for the whole model."

TOOLS = [
    Tool(
        "ifc_count",
        "Count BIM elements of one IFC class, optionally on one storey. "
        "Rooms are IfcSpace, sanitary fixtures are IfcSanitaryTerminal.",
        {"ifc_class": _s("IFC class", enum=ifc_tools.COUNTABLE), "storey": _s(STOREY, True)},
        "ifc", ifc_tools.ifc_count,
    ),
    Tool(
        "ifc_sum_quantity",
        "Sum a quantity over all elements of one IFC class, optionally on one storey. "
        "E.g. total net floor area of all rooms = IfcSpace + net_floor_area; "
        "total wall length = IfcWall + length; slab volume = IfcSlab + net_volume.",
        {
            "ifc_class": _s("IFC class", enum=ifc_tools.COUNTABLE),
            "quantity": _s("quantity", enum=list(ifc_tools.QUANTITIES)),
            "storey": _s(STOREY, True),
        },
        "ifc", ifc_tools.ifc_sum_quantity,
    ),
    Tool(
        "ifc_space_area",
        "Net floor area of ONE named room (IfcSpace).",
        {
            "space_name": _s("Room name exactly as in the question, e.g. 'ESIK (9-2)'"),
            "storey": _s(STOREY, True),
        },
        "ifc", ifc_tools.ifc_space_area,
    ),
    Tool(
        "ifc_list_elements",
        "Table of elements of one IFC class (name, type, storey, materials, Pset_*Common "
        "properties such as IsExternal or FireRating, width/height, length, volume). "
        "Use for questions about properties, materials, sizes or ratings; you then write the answer.",
        {"ifc_class": _s("IFC class", enum=ifc_tools.COUNTABLE), "storey": _s(STOREY, True)},
        "ifc", ifc_tools.ifc_list_elements, terminal=False,
    ),
    Tool(
        "ifc_model_facts",
        "Overview of the BIM model (storeys, element counts, room names). "
        "Use for open-ended questions; you then write the answer.",
        {}, "ifc", ifc_tools.ifc_model_facts, terminal=False,
    ),
    Tool(
        "dxf_count_symbols",
        "Count symbols drawn on the CAD floor plan: doors, windows, toilets (WC), "
        "washbasins (lavabo), bathtubs, showers, apartments (units, Wohnungen), "
        "or all numbered rooms.",
        {"kind": _s("symbol kind", enum=dxf_tools.SYMBOLS)},
        "dxf", dxf_tools.dxf_count_symbols,
    ),
    Tool(
        "dxf_count_rooms",
        "Count rooms whose name label equals a given label, e.g. 'Zimmer', 'Cuisine'.",
        {"label": _s("Room label exactly as quoted in the question, in the original language")},
        "dxf", dxf_tools.dxf_count_rooms,
    ),
    Tool(
        "dxf_room_area",
        "Net floor area of ONE room, by room number (e.g. '2.10') or unique name.",
        {"room": _s("Room number if given, else the room name")},
        "dxf", dxf_tools.dxf_room_area,
    ),
    Tool(
        "dxf_total_area",
        "Sum of numbered room areas on the plan. Filter by room label and/or apartment "
        "letter. Outdoor spaces (balconies, loggias, terraces) are excluded unless "
        "include_outdoor is true.",
        {
            "label": _s("Only rooms with this name label", True),
            "apartment": _s("Apartment letter, e.g. 'A'", True),
            "include_outdoor": {"type": "boolean", "description": "Include balconies etc.", "_optional": True},
        },
        "dxf", dxf_tools.dxf_total_area,
    ),
    Tool(
        "dxf_room_list",
        "Table of all rooms (number, name, area, apartment). For open-ended questions; "
        "you then write the answer.",
        {}, "dxf", dxf_tools.dxf_room_list, terminal=False,
    ),
    Tool(
        "pdf_page_text",
        "Text of a PDF sheet, with table rows on one line. Use for schedules, legends "
        "and notes; you then compute the answer from the text.",
        {"page": {"type": "integer", "description": "0-based page", "_optional": True}},
        "pdf", pdf_tools.pdf_page_text, terminal=False,
    ),
    Tool(
        "ask_vision_model",
        "Fallback: a vision model looks at the drawing. Use only when no other tool fits "
        "(e.g. symbols or rooms that the vector data can't identify). Pass the question "
        "in English.",
        {"question": _s("The question, in English")},
        "image", vision_tools.ask_vision_model,
    ),
]

SYSTEM = """You answer questions about construction drawings and BIM models by calling tools.
{file}
Rules:
- If one tool call computes the answer, make just that call. Prefer deterministic tools; use ask_vision_model only if nothing else fits.
- For breakdowns or comparisons, call several tools in one turn, then combine their results.
- Questions can be in any language. Keep quoted names (rooms, storeys, labels) exactly as written.
- After an info tool (facts, room list, page text), work out the answer from its output and reply with JSON only:
  {{"answer": <number or short text>, "unit": <"count"|"m2"|"m3"|"m"|"ft2"|null>, "explanation": "<one sentence citing the rows or items used>"}}
  For counts and areas, "answer" must be a bare number."""


@dataclass
class Result:
    question: str
    path: str
    answer: Answer | None
    calls: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    router: str = ""

    def add_usage(self, usage: dict) -> None:
        self.llm_calls += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.cost_usd += usage.get("cost_usd", 0.0)

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "question": self.question,
            "answer": self.answer.summary(max_evidence=50) if self.answer else None,
            "calls": self.calls,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "error": self.error,
            "router": self.router,
        }


def tools_for(info: FileInfo) -> list[Tool]:
    have = {
        "ifc": info.ifc is not None,
        "dxf": info.dxf is not None,
        "pdf": info.pdf_text and info.path.suffix.lower() == ".pdf",
        "image": info.image is not None,
    }
    return [t for t in TOOLS if have[t.source]]


def _source_path(tool: Tool, info: FileInfo) -> str:
    return str({"ifc": info.ifc, "dxf": info.dxf, "pdf": info.path, "image": info.image}[tool.source])


def _run(tool: Tool, info: FileInfo, args: dict, ctx: dict, prof: Profile) -> Answer:
    if tool.name == "ask_vision_model":
        return tool.fn(_source_path(tool, info), args.get("question", ""), context=ctx,
                       endpoint=prof.vision, effort=prof.vision_effort)
    return tool.fn(_source_path(tool, info), **args)


def answer(path: str | Path, question: str, context: dict | None = None, router: str | None = None) -> Result:
    router = router or ROUTER
    prof = PROFILES[router]
    info = inspect_file(path)
    tools = tools_for(info)
    res = Result(question=question, path=str(path), answer=None, router=router)
    ctx = {"file": Path(path).name, **(context or {})}
    by_name = {t.name: t for t in tools}

    # Only the vision model applies (raster drawing): skip the routing call.
    if [t.name for t in tools] == ["ask_vision_model"]:
        res.answer = vision_tools.ask_vision_model(str(info.image), question, context=ctx,
                                                   endpoint=prof.vision, effort=prof.vision_effort)
        res.calls.append({"tool": "ask_vision_model", "args": {"question": question}})
        res.add_usage(res.answer.usage)
        return res
    if not tools:
        res.error = f"Unsupported file: {info.describe()}"
        return res

    messages = [
        {"role": "system", "content": SYSTEM.format(file=info.describe())},
        {"role": "user", "content": question},
    ]
    specs = [t.spec() for t in tools]
    evidence = []  # everything the tools looked at, for the composed answer
    for turn in range(MAX_TURNS):
        # Answers worked out from schedule text go to the table model.
        endpoint = prof.table if any(c["tool"] == "pdf_page_text" for c in res.calls) else prof.route
        last = turn == MAX_TURNS - 1
        if last:  # no more tools: answer from what has been gathered
            messages.append({"role": "user", "content": "No more tool calls. Reply with the JSON answer now."})
        resp = verda.chat(
            "route question" if turn == 0 else "compose answer",
            messages, context=ctx, endpoint=endpoint,
            max_tokens=verda.ENDPOINTS[endpoint].DEFAULTS.get("max_tokens", 800), temperature=0,
            **({} if last else {"tools": specs}),
        )
        res.add_usage(verda.usage_of(resp, endpoint))
        msg = resp.choices[0].message
        if not msg.tool_calls:
            data = parse_llm_answer(verda.text_of(resp))
            res.answer = Answer(
                value=data["answer"], unit=data.get("unit"),
                method="LLM answer from tool output: " + ", ".join(c["tool"] for c in res.calls),
                deterministic=False, evidence=evidence,
                note=str(data.get("explanation", ""))[:500],
            )
            return res
        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [tc.model_dump(include={"id", "type", "function"}) for tc in msg.tool_calls],
        })
        outputs = []
        for tc in msg.tool_calls:
            tool = by_name.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                args = {k: v for k, v in args.items() if v not in (None, "")}
                if tool is None:
                    raise ToolError(f"Unknown tool {tc.function.name!r}")
                res.calls.append({"tool": tool.name, "args": args})
                out = _run(tool, info, args, ctx, prof)
            except (ToolError, TypeError, json.JSONDecodeError) as e:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"ERROR: {e}"})
                continue
            if out.usage:
                res.add_usage(out.usage)
            outputs.append((tool, out))
            evidence += out.evidence
            payload = out.value if not tool.terminal else out.summary(max_evidence=20)
            content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:15000]})
        # The common case: the first call was a single deterministic tool. Its
        # result is the answer, with no second LLM call.
        if turn == 0 and len(msg.tool_calls) == 1 and outputs and outputs[0][0].terminal:
            res.answer = outputs[0][1]
            return res
    res.error = "no answer within turn limit"
    return res
