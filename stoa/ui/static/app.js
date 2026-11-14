// Stoa Plan Check: pre-computed facts over the plan, hover to verify, tap to add, ask, render and share the render.
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
  addKind: null, // insert mode: the kind a tap on the plan adds
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
let drawings = []; // index entries in picker order
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
  const byId = Object.fromEntries(list.map((d) => [d.id, d]));
  drawings = [...picker.options].map((o) => byId[o.value]); // picker order = arrow order
  if (!list.length) { toast("No facts yet: run  uv run python -m stoa.ui.facts", 10000); return; }
  const want = decodeURIComponent(location.hash.slice(1));
  await loadDoc(byId[want] ? want : drawings[0].id);
}

async function loadDoc(id) {
  const token = ++loadDoc.n;
  $("#picker").value = id;
  const i = drawings.findIndex((d) => d.id === id);
  $("#nav-prev").disabled = i <= 0;
  $("#nav-next").disabled = i < 0 || i >= drawings.length - 1;
  const doc = await (await fetch(`/api/drawings/${id}`)).json();
  if (token !== loadDoc.n) return; // a later arrow press or search pick won
  state.doc = doc; state.edits = doc.edits; state.history = [];
  state.pinned = state.hover = state.single = state.adhoc = state.addKind = null;
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
  closeMenu(); resetRender();
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
  if (costOpen()) renderCost();
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

// The engineer's additions, always shown. data-added lets a right-click find them.
function drawAll() {
  const ed = $("#layer-edits");
  ed.replaceChildren();
  for (const a of state.edits.added) {
    shapeEl({ bbox: a.bbox }, { class: "ov ov-add", fill: color(a.kind), stroke: color(a.kind), "data-added": a.id }, ed);
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
    // Tag shapes so a right-click knows what it hit; answer evidence isn't editable.
    const tag = it.added ? { "data-added": it.ref } : f.id !== "__answer" ? { "data-key": keyOf(f, it) } : {};
    if (it.rejected) {
      shapeEl(it.shape, { class: "ov ov-hl", fill: "#c92a2a", stroke: "#c92a2a", "stroke-dasharray": "4 3", ...tag }, layer);
      continue;
    }
    n++;
    shapeEl(it.shape, { class: `ov ov-hl${one ? " one" : ""}`, fill: c, stroke: c, ...tag }, layer);
    const b = bboxOf(it.shape);
    const [cx, cy] = it.shape.poly ? center(b) : [b[0], b[1]];
    el("circle", { cx, cy, r: one ? r * 1.3 : r, fill: c, class: "badge-c", ...tag }, layer);
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
let menuTarget = null, longPressAt = 0;
svg.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) { menuTarget = e.target; return; } // right button: the contextmenu event handles it
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  svg.setPointerCapture(e.pointerId);
  drag = { x: e.clientX, y: e.clientY, view: { ...state.view }, moved: 0, target: e.target };
  if (pointers.size === 2) {
    clearTimeout(drag.press);
    const [a, b] = [...pointers.values()];
    drag.pinch = Math.hypot(a.x - b.x, a.y - b.y);
  } else if (e.pointerType !== "mouse") {
    // Touch has no right-click: a long press opens the same menu (iOS never fires contextmenu).
    const d = drag;
    d.press = setTimeout(() => {
      if (drag !== d || d.moved > 4 || pointers.size !== 1) return;
      d.longpress = true; longPressAt = Date.now();
      planMenu(d.x, d.y, d.target);
    }, 550);
  }
});
svg.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (Date.now() - longPressAt < 1000) return; // Android fires this after our own long press
  planMenu(e.clientX, e.clientY, menuTarget || e.target);
  menuTarget = null;
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
  if (!pointers.delete(e.pointerId)) return;
  svg.classList.remove("dragging");
  clearTimeout(drag?.press);
  if (drag && !drag.longpress && drag.moved <= 4 && pointers.size === 0 && e.type === "pointerup") onPlanClick(e);
  if (pointers.size === 0) drag = null;
}
svg.addEventListener("pointerup", endPointer);
svg.addEventListener("pointercancel", endPointer);
svg.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.clientX, e.clientY, Math.exp(e.deltaY * 0.0015)); }, { passive: false });
$("#z-in").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.x + r.width / 2, r.y + r.height / 2, 0.7); };
$("#z-out").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.x + r.width / 2, r.y + r.height / 2, 1 / 0.7); };
$("#z-fit").onclick = fit;
window.addEventListener("resize", () => state.doc && fit());

// ---------- editing: pick a kind, tap the plan to add; right-click / long-press to remove ----------
const components = () => state.doc.facts.filter((f) => f.group === "Components");

// Cursor in insert mode: a "+" in the armed kind's colour.
const addCursor = (c) => `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28"><circle cx="14" cy="14" r="11" fill="${c}" stroke="#fff" stroke-width="2"/>` +
  `<path d="M14 8.5v11M8.5 14h11" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></svg>`)}") 14 14`;

function buildPalette() {
  const box = $("#palette-kinds");
  box.replaceChildren();
  for (const f of components()) {
    const b = h("button", { type: "button" }, h("span", { className: "swatch", style: `background:${color(f.kind)}` }), f.label.replace(/s$/, ""));
    b.setAttribute("aria-pressed", state.addKind === f.kind);
    b.onclick = () => arm(state.addKind === f.kind ? null : f.kind);
    box.append(b);
  }
  const k = state.addKind;
  $("#palette").classList.toggle("armed", !!k);
  $("#palette-hint").textContent = k ? `Tap the plan to place a ${k} · Esc to stop` : "Tap the plan to add";
  $("#btn-undo").hidden = !state.history.length;
  svg.classList.toggle("adding", !!k);
  if (k) svg.style.setProperty("--add-cursor", addCursor(color(k)));
}

// Insert mode stays on until the kind is tapped again or Esc, so several can be placed in a row.
// Pinning the kind's fact shows what's already detected, so the engineer adds only what's missing.
function arm(kind) {
  state.addKind = kind;
  if (kind) { state.pinned = components().find((f) => f.kind === kind)?.id ?? state.pinned; state.single = null; }
  buildPalette(); renderFacts();
}

function commit(mutate) {
  state.history.push(JSON.stringify(state.edits));
  mutate(state.edits);
  buildPalette(); renderFacts(); saveEdits();
}
function undo() {
  if (!state.history.length) return toast("Nothing to undo");
  state.edits = JSON.parse(state.history.pop());
  buildPalette(); renderFacts(); saveEdits();
}
$("#btn-undo").onclick = undo;
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

function onPlanClick(e) {
  const kind = state.addKind;
  if (!kind) return;
  const p = toView(e.clientX, e.clientY);
  const [w, hh] = defaultSize(kind);
  commit((ed) => ed.added.push({ id: `add-${Date.now()}`, kind, bbox: [p.x - w / 2, p.y - hh / 2, p.x + w / 2, p.y + hh / 2] }));
  toast(`Added 1 ${kind} · right-click it to remove`);
}

// ---------- pop-up menu (plan right-click, share) ----------
let menu = null;
function popMenu(x, y, entries, parent = document.body) {
  closeMenu();
  menu = h("div", { className: "menu", role: "menu" });
  for (const [label, fn, cls] of entries) {
    if (!fn) { menu.append(h("div", { className: "label", textContent: label })); continue; }
    const b = h("button", { type: "button", role: "menuitem", textContent: label, className: cls || "" });
    b.onclick = () => { closeMenu(); fn(); };
    menu.append(b);
  }
  parent.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
  menu.querySelector("button")?.focus({ preventScroll: true });
}
function closeMenu() { menu?.remove(); menu = null; }
document.addEventListener("pointerdown", (e) => { if (menu && !menu.contains(e.target)) closeMenu(); }, true);
svg.addEventListener("wheel", closeMenu, { passive: true });

function planMenu(x, y, target) {
  const addedId = target?.getAttribute?.("data-added");
  const key = target?.getAttribute?.("data-key");
  if (addedId) {
    const a = state.edits.added.find((a) => a.id === addedId);
    if (!a) return;
    popMenu(x, y, [[`Your added ${a.kind}`], [`Remove this ${a.kind}`, () => {
      commit((ed) => { ed.added = ed.added.filter((x) => x.id !== addedId); });
      toast(`Removed your ${a.kind}`);
    }, "danger"]]);
  } else if (key) {
    const kind = key.split(":")[0];
    const was = state.edits.rejected.includes(key);
    popMenu(x, y, [[`Detected ${kind}`], was
      ? [`Restore: count it again`, () => { commit((ed) => { ed.rejected = ed.rejected.filter((k) => k !== key); }); toast("Restored"); }]
      : [`Reject: not on site`, () => { commit((ed) => { ed.rejected = [...ed.rejected, key]; }); toast("Rejected: not counted any more"); }, "danger"]]);
  }
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

// ---------- 3D render and sharing it ----------
function loadImage(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
}

// The plan as the image model sees it: the drawing plus the engineer's additions, labelled by kind.
async function planPNG(maxSide = 1600) {
  const v = state.doc.view;
  const s = Math.min(1, maxSide / Math.max(v.width, v.height)) || 1;
  const W = Math.round(v.width * s), H = Math.round(v.height * s);
  const c = h("canvas", { width: W, height: H });
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
  g.drawImage(await loadImage(v.background), 0, 0, W, H);
  for (const a of state.edits.added) {
    const [x0, y0, x1, y1] = a.bbox.map((n) => n * s), col = color(a.kind);
    g.strokeStyle = col; g.lineWidth = 3; g.setLineDash([8, 5]); g.fillStyle = col + "33";
    g.fillRect(x0, y0, x1 - x0, y1 - y0); g.strokeRect(x0, y0, x1 - x0, y1 - y0);
    g.fillStyle = col; g.font = "bold 16px Helvetica, Arial"; g.fillText(a.kind, x0, y0 - 4);
  }
  return new Promise((ok) => c.toBlob(ok, "image/png"));
}

function summaryLine() {
  return components().filter((f) => valueOf(f) > 0).map((f) => `${f.label} ${valueOf(f)}`).join(" · ");
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
const stem = () => state.doc.file.replace(/\.[^.]+$/, "");

let lastRender = null;
$("#btn-render").onclick = () => $("#render-dlg").showModal();
$("#btn-generate").onclick = async () => {
  const out = $("#render-out");
  const btn = $("#btn-generate");
  btn.disabled = true;
  out.replaceChildren(h("p", {}, h("span", { className: "spinner" }), " Rendering… usually 10–30 s"));
  try {
    const png = await planPNG();
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
function resetRender() {
  lastRender = null;
  $("#btn-share-3d").disabled = true;
  $("#render-out").replaceChildren(h("p", { className: "muted",
    textContent: "Renders the plan as you edited it. AI illustration only: use the plan for counts and measurements." }));
}

// The clipboard only takes PNG everywhere; Gemini may return JPEG.
async function asPNG(blob) {
  if (blob.type === "image/png") return blob;
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImage(url);
    const c = h("canvas", { width: img.naturalWidth, height: img.naturalHeight });
    c.getContext("2d").drawImage(img, 0, 0);
    return await new Promise((ok) => c.toBlob(ok, "image/png"));
  } finally { URL.revokeObjectURL(url); }
}
async function copyRender() {
  try {
    // Pass the promise straight in: Safari drops the user gesture across an await.
    await navigator.clipboard.write([new ClipboardItem({ "image/png": asPNG(lastRender) })]);
    toast("Render copied: paste it into a chat or report");
  } catch (e) {
    toast(`Could not copy: ${e.message}`, 5000);
  }
}
async function emailRender() {
  const name = `${stem()}_3d.${lastRender.type.split("/")[1] || "png"}`;
  const title = `3D view: ${state.doc.file}`;
  const text = [state.doc.file, summaryLine(), ...editSummary(), "",
    "AI illustration of the plan, not for measurement.", `Source: ${state.doc.credit}`].join("\n");
  const file = new File([lastRender], name, { type: lastRender.type });
  // Phones and tablets: the share sheet attaches the file to a new mail directly.
  if (navigator.canShare?.({ files: [file] })) {
    try { await navigator.share({ files: [file], title, text }); return; }
    catch (e) { if (e.name === "AbortError") return; }
  }
  // mailto can't carry an attachment: save the file, then open the draft to attach it to.
  const url = URL.createObjectURL(lastRender);
  h("a", { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  location.href = `mailto:?subject=${encodeURIComponent(title)}&body=${encodeURIComponent(text + `\n\n(Attach ${name}, saved to your downloads.)`)}`;
  toast(`Saved ${name}: attach it to the email`, 5000);
}
$("#btn-share-3d").onclick = (e) => {
  if (!lastRender) return;
  const r = e.currentTarget.getBoundingClientRect();
  popMenu(r.left, r.bottom + 4, [["Copy to clipboard", copyRender], ["Attach to an email", emailRender]], $("#render-dlg"));
};

// ---------- cost estimate: plan quantities (with the engineer's edits) × Finnish unit prices ----------
let prices = null;
const eur = (n) => `${Math.round(n).toLocaleString("fi-FI")} €`;
const costOpen = () => !$("#cost-pop").hidden;

function costLines() {
  const lines = [], unpriced = [];
  for (const f of components()) {
    const qty = valueOf(f), p = prices.items[f.kind];
    if (!qty) continue;
    if (!p) { unpriced.push(`${f.label} ${qty}`); continue; }
    lines.push({ fact: f, label: f.label, spec: p.label, qty, was: f.value, unit: "pcs",
      material: qty * p.material, labour: qty * p.labour });
  }
  const area = state.doc.facts.find((f) => f.id === "area_total");
  if (area?.value) {
    const p = prices.floor, qty = area.value;
    lines.push({ fact: area, label: "Floor finish", spec: `${p.label}, ${p.material + p.labour} €/${p.unit}`, qty, was: qty,
      unit: p.unit, material: qty * p.material, labour: qty * p.labour });
  }
  return { lines, unpriced };
}

function renderCost() {
  const pop = $("#cost-pop");
  if (!prices) { pop.replaceChildren(h("p", { className: "muted", textContent: "Loading prices…" })); return; }
  const { lines, unpriced } = costLines();
  const sum = (k) => lines.reduce((s, l) => s + l[k], 0);
  const mat = sum("material"), lab = sum("labour"), net = mat + lab, vat = net * prices.vat;
  const body = h("tbody");
  for (const l of lines) {
    const qty = h("td");
    if (l.unit === "pcs" && l.qty !== l.was) qty.append(h("span", { className: "was", textContent: l.was }));
    qty.append(l.unit === "pcs" ? `${l.qty}` : `${l.qty.toFixed(1)} ${l.unit}`);
    const tr = h("tr", { title: "Show these on the plan" },
      h("td", {}, h("div", { className: "what" }, h("span", { className: "swatch", style: `background:${color(l.fact.kind)}` }), l.label),
        h("div", { className: "spec", textContent: l.spec })),
      qty, h("td", { textContent: eur(l.material) }), h("td", { textContent: eur(l.labour) }),
      h("td", {}, h("strong", { textContent: eur(l.material + l.labour) })));
    tr.classList.toggle("hl", state.pinned === l.fact.id);
    tr.onmouseenter = () => { state.hover = l.fact.id; drawHighlight(); };
    tr.onmouseleave = () => { state.hover = null; drawHighlight(); };
    tr.onclick = () => { state.pinned = state.pinned === l.fact.id ? null : l.fact.id; state.single = null; renderFacts(); };
    body.append(tr);
  }
  const foot = (label, val, cls = "") => h("tr", { className: cls }, h("td", { colSpan: 4, textContent: label }), h("td", { textContent: val }));
  const edited = state.edits.added.length + state.edits.rejected.length;
  pop.replaceChildren(
    h("div", { className: "head" }, h("strong", { textContent: "Cost estimate" }),
      h("span", { className: "muted", textContent: edited ? `includes your ${edited} edit${edited > 1 ? "s" : ""}` : "quantities from the plan" })),
    lines.length ? h("table", {},
      h("thead", {}, h("tr", {}, ...["Item", "Qty", "Material", "Labour", "Total"].map((t) => h("th", { textContent: t })))),
      body,
      h("tfoot", {},
        foot("Material", eur(mat)), foot("Labour", eur(lab)), foot("Subtotal, excl. VAT", eur(net)),
        foot(`VAT ${(prices.vat * 100).toFixed(1).replace(/\.0$/, "")} %`, eur(vat)),
        foot("Total, incl. VAT", eur(net + vat), "total")))
      : h("p", { className: "muted", textContent: "No priced components on this plan." }),
    unpriced.length ? h("p", { className: "note", textContent: `Not priced: ${unpriced.join(", ")}.` }) : "",
    !state.doc.facts.every((f) => f.deterministic)
      ? h("p", { className: "note", textContent: "⚠ Quantities come from a detector: check them on the plan (tap a row) before quoting." }) : "",
    h("p", { className: "note", textContent: prices.basis }),
  );
}

function toggleCost(open = !costOpen()) {
  $("#cost-pop").hidden = !open;
  $("#btn-cost").setAttribute("aria-expanded", open);
  if (!open) return;
  renderCost();
  if (!prices) fetch("/static/prices.json").then((r) => r.json()).then((p) => { prices = p; if (costOpen()) renderCost(); })
    .catch(() => toast("Could not load prices"));
}
$("#btn-cost").onclick = () => toggleCost();
document.addEventListener("pointerdown", (e) => {
  // Clicks on the plan keep it open, so the engineer can fix counts and watch the total change.
  if (costOpen() && !e.target.closest("#cost, #stage, .menu")) toggleCost(false);
});

$("#picker").onchange = (e) => loadDoc(e.target.value);
loadDoc.n = 0;

// ---------- navigation: prev / next arrows and file-name search ----------
function step(d) {
  const i = drawings.findIndex((x) => x.id === $("#picker").value) + d;
  if (i >= 0 && i < drawings.length) loadDoc(drawings[i].id);
}
$("#nav-prev").onclick = () => step(-1);
$("#nav-next").onclick = () => step(1);

const search = { input: $("#search-input"), list: $("#search-results"), hits: [], sel: 0 };
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function runSearch() {
  const terms = search.input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return closeSearch();
  // Every term must appear in the file name or source; names starting with the first term come first.
  search.hits = drawings
    .filter((d) => terms.every((t) => `${d.file} ${d.source}`.toLowerCase().includes(t)))
    .sort((a, b) => b.file.toLowerCase().startsWith(terms[0]) - a.file.toLowerCase().startsWith(terms[0]))
    .slice(0, 50);
  search.sel = 0;
  const re = new RegExp(`(${terms.map(escRe).join("|")})`, "gi");
  search.list.replaceChildren(...search.hits.map((d, i) => {
    const name = h("span");
    for (const [j, part] of d.file.split(re).entries()) name.append(j % 2 ? h("mark", { textContent: part }) : part);
    const li = h("li", { role: "option", id: `hit-${i}` }, name, h("span", { className: "src", textContent: d.source }));
    li.onpointerdown = (e) => { e.preventDefault(); pickHit(i); }; // before the input's blur
    li.onpointermove = () => { search.sel = i; markSel(); };
    return li;
  }));
  if (!search.hits.length) search.list.append(h("li", { className: "none", textContent: "No drawing matches" }));
  search.list.hidden = false;
  $("#search").setAttribute("aria-expanded", "true");
  markSel();
}
function markSel() {
  [...search.list.children].forEach((li, i) => li.setAttribute("aria-selected", i === search.sel && !!search.hits.length));
  search.list.children[search.sel]?.scrollIntoView({ block: "nearest" });
  search.input.setAttribute("aria-activedescendant", search.hits.length ? `hit-${search.sel}` : "");
}
function closeSearch() {
  search.list.hidden = true; search.hits = [];
  $("#search").setAttribute("aria-expanded", "false");
}
function pickHit(i) {
  const d = search.hits[i];
  if (!d) return;
  closeSearch();
  search.input.value = ""; search.input.blur();
  loadDoc(d.id);
}
search.input.oninput = runSearch;
search.input.onfocus = runSearch;
search.input.onblur = closeSearch;
search.input.onkeydown = (e) => {
  const n = search.hits.length;
  if (e.key === "ArrowDown" && n) { e.preventDefault(); search.sel = (search.sel + 1) % n; markSel(); }
  else if (e.key === "ArrowUp" && n) { e.preventDefault(); search.sel = (search.sel - 1 + n) % n; markSel(); }
  else if (e.key === "Enter") { e.preventDefault(); pickHit(search.sel); }
  else if (e.key === "Escape") { e.stopPropagation(); search.input.value = ""; search.input.blur(); }
};

window.addEventListener("keydown", (e) => {
  const typing = e.target.closest?.("input, textarea, select") || $("#render-dlg").open;
  if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (e.key === "ArrowLeft") return step(-1);
    if (e.key === "ArrowRight") return step(1);
    if (e.key === "/") { e.preventDefault(); return search.input.focus(); }
  }
  if (e.key === "Escape") {
    if (menu) return closeMenu();
    if (costOpen()) return toggleCost(false);
    if (state.addKind) return arm(null);
    if (state.pinned) { state.pinned = null; renderFacts(); }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.target.closest?.("input, textarea")) { e.preventDefault(); undo(); }
});
loadIndex();
