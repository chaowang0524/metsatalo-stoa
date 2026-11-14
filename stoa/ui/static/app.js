// Stoa Plan Check: pre-computed facts over the plan, hover to verify, edit, ask, render, share.
"use strict";

const NS = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);
const COLORS = {
  window: "#1c7ed6", door: "#e8590c", toilet: "#0ca678", washbasin: "#15aabf", sink: "#15aabf",
  bathtub: "#7048e8", shower: "#ae3ec9", apartment: "#f59f00", room: "#d6336c", closet: "#868e96",
  appliance: "#5c940d", sauna: "#a61e4d", fireplace: "#c92a2a", chimney: "#495057", answer: "#e8590c",
  stair: "#5f3dc4", wall: "#495057", column: "#862e9c", railing: "#868e96", furniture: "#a9743a",
  sanitary: "#0ca678", slab: "#adb5bd", member: "#a9743a", beam: "#862e9c",
};
const color = (kind) => COLORS[kind] || "#e8590c";

const svg = $("#plan");
const state = {
  doc: null, edits: { added: [], rejected: [] }, history: [],
  view: { x: 0, y: 0, w: 1, h: 1 },
  pinned: null, hover: null, single: null, // fact ids / item key under the pointer
  adhoc: null, // an answer's evidence, shown like a fact
  editing: false, addKind: null,
};

// ---------- helpers ----------
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  for (const k of kids) e.append(k);
  return e;
}
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), ms);
}
function bboxOf(shape) {
  if (shape.bbox) return shape.bbox;
  const xs = shape.poly.map((p) => p[0]), ys = shape.poly.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
const center = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
const fmt = (v, unit) => unit === "m2" ? `${Number(v).toFixed(2)} m²` : unit === "count" || unit == null ? `${v}` : `${v} ${unit}`;
const keyOf = (fact, item) => `${fact.kind}:${item.ref}`;

// Number items the way a person counts: row by row, left to right.
function ordered(items) {
  const rowH = (state.doc.view.height || 1000) / 25;
  return items.slice().sort((a, b) => {
    const [ax, ay] = center(bboxOf(a.shape)), [bx, by] = center(bboxOf(b.shape));
    return Math.round(ay / rowH) - Math.round(by / rowH) || ax - bx;
  });
}

// Items of a fact with the engineer's edits applied (rejected flagged, additions appended).
function itemsOf(fact) {
  if (fact.id === "__answer") return fact.items;
  const rej = new Set(state.edits.rejected);
  const items = fact.items.map((it) => ({ ...it, rejected: rej.has(keyOf(fact, it)) }));
  if (fact.group === "Components") {
    for (const a of state.edits.added.filter((a) => a.kind === fact.kind))
      items.push({ ref: a.id, label: `${fact.kind} (added)`, shape: { bbox: a.bbox }, added: true });
  }
  return ordered(items);
}
function valueOf(fact) {
  if (fact.unit !== "count") return fact.value;
  return itemsOf(fact).filter((i) => !i.rejected).length;
}
const factById = (id) => id === "__answer" ? state.adhoc : state.doc.facts.find((f) => f.id === id);

// ---------- loading ----------
async function loadIndex() {
  const list = await (await fetch("/api/drawings")).json();
  const picker = $("#picker");
  const groups = {};
  for (const d of list) {
    const g = groups[d.source] || (groups[d.source] = h("optgroup", { label: d.source }));
    const s = d.summary;
    const tag = [s.window != null && `${s.window} win`, s.door != null && `${s.door} doors`].filter(Boolean).join(", ");
    g.append(h("option", { value: d.id, textContent: `${d.file}  ·  ${tag}` }));
  }
  for (const src of ["ifc_models", "swiss_dwellings", "cubicasa5k"]) if (groups[src]) picker.append(groups[src]);
  for (const g of Object.values(groups)) if (!g.parentNode) picker.append(g);
  if (!list.length) { toast("No facts yet: run  uv run python -m stoa.ui.facts", 10000); return; }
  const want = decodeURIComponent(location.hash.slice(1));
  picker.value = list.some((d) => d.id === want) ? want : list[0].id;
  await loadDoc(picker.value);
}

async function loadDoc(id) {
  const doc = await (await fetch(`/api/drawings/${id}`)).json();
  state.doc = doc; state.edits = doc.edits; state.history = [];
  state.pinned = state.hover = state.single = state.adhoc = null;
  history.replaceState(null, "", `#${id}`);
  const v = doc.view;
  const bg = $("#bg");
  bg.setAttribute("href", v.background);
  bg.setAttribute("width", v.width); bg.setAttribute("height", v.height);
  $("#credit").textContent = doc.credit;
  $("#doc-title").textContent = doc.file;
  const det = doc.facts.every((f) => f.deterministic);
  const basis = doc.source === "ifc_models" ? (doc.ifc ? "exact · from BIM model" : "exact · from plan SVG") : "exact · from CAD geometry";
  $("#doc-meta").replaceChildren(
    h("span", { className: `badge ${det ? "det" : "model"}`, textContent: det ? basis : "detector · verify on plan" }),
    doc.storey ? h("span", { textContent: `storey ${doc.storey}` }) : "",
    h("span", { className: "badge free", textContent: "pre-computed · 0 LLM tokens" }),
    h("span", { textContent: doc.source }),
  );
  $("#thread").replaceChildren();
  buildPalette();
  fit();
  renderFacts();
}

// ---------- fact panel ----------
function renderFacts() {
  const box = $("#facts");
  const scroll = box.scrollTop;
  box.replaceChildren();
  const groups = {};
  for (const f of state.doc.facts) (groups[f.group] ||= []).push(f);
  for (const [name, facts] of Object.entries(groups)) {
    const g = h("div", { className: "group" }, h("h2", { textContent: name }));
    for (const f of facts) g.append(...factRow(f));
    box.append(g);
  }
  box.scrollTop = scroll;
  drawAll(); drawHighlight();
}

function factRow(f) {
  const val = valueOf(f);
  const changed = f.unit === "count" && val !== f.value;
  const row = h("div", {
    className: `fact${state.pinned === f.id ? " pinned" : ""}${val === 0 ? " zero" : ""}`,
    tabIndex: 0,
  });
  const valEl = h("span", { className: "val" });
  if (changed) valEl.append(h("span", { className: "was", textContent: f.value }));
  valEl.append(f.unit === "m2" ? h("span", {}, Number(val).toFixed(2), h("small", { textContent: " m²" })) : `${val}`);
  row.append(
    h("span", { className: "swatch", style: `background:${color(f.kind)}` }),
    h("span", { textContent: f.label }),
    valEl,
    h("span", { className: "flag", title: f.deterministic ? "exact, from the drawing's source data" : "model detection: please verify",
      textContent: changed ? "✎" : f.deterministic ? "✓" : "⚠" }),
  );
  row.onmouseenter = () => { state.hover = f.id; drawHighlight(); row.classList.add("hl"); };
  row.onmouseleave = () => { state.hover = null; drawHighlight(); row.classList.remove("hl"); };
  row.onclick = () => { state.pinned = state.pinned === f.id ? null : f.id; state.single = null; renderFacts(); };
  row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.onclick(); } };
  if (state.pinned !== f.id) return [row];
  return [row, h("div", { className: "method", textContent: `Method: ${f.method}` }), itemList(f)];
}

function itemList(f) {
  const ul = h("ul", { className: "items" });
  let n = 0;
  for (const it of itemsOf(f)) {
    const li = h("li", { className: it.rejected ? "rej" : it.added ? "add" : "" },
      h("span", { className: "n", textContent: it.rejected ? "✕" : `${++n}` }),
      h("span", { textContent: it.label + (it.rejected ? " (rejected)" : "") }),
      h("span", { className: "v", textContent: it.value != null ? fmt(it.value, typeof it.value === "number" ? "m2" : null) : "" }));
    li.onmouseenter = () => { state.single = it.ref; drawHighlight(); };
    li.onmouseleave = () => { state.single = null; drawHighlight(); };
    li.onclick = () => zoomTo(bboxOf(it.shape));
    ul.append(li);
  }
  if (!n && !ul.children.length) ul.append(h("li", { className: "muted", textContent: "None on this plan." }));
  return ul;
}

// ---------- plan overlay ----------
function pad(b, p) { return [b[0] - p, b[1] - p, b[2] + p, b[3] + p]; }
function shapeEl(shape, attrs, parent) {
  const pp = state.doc.view.width / 400; // keep thin symbols (windows) visible
  if (shape.poly) return el("polygon", { points: shape.poly.map((p) => p.join(",")).join(" "), ...attrs }, parent);
  const b = pad(shape.bbox, pp);
  return el("rect", { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], rx: pp, ...attrs }, parent);
}
const unitsPerPx = () => state.view.w / Math.max(svg.clientWidth, 1);

// Edit mode: every component faintly, clickable to reject. Additions: always shown.
function drawAll() {
  const all = $("#layer-all"), ed = $("#layer-edits");
  all.replaceChildren(); ed.replaceChildren();
  if (state.editing) {
    const rej = new Set(state.edits.rejected);
    for (const f of state.doc.facts.filter((f) => f.group === "Components")) {
      for (const it of f.items) {
        const key = keyOf(f, it);
        shapeEl(it.shape, { class: `ov ov-all${rej.has(key) ? " rej" : ""}`, fill: color(f.kind), stroke: color(f.kind),
          "data-key": key, style: "cursor:pointer" }, all);
      }
    }
  }
  for (const a of state.edits.added) {
    shapeEl({ bbox: a.bbox }, { class: "ov ov-add", fill: color(a.kind), stroke: color(a.kind),
      "data-added": a.id, style: state.editing ? "cursor:pointer" : "" }, ed);
  }
}

function drawHighlight() {
  const layer = $("#layer-hl");
  layer.replaceChildren();
  const f = factById(state.hover || state.pinned);
  if (!f) return;
  const c = color(f.kind);
  const k = unitsPerPx();
  const r = 11 * k;
  let n = 0;
  for (const it of itemsOf(f)) {
    const one = state.single === it.ref;
    if (it.rejected) {
      shapeEl(it.shape, { class: "ov ov-hl", fill: "#c92a2a", stroke: "#c92a2a", "stroke-dasharray": "4 3" }, layer);
      continue;
    }
    n++;
    shapeEl(it.shape, { class: `ov ov-hl${one ? " one" : ""}`, fill: c, stroke: c }, layer);
    const b = bboxOf(it.shape);
    const [cx, cy] = it.shape.poly ? center(b) : [b[0], b[1]];
    el("circle", { cx, cy, r: one ? r * 1.3 : r, fill: c, class: "badge-c" }, layer);
    el("text", { x: cx, y: cy, "font-size": (n > 99 ? 9 : 12) * k * (one ? 1.3 : 1), class: "badge-t" }, layer).textContent = n;
  }
}

// ---------- pan / zoom ----------
function setView(v) {
  state.view = v;
  svg.setAttribute("viewBox", `${v.x} ${v.y} ${v.w} ${v.h}`);
  drawHighlight();
}
function fit() {
  const { width, height } = state.doc.view;
  const ar = svg.clientWidth / Math.max(svg.clientHeight, 1);
  let w = width * 1.04, hh = height * 1.04;
  if (w / hh < ar) w = hh * ar; else hh = w / ar;
  setView({ x: width / 2 - w / 2, y: height / 2 - hh / 2, w, h: hh });
}
function zoomAt(px, py, factor) {
  const v = state.view;
  const p = toView(px, py);
  const w = v.w * factor, hh = v.h * factor;
  setView({ x: p.x - (p.x - v.x) * factor, y: p.y - (p.y - v.y) * factor, w, h: hh });
}
function zoomTo(b) {
  const ar = svg.clientWidth / Math.max(svg.clientHeight, 1);
  let w = Math.max(b[2] - b[0], state.doc.view.width / 12) * 2.5, hh = w / ar;
  setView({ x: (b[0] + b[2]) / 2 - w / 2, y: (b[1] + b[3]) / 2 - hh / 2, w, h: hh });
}
function toView(clientX, clientY) {
  const pt = svg.createSVGPoint();
  pt.x = clientX; pt.y = clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

const pointers = new Map();
let drag = null;
svg.addEventListener("pointerdown", (e) => {
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  svg.setPointerCapture(e.pointerId);
  drag = { x: e.clientX, y: e.clientY, view: { ...state.view }, moved: 0, target: e.target };
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    drag.pinch = Math.hypot(a.x - b.x, a.y - b.y);
  }
});
svg.addEventListener("pointermove", (e) => {
  if (!pointers.has(e.pointerId) || !drag) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 2 && drag.pinch) {
    const [a, b] = [...pointers.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y);
    zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, drag.pinch / d);
    drag.pinch = d; drag.moved = 99;
    return;
  }
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
  if (drag.moved > 4) {
    svg.classList.add("dragging");
    const k = state.view.w / svg.clientWidth;
    setView({ ...drag.view, x: drag.view.x - dx * k, y: drag.view.y - dy * k });
  }
});
function endPointer(e) {
  pointers.delete(e.pointerId);
  svg.classList.remove("dragging");
  if (drag && drag.moved <= 4 && pointers.size === 0 && e.type === "pointerup") onPlanClick(e, drag.target);
  if (pointers.size === 0) drag = null;
}
svg.addEventListener("pointerup", endPointer);
svg.addEventListener("pointercancel", endPointer);
svg.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.clientX, e.clientY, Math.exp(e.deltaY * 0.0015)); }, { passive: false });
$("#z-in").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.x + r.width / 2, r.y + r.height / 2, 0.7); };
$("#z-out").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.x + r.width / 2, r.y + r.height / 2, 1 / 0.7); };
$("#z-fit").onclick = fit;
window.addEventListener("resize", () => state.doc && fit());

// ---------- editing ----------
function buildPalette() {
  const box = $("#palette-kinds");
  box.replaceChildren();
  for (const f of state.doc.facts.filter((f) => f.group === "Components")) {
    const b = h("button", { type: "button" }, h("span", { className: "swatch", style: `background:${color(f.kind)}` }), f.label.replace(/s$/, ""));
    b.setAttribute("aria-pressed", state.addKind === f.kind);
    b.onclick = () => { state.addKind = state.addKind === f.kind ? null : f.kind; buildPalette(); };
    box.append(b);
  }
  svg.classList.toggle("adding", state.editing && !!state.addKind);
}
function setEditing(on) {
  state.editing = on;
  $("#btn-edit").setAttribute("aria-pressed", on);
  $("#palette").hidden = !on;
  if (!on) state.addKind = null;
  buildPalette(); drawAll();
}
$("#btn-edit").onclick = () => setEditing(!state.editing);

function commit(mutate) {
  state.history.push(JSON.stringify(state.edits));
  mutate(state.edits);
  renderFacts(); saveEdits();
}
$("#btn-undo").onclick = () => {
  if (!state.history.length) return toast("Nothing to undo");
  state.edits = JSON.parse(state.history.pop());
  renderFacts(); saveEdits();
};
let saveT;
function saveEdits() {
  clearTimeout(saveT);
  saveT = setTimeout(() => fetch(`/api/edits/${state.doc.id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.edits),
  }).catch(() => toast("Could not save edits")), 400);
}

function defaultSize(kind) {
  const f = state.doc.facts.find((f) => f.kind === kind && f.group === "Components");
  const ws = [], hs = [];
  for (const it of f?.items || []) { const b = bboxOf(it.shape); ws.push(b[2] - b[0]); hs.push(b[3] - b[1]); }
  const med = (a) => a.sort((x, y) => x - y)[a.length >> 1];
  if (ws.length) return [Math.max(med(ws), med(hs)), Math.max(med(ws), med(hs))].map((s) => Math.max(s, state.doc.view.width / 80));
  const s = state.doc.view.units_per_m ? state.doc.view.units_per_m : state.doc.view.width / 30;
  return [s, s];
}

function onPlanClick(e, target) {
  if (!state.editing) return;
  const addedId = target?.getAttribute?.("data-added");
  if (addedId) {
    commit((ed) => { ed.added = ed.added.filter((a) => a.id !== addedId); });
    return toast("Removed your addition");
  }
  const key = target?.getAttribute?.("data-key");
  if (key && !state.addKind) {
    const was = state.edits.rejected.includes(key);
    commit((ed) => { ed.rejected = was ? ed.rejected.filter((k) => k !== key) : [...ed.rejected, key]; });
    return toast(was ? "Restored" : "Rejected: not counted any more");
  }
  if (!state.addKind) return toast("Pick an object type above, or tap a detected object to reject it");
  const p = toView(e.clientX, e.clientY);
  const [w, hh] = defaultSize(state.addKind);
  const kind = state.addKind;
  commit((ed) => ed.added.push({ id: `add-${Date.now()}`, kind, bbox: [p.x - w / 2, p.y - hh / 2, p.x + w / 2, p.y + hh / 2] }));
  if (!state.pinned) state.pinned = state.doc.facts.find((f) => f.kind === kind && f.group === "Components")?.id;
  renderFacts();
  toast(`Added 1 ${kind}`);
}

// ---------- ask ----------
$("#btn-ask").onclick = () => {
  $("#ask-form").hidden = false; $("#btn-ask").hidden = true; $("#ask-input").focus();
};
$("#ask-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#ask-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  const a = h("div", { className: "a" }, h("span", { className: "spinner" }), " Routing to a tool…");
  const qa = h("div", { className: "qa" }, h("div", { className: "q", textContent: q }), a);
  $("#thread").append(qa);
  qa.scrollIntoView({ block: "end" });
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.doc.id, question: q }) });
    if (!r.ok) throw new Error(await r.text());
    showAnswer(a, await r.json());
  } catch (err) {
    a.replaceChildren(h("span", { className: "muted", textContent: `Failed: ${err.message.slice(0, 200)}` }));
  }
  qa.scrollIntoView({ block: "end" });
};

function showAnswer(box, res) {
  const ans = res.answer;
  if (!ans) { box.replaceChildren(h("span", { className: "muted", textContent: res.error || "No answer" })); return; }
  const tools = res.calls.map((c) => c.tool).join(" → ") || "—";
  const cost = res.cost_usd ? `$${res.cost_usd.toFixed(4)}` : "$0";
  box.replaceChildren(
    h("div", { className: "big", textContent: typeof ans.value === "object" ? "see list" : fmt(ans.value, ans.unit) }),
    h("div", {}, h("span", { className: `badge ${ans.deterministic ? "det" : "model"}`,
      textContent: ans.deterministic ? "exact" : "model answer · verify" }), " ", ans.method),
    ans.note ? h("div", { className: "sub", textContent: ans.note }) : "",
    h("div", { className: "sub", textContent: `${tools} · ${res.llm_calls} LLM calls · ${res.prompt_tokens + res.completion_tokens} tokens · ${cost}` }),
  );
  if (res.items?.length) {
    const btn = h("button", { className: "small", textContent: `Show ${res.items.length} on plan` });
    btn.onclick = () => {
      state.adhoc = { id: "__answer", kind: "answer", items: ordered(res.items) };
      state.pinned = "__answer"; renderFacts();
    };
    box.append(btn);
  }
  if (res.unplaced) box.append(h("div", { className: "sub", textContent: `${res.unplaced} evidence items are not drawn on this plan.` }));
}

// ---------- export, render, share ----------
function loadImage(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
}

// The plan as PNG. annotate: draw the highlighted fact with numbers; forRender: label additions by kind.
async function planPNG({ maxSide = 2400, annotate = true, footer = true } = {}) {
  const v = state.doc.view;
  const s = Math.min(1, maxSide / Math.max(v.width, v.height)) || 1;
  const W = Math.round(v.width * s), H = Math.round(v.height * s);
  const foot = footer ? 70 : 0;
  const c = h("canvas", { width: W, height: H + foot });
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H + foot);
  g.drawImage(await loadImage(v.background), 0, 0, W, H);
  const box = (b, col, dash, label) => {
    g.save();
    g.strokeStyle = col; g.lineWidth = 3; g.setLineDash(dash ? [8, 5] : []);
    g.fillStyle = col + "33";
    g.fillRect(b[0] * s, b[1] * s, (b[2] - b[0]) * s, (b[3] - b[1]) * s);
    g.strokeRect(b[0] * s, b[1] * s, (b[2] - b[0]) * s, (b[3] - b[1]) * s);
    if (label) { g.fillStyle = col; g.font = "bold 16px Helvetica, Arial"; g.fillText(label, b[0] * s, b[1] * s - 4); }
    g.restore();
  };
  for (const a of state.edits.added) box(a.bbox, color(a.kind), true, annotate ? `+${a.kind}` : a.kind);
  if (annotate) {
    const rej = new Set(state.edits.rejected);
    for (const f of state.doc.facts) for (const it of f.items) if (rej.has(keyOf(f, it)) && f.group === "Components") {
      const b = bboxOf(it.shape);
      box(b, "#c92a2a", true, "✕ rejected");
    }
    const f = factById(state.pinned);
    if (f) {
      let n = 0;
      for (const it of itemsOf(f)) {
        if (it.rejected) continue;
        const b = bboxOf(it.shape);
        box(b, color(f.kind), false);
        const [x, y] = it.shape.poly ? center(b) : [b[0], b[1]];
        g.fillStyle = color(f.kind); g.beginPath(); g.arc(x * s, y * s, 13, 0, 7); g.fill();
        g.fillStyle = "#fff"; g.font = "bold 14px Helvetica, Arial"; g.textAlign = "center"; g.textBaseline = "middle";
        g.fillText(++n, x * s, y * s); g.textAlign = "start"; g.textBaseline = "alphabetic";
      }
    }
  }
  if (footer) {
    g.fillStyle = "#1d1d1b"; g.fillRect(0, H, W, foot);
    g.fillStyle = "#fff"; g.font = "bold 18px Helvetica, Arial";
    g.fillText(`${state.doc.file}  ·  ${summaryLine()}`, 14, H + 28, W - 28);
    g.font = "12px Helvetica, Arial"; g.fillStyle = "#bbb";
    g.fillText(`Stoa Plan Check · ${state.doc.credit}`, 14, H + 52, W - 28);
  }
  return new Promise((ok) => c.toBlob(ok, "image/png"));
}

function summaryLine() {
  return state.doc.facts.filter((f) => f.group === "Components" && valueOf(f) > 0)
    .map((f) => `${f.label} ${valueOf(f)}`).join(" · ");
}
function editSummary() {
  const lines = [];
  const add = {};
  for (const a of state.edits.added) add[a.kind] = (add[a.kind] || 0) + 1;
  for (const [k, n] of Object.entries(add)) lines.push(`+${n} ${k} added on site`);
  const rej = {};
  for (const k of state.edits.rejected) { const kind = k.split(":")[0]; rej[kind] = (rej[kind] || 0) + 1; }
  for (const [k, n] of Object.entries(rej)) lines.push(`−${n} ${k} rejected (not on site)`);
  return lines;
}

async function share(blob, name, title) {
  const file = new File([blob], name, { type: blob.type });
  const text = [`${state.doc.file}`, summaryLine(), ...editSummary(), "", `Source: ${state.doc.credit}`].join("\n");
  if (navigator.canShare?.({ files: [file] })) {
    try { await navigator.share({ files: [file], title, text }); return; }
    catch (e) { if (e.name === "AbortError") return; }
  }
  // Desktop fallback: save the file, open a mail draft to attach it to.
  const url = URL.createObjectURL(blob);
  h("a", { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  location.href = `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(text + `\n\n(Attach ${name}, saved to your downloads.)`)}`;
  toast(`Saved ${name}: attach it to the email`, 5000);
}
const stem = () => state.doc.file.replace(/\.[^.]+$/, "");
$("#btn-share").onclick = async () => share(await planPNG(), `${stem()}_checked.png`, `Plan check: ${state.doc.file}`);

// 3D render
let lastRender = null;
$("#btn-render").onclick = () => $("#render-dlg").showModal();
$("#btn-generate").onclick = async () => {
  const out = $("#render-out");
  const btn = $("#btn-generate");
  btn.disabled = true;
  out.replaceChildren(h("p", {}, h("span", { className: "spinner" }), " Rendering… usually 10–30 s"));
  try {
    const png = await planPNG({ maxSide: 1600, annotate: false, footer: false });
    const dataUrl = await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(png); });
    const style = document.querySelector("input[name=style]:checked").value;
    const r = await fetch("/api/render", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.doc.id, image: dataUrl, style }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const res = await r.json();
    lastRender = await (await fetch(res.image)).blob();
    out.replaceChildren(h("figure", {},
      h("img", { src: res.image, alt: "AI 3D render of the plan" }),
      h("figcaption", { className: "muted", textContent: `${res.model} · ${res.latency_s}s · AI illustration, not for measurement` })));
    $("#btn-share-3d").disabled = false;
  } catch (err) {
    out.replaceChildren(h("p", { className: "muted", textContent: `Render failed: ${err.message}` }));
  } finally { btn.disabled = false; }
};
$("#btn-share-3d").onclick = () => lastRender &&
  share(lastRender, `${stem()}_3d.${lastRender.type.split("/")[1] || "png"}`, `3D view: ${state.doc.file}`);

$("#picker").onchange = (e) => loadDoc(e.target.value);
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.pinned) { state.pinned = null; renderFacts(); }
  if ((e.metaKey || e.ctrlKey) && e.key === "z" && state.editing) { e.preventDefault(); $("#btn-undo").click(); }
});
loadIndex();
