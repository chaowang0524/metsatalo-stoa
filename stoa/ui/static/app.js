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
  stove: "#5c940d", fridge: "#5c940d", radiator: "#c2255c", drain: "#1098ad", socket: "#f08c00", light: "#fab005",
};
const color = (kind) => COLORS[kind] || "#e8590c";

const svg = $("#plan");
const state = {
  doc: null, edits: { added: [], rejected: [] }, history: [],
  view: { x: 0, y: 0, w: 1, h: 1 },
  pinned: null, hover: null, single: null, // fact ids / item key under the pointer
  adhoc: null, // an answer's evidence, shown like a fact
  addKind: null, // insert mode: the kind a tap on the plan adds
  addLib: null, // ...and its symbol from the library: a variant id, "generic", or null for the plan's own
  sel: null, // id of the added object with resize / rotate handles
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
function fmt(v, unit) {
  if (v == null || (typeof v === "string" && !v.trim())) return "No answer available";
  const numeric = (typeof v === "number" || typeof v === "string") && Number.isFinite(Number(v));
  if (!numeric) return typeof v === "number" ? "No answer available" : `${v}`;
  if (unit === "m2") return `${Number(v).toFixed(2)} m²`;
  return unit === "count" || unit == null ? `${v}` : `${v} ${unit}`;
}
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
      items.push({ ref: a.id, label: `${fact.kind} (added)`, shape: { poly: corners(a) }, added: true });
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
const STATUS = { processing: "⏳ analysing…", failed: "⚠ facts failed" };
function optionText(d) {
  const s = d.summary || {};
  const tag = STATUS[d.status] || [s.window != null && `${s.window} win`, s.door != null && `${s.door} doors`].filter(Boolean).join(", ");
  return tag ? `${d.file}  ·  ${tag}` : d.file;
}
async function loadIndex(want) {
  const list = await (await fetch("/api/drawings")).json();
  const picker = $("#picker");
  picker.replaceChildren();
  const groups = {};
  for (const d of list) {
    const g = groups[d.source] || (groups[d.source] = h("optgroup", { label: d.source === "uploads" ? "uploaded" : d.source }));
    g.append(h("option", { value: d.id, textContent: optionText(d) }));
  }
  for (const src of ["uploads", "ifc_models", "swiss_dwellings", "cubicasa5k"]) if (groups[src]) picker.append(groups[src]);
  for (const g of Object.values(groups)) if (!g.parentNode) picker.append(g);
  const byId = Object.fromEntries(list.map((d) => [d.id, d]));
  drawings = [...picker.options].map((o) => byId[o.value]); // picker order = arrow order
  if (!list.length) { toast("No facts yet: run  uv run python -m stoa.ui.facts, or upload a drawing", 10000); return; }
  want ||= decodeURIComponent(location.hash.slice(1));
  await loadDoc(byId[want] ? want : drawings[0].id);
  watchUploads();
}

// ---------- uploads: one or many drawings, facts computed on the server ----------
let uploadTimer = null;
function watchUploads() {
  clearTimeout(uploadTimer);
  if (!drawings.some((d) => d.status === "processing")) return;
  uploadTimer = setTimeout(async () => {
    try {
      const fresh = Object.fromEntries((await (await fetch("/api/drawings")).json()).map((d) => [d.id, d]));
      for (const d of drawings) {
        const n = fresh[d.id];
        if (!n || (n.status === d.status && n.background === d.background)) continue;
        const statusChanged = n.status !== d.status;
        Object.assign(d, n);
        const opt = [...$("#picker").options].find((o) => o.value === d.id);
        if (opt) opt.textContent = optionText(d);
        if (state.doc?.id === d.id) await loadDoc(d.id);
        else if (statusChanged) toast(`${d.file}: ${d.status === "done" ? "facts ready" : "facts failed"}`);
      }
    } catch { /* try again on the next tick */ }
    watchUploads();
  }, 3000);
}
async function uploadFiles(list) {
  const files = [...list];
  if (!files.length) return;
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`, 120000);
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  try {
    const r = await fetch("/api/uploads", { method: "POST", body: form });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const res = await r.json();
    const skip = res.skipped.length ? ` · skipped ${res.skipped.join(", ")}` : "";
    if (!res.drawings.length) { toast(`Nothing to analyse${skip}`, 6000); return; }
    toast(`${res.drawings.length} drawing${res.drawings.length > 1 ? "s" : ""} uploaded, analysing${skip}`, 5000);
    await loadIndex(res.drawings[0]);
  } catch (err) {
    toast(`Upload failed: ${err.message}`, 8000);
  }
}
$("#btn-upload").onclick = () => $("#upload-input").click();
$("#upload-input").onchange = (e) => { uploadFiles(e.target.files); e.target.value = ""; };
{
  let depth = 0; // dragenter/leave fire for every child element crossed
  const files = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  const show = (on) => { $("#drop").hidden = !on; if (!on) depth = 0; };
  addEventListener("dragenter", (e) => { if (files(e)) { e.preventDefault(); depth++; show(true); } });
  addEventListener("dragover", (e) => { if (files(e)) e.preventDefault(); });
  addEventListener("dragleave", (e) => { if (files(e) && --depth <= 0) show(false); });
  addEventListener("drop", (e) => { if (!files(e)) return; e.preventDefault(); show(false); uploadFiles(e.dataTransfer.files); });
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
  state.pinned = state.hover = state.single = state.adhoc = state.addKind = state.sel = null;
  state.edits.added.forEach(normalise);
  addedFacts();
  zoomed = null;
  history.replaceState(null, "", `#${id}`);
  const v = doc.view;
  const bg = $("#bg");
  bg.setAttribute("href", v.background);
  bg.setAttribute("width", v.width); bg.setAttribute("height", v.height);
  $("#credit").textContent = doc.credit;
  $("#doc-title").textContent = doc.file;
  const any = doc.facts.length > 0;
  const det = any && doc.facts.every((f) => f.deterministic);
  const basis = doc.source === "ifc_models" ? (doc.ifc ? "exact · from BIM model" : "exact · from plan SVG") : "exact · from CAD geometry";
  const cost = doc.cost_usd ? ` · $${doc.cost_usd.toFixed(2)}` : "";
  $("#doc-meta").replaceChildren(
    any ? h("span", { className: `badge ${det ? "det" : "model"}`, textContent: det ? basis
      : doc.facts.some((f) => f.deterministic) ? "✓ rows exact · ⚠ rows model: verify" : "model detection · verify on plan" }) : "",
    doc.storey ? h("span", { textContent: `storey ${doc.storey}` }) : "",
    any ? h("span", { className: "badge free", textContent: det ? "pre-computed · 0 LLM tokens" : `pre-computed · 1 Gemini call per plan${cost}` }) : "",
    h("span", { textContent: doc.uploaded ? "uploaded" : doc.source }),
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
  if (!state.doc.facts.length) box.append(emptyNote(state.doc));
  const groups = {};
  for (const f of state.doc.facts) (groups[f.group] ||= []).push(f);
  for (const [name, facts] of Object.entries(groups)) {
    const g = h("div", { className: "group" }, h("h2", { textContent: name }));
    for (const f of facts) g.append(...factRow(f));
    box.append(g);
  }
  box.scrollTop = scroll;
  drawAll(); drawHighlight();
  for (const btn of $("#thread").querySelectorAll(".reference-toggle")) {
    const active = state.pinned === "__answer" && state.adhoc?.button === btn;
    btn.setAttribute("aria-pressed", String(active));
    btn.textContent = active ? "Hide references" : `Highlight references (${btn.dataset.referenceCount})`;
  }
  if (costOpen()) renderCost();
}

function emptyNote(doc) {
  if (doc.status === "processing") {
    return h("div", { className: "empty" }, h("span", { className: "spinner" }), doc.kind === "raster" || doc.kind === "pdf"
      ? " Finding windows, doors, fixtures and rooms (one Gemini call, about a minute)…" : " Computing facts…");
  }
  if (doc.status === "failed") {
    return h("div", { className: "empty" }, "Could not pre-compute facts. You can still ask questions below.",
      h("div", { className: "err", textContent: doc.error || "" }));
  }
  return h("div", { className: "empty", textContent: doc.kind === "ifc"
    ? "IFC model without a plan drawing: ask a question below."
    : doc.kind === "pdf" ? "Sheet drawn from the uploaded IFC model: ask a question below, answers come from the model exactly."
    : "No pre-computed facts for this sheet. Ask a question below." });
}

function factRow(f) {
  const val = valueOf(f);
  const changed = f.unit === "count" && val !== f.value;
  const row = h("div", {
    className: `fact${state.pinned === f.id ? " pinned" : ""}${state.addKind === f.kind && f.group === "Components" ? " adding" : ""}${val === 0 ? " zero" : ""}`,
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
      textContent: state.addKind === f.kind && f.group === "Components" ? "⊕" : changed ? "✎" : f.deterministic ? "✓" : "⚠" }),
  );
  row.onmouseenter = () => { state.hover = f.id; drawHighlight(); row.classList.add("hl"); };
  row.onmouseleave = () => { state.hover = null; drawHighlight(); row.classList.remove("hl"); };
  // A component row also starts counting by hand: tap the plan to add one, right-click one to remove it.
  if (f.group === "Components") row.title = "Click to correct the count: tap the plan to add, right-click to remove";
  row.onclick = () => {
    if (f.group === "Components") {
      const off = state.addKind === f.kind && !state.addLib;
      if (off) state.pinned = null;
      return arm(off ? null : f.kind);
    }
    state.pinned = state.pinned === f.id ? null : f.id; state.single = null; renderFacts();
  };
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
    li.onclick = () => zoomToggle(it.ref, bboxOf(it.shape));
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

// The engineer's edits, always shown: added objects as stamped symbols with a "+ kind" tag,
// rejected ones crossed out. Everything is styled with attributes, not CSS, so the export
// can serialise this layer as is.
function drawAll() {
  const ed = $("#layer-edits");
  ed.replaceChildren();
  for (const key of state.edits.rejected) {
    const [kind, ref] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
    const it = components().find((f) => f.kind === kind)?.items.find((i) => i.ref === ref);
    if (!it) continue;
    const b = bboxOf(it.shape);
    el("rect", { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], fill: "none", stroke: "#c92a2a",
      "stroke-width": 2, "stroke-dasharray": "5 3", "vector-effect": "non-scaling-stroke" }, ed);
    el("path", { d: `M${b[0]},${b[1]}L${b[2]},${b[3]}M${b[2]},${b[1]}L${b[0]},${b[3]}`, stroke: "#c92a2a",
      "stroke-width": 2, "vector-effect": "non-scaling-stroke" }, ed);
    tag(`− ${kindName(kind)}`, "#c92a2a", b, ed);
  }
  for (const a of state.edits.added) {
    stampEl(a, ed);
    tag(`+ ${kindName(a.kind)}`, color(a.kind), a.bbox, ed);
  }
  drawSel();
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
  drawHighlight(); drawSel();
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
// Click an item to zoom in on it, click it again to go back to the view before the first zoom.
let zoomed = null; // { ref, prev }
function zoomToggle(ref, b) {
  if (zoomed?.ref === ref) { setView(zoomed.prev); zoomed = null; return; }
  zoomed = { ref, prev: zoomed?.prev ?? { ...state.view } };
  zoomTo(b);
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
  if (edit) return;
  if (pointers.size === 0 && startEdit(e)) return;
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
  if (edit?.id === e.pointerId) return moveEdit(e);
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
  if (edit?.id === e.pointerId) return endEdit();
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

// ---------- splitter between the plan and the side panel ----------
// The plan keeps its scale and centre while the panel is resized; the width is remembered per browser.
const PANEL_W = 380, panel = $("#panel"), splitter = $("#splitter");
function setPanelWidth(w) {
  const max = Math.max(260, window.innerWidth - 320);
  w = Math.round(Math.min(Math.max(w, 260), max));
  const k = unitsPerPx(), v = state.view, cx = v.x + v.w / 2, cy = v.y + v.h / 2;
  panel.style.width = `${w}px`;
  splitter.setAttribute("aria-valuenow", w);
  if (state.doc) {
    const nw = svg.clientWidth * k, nh = svg.clientHeight * k;
    setView({ x: cx - nw / 2, y: cy - nh / 2, w: nw, h: nh });
  }
  try { localStorage.setItem("stoa.panelWidth", w); } catch {}
}
try { const w = +localStorage.getItem("stoa.panelWidth"); if (w) panel.style.width = `${w}px`; } catch {}
splitter.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  splitter.setPointerCapture(e.pointerId);
  splitter.classList.add("dragging"); document.body.classList.add("resizing");
  const move = (ev) => setPanelWidth(window.innerWidth - ev.clientX - splitter.offsetWidth / 2);
  const up = () => {
    splitter.classList.remove("dragging"); document.body.classList.remove("resizing");
    splitter.removeEventListener("pointermove", move); splitter.removeEventListener("pointerup", up);
    splitter.removeEventListener("pointercancel", up);
  };
  splitter.addEventListener("pointermove", move);
  splitter.addEventListener("pointerup", up);
  splitter.addEventListener("pointercancel", up);
});
splitter.ondblclick = () => setPanelWidth(PANEL_W);
splitter.onkeydown = (e) => {
  const d = { ArrowLeft: 24, ArrowRight: -24 }[e.key];
  if (d) { e.preventDefault(); e.stopPropagation(); setPanelWidth(panel.offsetWidth + d * (e.shiftKey ? 4 : 1)); }
};

// ---------- editing: pick a kind, tap the plan to add; right-click / long-press to remove ----------
const components = () => state.doc.facts.filter((f) => f.group === "Components");

// Cursor in insert mode: a "+" in the armed kind's colour.
const addCursor = (c) => `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28"><circle cx="14" cy="14" r="11" fill="${c}" stroke="#fff" stroke-width="2"/>` +
  `<path d="M14 8.5v11M8.5 14h11" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/></svg>`)}") 14 14`;

function buildPalette() {
  const box = $("#palette-kinds");
  box.replaceChildren();
  // The plan's own kinds are armed from the facts panel; the palette only adds the library.
  const lb = h("button", { type: "button", className: "lib-btn", title: "Symbol library: windows, doors, fixtures and more from other drawings" }, "▦ Library…");
  lb.setAttribute("aria-pressed", !!state.addLib);
  lb.setAttribute("aria-expanded", libOpen());
  lb.onclick = (e) => { e.stopPropagation(); toggleLib(); };
  box.append(lb);
  const k = state.addKind, v = state.addLib && state.addLib !== "generic" ? libVariant(state.addLib) : null;
  const which = v ? ` (library, ${v.label})` : state.addLib ? " (standard symbol)" : "";
  $("#palette").classList.toggle("armed", !!k);
  $("#palette-hint").textContent = k ? `Tap the plan to add a ${kindName(k)}${which}, right-click one to remove · Esc to stop` : "Click a component on the right to add more, or pick from the";
  $("#btn-undo").hidden = !state.history.length;
  svg.classList.toggle("adding", !!k);
  if (k) svg.style.setProperty("--add-cursor", addCursor(color(k)));
}

// Insert mode stays on until the kind is tapped again or Esc, so several can be placed in a row.
// Pinning the kind's fact shows what's already detected, so the engineer adds only what's missing.
function arm(kind, lib = null) {
  state.addKind = kind; state.addLib = kind ? lib : null;
  if (kind) { state.pinned = components().find((f) => f.kind === kind)?.id ?? state.pinned; state.single = null; }
  buildPalette(); renderFacts();
}

function commit(mutate) {
  state.history.push(JSON.stringify(state.edits));
  mutate(state.edits);
  addedFacts(); buildPalette(); renderFacts(); saveEdits();
}
function undo() {
  if (!state.history.length) return toast("Nothing to undo");
  state.edits = JSON.parse(state.history.pop());
  if (!selected()) state.sel = null;
  addedFacts(); buildPalette(); renderFacts(); saveEdits();
}

// A kind added from the library that the plan has none of gets its own (initially empty) count,
// so it is listed, highlighted and priced like the detected kinds.
function addedFacts() {
  const kinds = new Set(state.edits.added.map((a) => a.kind));
  state.doc.facts = state.doc.facts.filter((f) => !f.fromLibrary || kinds.has(f.kind));
  const have = new Set(components().map((f) => f.kind));
  const at = state.doc.facts.findLastIndex((f) => f.group === "Components") + 1;
  const extra = [...kinds].filter((k) => !have.has(k)).map((k) => ({
    id: `added:${k}`, group: "Components", kind: k, label: `${kindName(k).replace(/^./, (c) => c.toUpperCase())}s`,
    value: 0, unit: "count", items: [], deterministic: true, fromLibrary: true,
    method: "Not on the drawing: placed by the engineer from the symbol library" }));
  state.doc.facts.splice(at, 0, ...extra);
}

// ---------- symbol library: real symbols from other drawings, plus standard plan symbols ----------
let library = { kinds: {} };
fetch("/api/library").then((r) => r.json()).then((l) => { library = l; if (state.doc) renderFacts(); }).catch(() => {});
function libVariant(id) {
  for (const vs of Object.values(library.kinds)) {
    const v = vs.find((x) => x.id === id);
    if (v) return { ...v, ref: v.id, shape: { bbox: v.bbox } };
  }
  return null;
}
const libOpen = () => !$("#lib-pop").hidden;
function toggleLib(open = !libOpen()) {
  $("#lib-pop").hidden = !open;
  if (open) renderLib();
  $("#palette .lib-btn")?.setAttribute("aria-expanded", open);
}
// A thumbnail of a symbol: its own geometry fitted into a square, in the kind's colour.
function thumb(kind, markup, box) {
  const [x0, y0, x1, y1] = box, w = x1 - x0 || 1, hh = y1 - y0 || 1, pad = Math.max(w, hh) * 0.08;
  const s = el("svg", { viewBox: `${x0 - pad} ${y0 - pad} ${w + 2 * pad} ${hh + 2 * pad}`, width: 52, height: 52 });
  const g = el("g", { fill: "none", stroke: color(kind), color: color(kind) }, s);
  g.innerHTML = markup;
  for (const n of g.querySelectorAll("path, rect, circle, ellipse, polygon, line"))
    Object.entries({ "vector-effect": "non-scaling-stroke", "stroke-width": 1.5, fill: "none", stroke: color(kind) })
      .forEach(([k, v]) => n.setAttribute(k, v));
  return s;
}
function renderLib() {
  const pop = $("#lib-pop");
  const kinds = [...new Set([...Object.keys(library.kinds), ...Object.keys(GENERIC)])]
    .sort((a, b) => kindName(a).localeCompare(kindName(b)));
  pop.replaceChildren(h("div", { className: "head" }, h("strong", { textContent: "Symbol library" }),
    h("span", { className: "muted", textContent: "pick a symbol, then tap the plan" })));
  for (const k of kinds) {
    const row = h("div", { className: "lib-row" });
    const pick = (lib, title, svgEl) => {
      const b = h("button", { type: "button", title }, svgEl);
      b.setAttribute("aria-pressed", state.addKind === k && state.addLib === lib);
      b.onclick = () => { arm(k, lib); toggleLib(false); };
      row.append(b);
    };
    for (const v of library.kinds[k] || [])
      pick(v.id, `${kindName(k)} ${v.label} · from ${v.source} (${v.n} like it)`, thumb(k, v.sym, v.bbox));
    const [size, mk] = GENERIC[k] || GENERIC_BOX;
    pick("generic", `${kindName(k)}, standard symbol ${size[0]} × ${size[1]} m`, thumb(k, mk, [0, 0, 100, 100]));
    pop.append(h("div", { className: "lib-kind" },
      h("div", { className: "lib-name" }, h("span", { className: "swatch", style: `background:${color(k)}` }),
        kindName(k).replace(/^./, (c) => c.toUpperCase())), row));
  }
  pop.append(h("p", { className: "note", textContent:
    "Real symbols are copied from the Swiss Dwellings CAD plans (CC BY 4.0, Archilyse AG) and the IFC model plans, stamped at their real size." }));
}
document.addEventListener("pointerdown", (e) => {
  if (libOpen() && !e.target.closest?.("#lib-pop, .lib-btn")) toggleLib(false);
});
$("#btn-undo").onclick = undo;
let saveT;
function saveEdits() {
  clearTimeout(saveT);
  saveT = setTimeout(() => fetch(`/api/edits/${state.doc.id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.edits),
  }).catch(() => toast("Could not save edits")), 400);
}

// ---------- stamped symbols: a copy of a real instance of the kind, or a generic plan symbol ----------
const NAMES = { sauna: "sauna bench", sanitary: "sanitary fixture", appliance: "appliance" };
const kindName = (k) => NAMES[k] || k;

// Generic symbols in a 100 × 100 box, with a real-world size in metres, for kinds not yet on the plan.
const GENERIC = {
  window: [[1.2, 0.2], '<rect x="0" y="0" width="100" height="100"/><path d="M0 33H100M0 67H100"/>'],
  door: [[0.9, 0.9], '<path d="M0 100V0M0 0A100 100 0 0 1 100 100"/><path d="M0 100H100" stroke-dasharray="4 4"/>'],
  toilet: [[0.4, 0.7], '<rect x="5" y="0" width="90" height="28" rx="6"/><ellipse cx="50" cy="64" rx="40" ry="34"/>'],
  washbasin: [[0.6, 0.45], '<rect x="0" y="0" width="100" height="100" rx="14"/><ellipse cx="50" cy="55" rx="34" ry="30"/><circle cx="50" cy="55" r="4"/>'],
  bathtub: [[0.75, 1.7], '<rect x="0" y="0" width="100" height="100" rx="6"/><rect x="10" y="6" width="80" height="88" rx="30"/><circle cx="50" cy="16" r="3"/>'],
  shower: [[0.9, 0.9], '<rect x="0" y="0" width="100" height="100"/><path d="M0 0L100 100M100 0L0 100"/><circle cx="50" cy="50" r="7"/>'],
  sauna: [[2.0, 0.6], '<rect x="0" y="0" width="100" height="100"/><path d="M0 50H100M20 0V100M40 0V100M60 0V100M80 0V100"/>'],
  closet: [[1.0, 0.6], '<rect x="0" y="0" width="100" height="100"/><path d="M0 0L100 100M100 0L0 100"/>'],
  appliance: [[0.6, 0.6], '<rect x="0" y="0" width="100" height="100"/><circle cx="50" cy="50" r="30"/>'],
  fireplace: [[1.0, 0.6], '<rect x="0" y="0" width="100" height="100"/><path d="M15 100V35H85V100"/>'],
  chimney: [[0.5, 0.5], '<rect x="0" y="0" width="100" height="100"/><rect x="25" y="25" width="50" height="50"/>'],
  stair: [[1.0, 3.0], '<rect x="0" y="0" width="100" height="100"/><path d="M0 12H100M0 25H100M0 37H100M0 50H100M0 62H100M0 75H100M0 87H100M50 95V5M42 15L50 5L58 15"/>'],
  railing: [[1.5, 0.1], '<path d="M0 50H100M0 0V100M50 0V100M100 0V100"/>'],
  sink: [[0.8, 0.5], '<rect x="0" y="0" width="100" height="100"/><rect x="10" y="15" width="50" height="70" rx="8"/><circle cx="80" cy="50" r="6"/>'],
  stove: [[0.6, 0.6], '<rect x="0" y="0" width="100" height="100"/><circle cx="28" cy="28" r="15"/><circle cx="72" cy="28" r="15"/><circle cx="28" cy="72" r="15"/><circle cx="72" cy="72" r="15"/>'],
  fridge: [[0.6, 0.65], '<rect x="0" y="0" width="100" height="100"/><path d="M0 80H100"/><text x="50" y="55" font-size="34" text-anchor="middle" stroke="none" fill="currentColor">R</text>'],
  column: [[0.3, 0.3], '<rect x="0" y="0" width="100" height="100"/><path d="M0 0L100 100M100 0L0 100"/>'],
  radiator: [[1.0, 0.1], '<rect x="0" y="0" width="100" height="100"/><path d="M10 0V100M20 0V100M30 0V100M40 0V100M50 0V100M60 0V100M70 0V100M80 0V100M90 0V100"/>'],
  drain: [[0.2, 0.2], '<rect x="0" y="0" width="100" height="100"/><circle cx="50" cy="50" r="30"/><path d="M30 50H70M50 30V70"/>'],
  socket: [[0.25, 0.25], '<circle cx="50" cy="50" r="45"/><path d="M5 50H95M30 20V50M70 20V50"/>'],
  light: [[0.3, 0.3], '<circle cx="50" cy="50" r="45"/><path d="M18 18L82 82M82 18L18 82"/>'],
};
const GENERIC_BOX = [[0.6, 0.6], '<rect x="0" y="0" width="100" height="100"/><path d="M0 0L100 100M100 0L0 100"/>'];
const RASTER = () => /\.(png|jpe?g)$/i.test(state.doc.view.background);

// Instances of a kind whose look can be copied: vector geometry, or for a raster plan the pixels in the box.
function templates(kind) {
  const items = components().find((f) => f.kind === kind)?.items || [];
  return items.filter((it) => it.sym || (RASTER() && it.shape.bbox));
}
function tplOf(a) {
  if (a.lib) return libVariant(a.lib);
  return a.tpl ? templates(a.kind).find((it) => it.ref === a.tpl) : null;
}
function nearestTpl(kind, x, y) {
  let best = null, d = Infinity;
  for (const it of templates(kind)) {
    const [cx, cy] = center(bboxOf(it.shape)), dd = (cx - x) ** 2 + (cy - y) ** 2;
    if (dd < d) { d = dd; best = it; }
  }
  return best;
}
// View units per metre: known for CAD plans; otherwise from the doors on the plan (≈ 0.9 m).
function unitsPerM() {
  const v = state.doc.view;
  if (v.units_per_m) return v.units_per_m;
  const sides = (components().find((f) => f.kind === "door")?.items || [])
    .map((it) => { const b = bboxOf(it.shape); return Math.max(b[2] - b[0], b[3] - b[1]); }).sort((a, b) => a - b);
  return sides.length ? sides[sides.length >> 1] / 0.9 : v.width / 15;
}
function tplMarkup(it) {
  if (it.sym) return it.sym;
  const [x0, y0, x1, y1] = it.shape.bbox, v = state.doc.view;
  return `<svg x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" viewBox="${x0} ${y0} ${x1 - x0} ${y1 - y0}">` +
    `<image href="${v.background}" width="${v.width}" height="${v.height}" preserveAspectRatio="none"/></svg>`;
}

// Corners of an added object in view units, clockwise from top-left (in its own rotated frame).
function corners(a) {
  const r = a.rot * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
  return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => {
    const x = sx * a.w / 2, y = sy * a.h / 2;
    return [a.cx + x * c - y * s, a.cy + x * s + y * c];
  });
}
function updateBBox(a) {
  const p = corners(a), xs = p.map((q) => q[0]), ys = p.map((q) => q[1]);
  a.bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
// Edits saved before symbols had only a box: centre, size and the nearest real instance from it.
function normalise(a) {
  if (a.cx == null) {
    const [x0, y0, x1, y1] = a.bbox;
    Object.assign(a, { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, w: x1 - x0, h: y1 - y0, rot: 0, flip: false });
    a.tpl = nearestTpl(a.kind, a.cx, a.cy)?.ref ?? null;
  }
  updateBBox(a);
}

function stampEl(a, parent) {
  const c = color(a.kind);
  const g = el("g", { transform: `translate(${a.cx} ${a.cy}) rotate(${a.rot})`, "data-added": a.id }, parent);
  el("rect", { x: -a.w / 2, y: -a.h / 2, width: a.w, height: a.h, fill: c, "fill-opacity": 0.12, stroke: c,
    "stroke-width": 1.5, "stroke-dasharray": "6 3", "vector-effect": "non-scaling-stroke", "data-added": a.id }, g);
  const it = tplOf(a), f = a.flip ? -1 : 1;
  const sym = el("g", { fill: "none", stroke: c, color: c, "pointer-events": "none" }, g);
  if (it) {
    const b = bboxOf(it.shape), tw = b[2] - b[0] || 1, th = b[3] - b[1] || 1;
    sym.setAttribute("transform", `scale(${f * a.w / tw} ${a.h / th}) translate(${-(b[0] + b[2]) / 2} ${-(b[1] + b[3]) / 2})`);
    sym.innerHTML = tplMarkup(it);
  } else {
    sym.setAttribute("transform", `scale(${f * a.w / 100} ${a.h / 100}) translate(-50 -50)`);
    sym.innerHTML = (GENERIC[a.kind] || GENERIC_BOX)[1];
  }
  for (const n of sym.querySelectorAll("path, rect, circle, ellipse, polygon, line"))
    Object.entries({ "vector-effect": "non-scaling-stroke", "stroke-width": 2, fill: "none", stroke: c })
      .forEach(([k, v]) => n.setAttribute(k, v));
  return g;
}

// A label above a box, about as tall as the plan's room labels (≈ 0.22 m), so it reads the same in the export.
function tag(text, c, b, parent) {
  const v = state.doc.view, fs = Math.max(unitsPerM() * 0.22, Math.max(v.width, v.height) / 250);
  const t = el("text", { x: b[0], y: b[1] - fs * 0.35, "font-size": fs, "font-weight": 700, fill: c, stroke: "#fff",
    "stroke-width": fs * 0.22, "paint-order": "stroke", "stroke-linejoin": "round", "pointer-events": "none",
    "font-family": "-apple-system, Helvetica, Arial, sans-serif" }, parent);
  t.textContent = text;
}

function onPlanClick(e) {
  const kind = state.addKind;
  if (!kind) { if (state.sel) select(null); return; }
  const p = toView(e.clientX, e.clientY);
  // A library pick is stamped at its real-world size; otherwise copy the nearest instance on this plan.
  const lib = state.addLib && state.addLib !== "generic" ? libVariant(state.addLib) : null;
  const it = state.addLib ? null : nearestTpl(kind, p.x, p.y);
  let w, hh;
  if (lib) [w, hh] = lib.size_m.map((m) => m * unitsPerM());
  else if (it) { const b = bboxOf(it.shape); [w, hh] = [b[2] - b[0], b[3] - b[1]]; }
  else [w, hh] = (GENERIC[kind] || GENERIC_BOX)[0].map((m) => m * unitsPerM());
  const a = { id: `add-${Date.now()}`, kind, cx: p.x, cy: p.y, w, h: hh, rot: 0, flip: false, tpl: it?.ref ?? null,
    ...(lib && { lib: lib.id }) };
  updateBBox(a);
  commit((ed) => ed.added.push(a));
  select(a.id);
  const from = lib ? ", from the library" : it ? ", copied from the plan" : "";
  toast(`Added 1 ${kindName(kind)}${from} · drag the corners to resize, the top circle to rotate`, 4000);
}

// ---------- selection: drag to move, corner circles to resize, top circle to rotate ----------
const selected = () => state.edits.added.find((a) => a.id === state.sel);
function select(id) { state.sel = id; drawSel(); }

function drawSel() {
  const layer = $("#layer-sel");
  layer.replaceChildren();
  const a = selected();
  if (!a) return;
  const k = unitsPerPx(), r = (matchMedia("(pointer: coarse)").matches ? 12 : 7) * k;
  const p = corners(a);
  el("polygon", { points: p.map((q) => q.join(",")).join(" "), fill: "none", stroke: "#1d1d1b",
    "stroke-width": 1.5, "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke", "pointer-events": "none" }, layer);
  // Rotation handle: on a stem above the middle of the top edge, in the object's own frame.
  const rad = a.rot * Math.PI / 180, up = [Math.sin(rad), -Math.cos(rad)];
  const top = [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2];
  const rh = [top[0] + up[0] * 28 * k, top[1] + up[1] * 28 * k];
  el("line", { x1: top[0], y1: top[1], x2: rh[0], y2: rh[1], stroke: "#1d1d1b", "stroke-width": 1.5,
    "vector-effect": "non-scaling-stroke", "pointer-events": "none" }, layer);
  el("circle", { cx: rh[0], cy: rh[1], r, class: "handle rot", "data-handle": "rot" }, layer);
  p.forEach((q, i) => el("circle", { cx: q[0], cy: q[1], r, class: "handle", "data-handle": i }, layer));
}

let edit = null; // an in-progress move / resize / rotate of the selected object
function startEdit(e) {
  const handle = e.target.closest?.("[data-handle]");
  const id = handle ? state.sel : e.target.closest?.("[data-added]")?.getAttribute("data-added");
  const a = state.edits.added.find((x) => x.id === id);
  if (!a) return false;
  if (!handle) select(a.id);
  const p = toView(e.clientX, e.clientY), h = handle?.getAttribute("data-handle");
  edit = { a, id: e.pointerId, p0: p, start: { ...a }, moved: false, before: JSON.stringify(state.edits),
    mode: h == null ? "move" : h === "rot" ? "rot" : "size", corner: h == null || h === "rot" ? null : +h };
  svg.setPointerCapture(e.pointerId);
  if (e.pointerType !== "mouse") { // touch: a long press without moving opens the object's menu
    const ed = edit, target = e.target;
    ed.press = setTimeout(() => {
      if (edit !== ed || ed.moved) return;
      edit = null; longPressAt = Date.now();
      planMenu(e.clientX, e.clientY, target);
    }, 550);
  }
  return true;
}
function moveEdit(e) {
  const { a, p0, start: s0 } = edit, p = toView(e.clientX, e.clientY);
  if (!edit.moved && Math.hypot(p.x - p0.x, p.y - p0.y) < 3 * unitsPerPx()) return;
  edit.moved = true;
  if (edit.mode === "move") {
    a.cx = s0.cx + p.x - p0.x; a.cy = s0.cy + p.y - p0.y;
  } else if (edit.mode === "rot") {
    let deg = Math.atan2(p.y - s0.cy, p.x - s0.cx) * 180 / Math.PI + 90;
    const step = e.shiftKey ? 15 : 45, snap = Math.round(deg / step) * step;
    if (e.shiftKey || Math.abs(deg - snap) < 5) deg = snap; // shift: 15° steps; otherwise snap near 45°
    a.rot = ((deg % 360) + 360) % 360;
  } else {
    // The opposite corner stays put; work in the object's frame so it resizes along its own axes.
    const [sx, sy] = [[-1, -1], [1, -1], [1, 1], [-1, 1]][edit.corner];
    const r = s0.rot * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    const fx = s0.cx - (sx * s0.w / 2) * c + (sy * s0.h / 2) * s, fy = s0.cy - (sx * s0.w / 2) * s - (sy * s0.h / 2) * c;
    const dx = (p.x - fx) * c + (p.y - fy) * s, dy = -(p.x - fx) * s + (p.y - fy) * c;
    const min = 6 * unitsPerPx();
    let w = Math.max(min, sx * dx), hh = Math.max(min, sy * dy);
    if (e.shiftKey) { const k = Math.max(w / s0.w, hh / s0.h); w = s0.w * k; hh = s0.h * k; } // shift: keep proportions
    a.w = w; a.h = hh;
    a.cx = fx + (sx * w / 2) * c - (sy * hh / 2) * s; a.cy = fy + (sx * w / 2) * s + (sy * hh / 2) * c;
  }
  updateBBox(a);
  drawAll(); drawHighlight();
}
function endEdit() {
  clearTimeout(edit.press);
  if (edit.moved) { state.history.push(edit.before); buildPalette(); saveEdits(); }
  edit = null;
}
function transformSel(fn, msg) {
  const a = selected();
  if (!a) return;
  commit(() => { fn(a); updateBBox(a); });
  toast(msg);
}
const rotateSel = (deg) => transformSel((a) => { a.rot = (((a.rot + deg) % 360) + 360) % 360; }, `Rotated ${deg > 0 ? "+" : ""}${deg}°`);
const flipSel = () => transformSel((a) => { a.flip = !a.flip; }, "Flipped");
function removeSel() {
  const a = selected();
  if (!a) return;
  commit((ed) => { ed.added = ed.added.filter((x) => x.id !== a.id); });
  state.sel = null; drawSel();
  toast(`Removed your ${kindName(a.kind)}`);
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
  const addedId = target?.closest?.("[data-added]")?.getAttribute("data-added") ?? (target?.closest?.("[data-handle]") && state.sel);
  const key = target?.getAttribute?.("data-key");
  // While counting a kind by hand, a right-click on one of that kind removes it at once (⌘Z undoes).
  const k = state.addKind;
  if (k && addedId) {
    const a = state.edits.added.find((a) => a.id === addedId);
    if (a?.kind === k) { select(a.id); return removeSel(); }
  }
  if (k && key?.split(":")[0] === k) {
    const was = state.edits.rejected.includes(key);
    commit((ed) => { ed.rejected = was ? ed.rejected.filter((x) => x !== key) : [...ed.rejected, key]; });
    return toast(was ? `Restored 1 ${kindName(k)}` : `Removed 1 ${kindName(k)}: not counted any more · ⌘Z to undo`);
  }
  if (addedId) {
    const a = state.edits.added.find((a) => a.id === addedId);
    if (!a) return;
    select(a.id);
    popMenu(x, y, [[`Your added ${kindName(a.kind)}`], ["Rotate 90°   (R)", () => rotateSel(90)],
      ["Flip   (F)", flipSel], [`Remove this ${kindName(a.kind)}   (Del)`, removeSel, "danger"]]);
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
  const numeric = typeof ans.value === "number" && Number.isFinite(ans.value);
  const details = h("details", { className: "answer-details" },
    h("summary", { textContent: "Sources and calculation details" }),
    h("div", {}, h("span", { className: `badge ${ans.deterministic ? "det" : "model"}`,
      textContent: ans.deterministic ? "exact" : "model answer · verify" }), " ", ans.method),
    ans.note ? h("div", { className: "sub", textContent: ans.note }) : "",
    h("div", { className: "sub", textContent: `${tools} · ${res.llm_calls} LLM calls · ${res.prompt_tokens + res.completion_tokens} tokens · ${cost}` }),
  );
  box.replaceChildren(
    h("div", { className: numeric ? "big" : "answer-text", textContent: ans.value !== null && typeof ans.value === "object" ? "see list" : fmt(ans.value, ans.unit) }),
    details,
  );
  if (res.items?.length) {
    const btn = h("button", { className: "small reference-toggle", textContent: `Highlight references (${res.items.length})`,
      title: "Highlight the drawing objects used as supporting evidence for this answer." });
    btn.dataset.referenceCount = res.items.length;
    btn.setAttribute("aria-pressed", "false");
    btn.onclick = () => {
      const active = state.pinned === "__answer" && state.adhoc?.button === btn;
      state.adhoc = active ? null : { id: "__answer", kind: "answer", items: ordered(res.items), button: btn };
      state.pinned = active ? null : "__answer";
      state.single = state.hover = null;
      renderFacts();
    };
    box.append(btn);
  }
  if (res.unplaced) details.append(h("div", { className: "sub", textContent: `${res.unplaced} evidence items are not drawn on this plan.` }));
}

// ---------- 3D render and sharing it ----------
function loadImage(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
}

const asDataURL = (blob) => new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(blob); });
const toBlob = (c, type, q) => new Promise((ok) => c.toBlob(ok, type, q));
const stem = () => state.doc.file.replace(/\.[^.]+$/, "");

// The edited plan as a canvas: the drawing with the edits layer on top, drawn from the same SVG as
// on screen, so stamped symbols and tags come out exactly as the engineer placed them.
// With caption, a strip below says what the file is and what was changed.
async function planCanvas(maxSide, caption) {
  const v = state.doc.view;
  let s = maxSide / Math.max(v.width, v.height);
  if (RASTER()) s = Math.min(s, 1); // no point in upscaling pixels
  const W = Math.round(v.width * s), H = Math.round(v.height * s);
  // An SVG drawn as an image can't load anything, so the background goes in as a data URL.
  const bg = await asDataURL(await (await fetch(v.background)).blob());
  const out = document.createElementNS(NS, "svg");
  for (const [k, val] of Object.entries({ width: W, height: H, viewBox: `0 0 ${v.width} ${v.height}` })) out.setAttribute(k, val);
  el("rect", { width: v.width, height: v.height, fill: "#fff" }, out);
  el("image", { href: bg, width: v.width, height: v.height, preserveAspectRatio: "none" }, out);
  const layer = $("#layer-edits").cloneNode(true);
  for (const im of layer.querySelectorAll("image")) im.setAttribute("href", bg);
  out.append(layer);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(out)], { type: "image/svg+xml" }));
  let img;
  try { img = await loadImage(url); } finally { URL.revokeObjectURL(url); }

  const lines = caption ? captionLines() : [];
  const fs = Math.max(13, Math.round(Math.max(W, H) / 95)), lh = Math.round(fs * 1.4), pad = Math.round(fs * 0.9);
  const capH = lines.length ? pad * 2 + lh * lines.length : 0;
  const c = h("canvas", { width: W, height: H + capH });
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); // JPEG has no alpha
  g.drawImage(img, 0, 0, W, H);
  if (capH) {
    g.fillStyle = "#1d1d1b"; g.fillRect(0, H, W, Math.max(2, fs / 8));
    g.textBaseline = "top";
    lines.forEach(([text, bold, col], i) => {
      g.font = `${bold ? "700" : "400"} ${fs}px -apple-system, Helvetica, Arial, sans-serif`;
      g.fillStyle = col || "#1d1d1b";
      g.fillText(text, pad, H + pad + i * lh, W - 2 * pad);
    });
  }
  return c;
}

function captionLines() {
  const count = (keys) => { const n = {}; for (const k of keys) n[k] = (n[k] || 0) + 1; return n; };
  const add = Object.entries(count(state.edits.added.map((a) => a.kind))).map(([k, n]) => `+${n} ${kindName(k)}`);
  const rej = Object.entries(count(state.edits.rejected.map((k) => k.split(":")[0]))).map(([k, n]) => `−${n} ${kindName(k)}`);
  const when = new Date().toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
  return [
    [`${state.doc.file}  ·  edited plan  ·  ${when}`, true],
    [add.length || rej.length ? `Changes on site: ${[...add, ...rej].join(", ")}` : "No changes on site", false, "#e8590c"],
    ["Dashed outlines labelled + were added by the engineer; red crossed-out boxes were rejected (not on site).", false, "#6b6a66"],
    [state.doc.credit, false, "#6b6a66"],
  ];
}

// A self-contained one-page PDF (A3, oriented like the plan) around a JPEG: no library needed.
function pdfFromJPEG(jpg, w, hh) {
  const [pw, ph] = w >= hh ? [1190.55, 841.89] : [841.89, 1190.55], m = 28;
  const k = Math.min((pw - 2 * m) / w, (ph - 2 * m) / hh), iw = w * k, ih = hh * k;
  const enc = new TextEncoder(), parts = [], offsets = [];
  let len = 0;
  const put = (x) => { const b = typeof x === "string" ? enc.encode(x) : x; parts.push(b); len += b.length; };
  const obj = (n, body) => { offsets[n] = len; put(`${n} 0 obj\n`); body(); put("\nendobj\n"); };
  put("%PDF-1.4\n");
  obj(1, () => put("<< /Type /Catalog /Pages 2 0 R >>"));
  obj(2, () => put("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"));
  obj(3, () => put(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pw} ${ph}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`));
  obj(4, () => {
    put(`<< /Type /XObject /Subtype /Image /Width ${w} /Height ${hh} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpg.length} >>\nstream\n`);
    put(jpg); put("\nendstream");
  });
  const draw = `q ${iw.toFixed(2)} 0 0 ${ih.toFixed(2)} ${((pw - iw) / 2).toFixed(2)} ${((ph - ih) / 2).toFixed(2)} cm /Im0 Do Q`;
  obj(5, () => put(`<< /Length ${draw.length} >>\nstream\n${draw}\nendstream`));
  const xref = len;
  put(`xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}` +
    `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(parts, { type: "application/pdf" });
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  h("a", { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
async function exportPlan(fmt) {
  toast("Preparing export…", 8000);
  try {
    const c = await planCanvas(3200, true);
    const blob = fmt === "png" ? await toBlob(c, "image/png") : await toBlob(c, "image/jpeg", 0.92);
    const out = fmt === "pdf" ? pdfFromJPEG(new Uint8Array(await blob.arrayBuffer()), c.width, c.height) : blob;
    download(out, `${stem()}_edited.${fmt === "jpeg" ? "jpg" : fmt}`);
    toast(`Exported ${fmt.toUpperCase()}`);
  } catch (e) {
    toast(`Export failed: ${e.message || e}`, 6000);
  }
}
$("#btn-export").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  popMenu(r.left, r.bottom + 4, [["Edits are saved automatically"], ["PNG image", () => exportPlan("png")],
    ["JPEG image", () => exportPlan("jpeg")], ["PDF (A3)", () => exportPlan("pdf")]]);
};

// The plan as the image model sees it: the same picture as the export, without the caption.
async function planPNG(maxSide = 1600) {
  return toBlob(await planCanvas(maxSide, false), "image/png");
}

let lastRender = null;
$("#btn-render").onclick = () => {
  const dlg = $("#render-dlg");
  dlg.style.cssText = ""; // opens centred again
  dlg.showModal();
};

// Drag the dialog by its title bar, so the plan behind it stays readable.
// left/top rather than a transform: a transform would offset the fixed-position share menu inside it.
{
  const dlg = $("#render-dlg"), head = dlg.querySelector(".dlg-head");
  let grab = null;
  head.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("button")) return;
    const r = dlg.getBoundingClientRect();
    grab = { dx: e.clientX - r.left, dy: e.clientY - r.top, w: r.width };
    head.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  head.addEventListener("pointermove", (e) => {
    if (!grab) return;
    // Keep the title bar on screen so it can always be grabbed back.
    const x = Math.min(Math.max(e.clientX - grab.dx, 40 - grab.w), innerWidth - 40);
    const y = Math.min(Math.max(e.clientY - grab.dy, 0), innerHeight - 40);
    Object.assign(dlg.style, { margin: "0", left: `${x}px`, top: `${y}px`, right: "auto", bottom: "auto" });
  });
  const drop = () => { grab = null; };
  head.addEventListener("pointerup", drop);
  head.addEventListener("pointercancel", drop);
}
$("#btn-generate").onclick = async () => {
  const out = $("#render-out");
  const btn = $("#btn-generate");
  btn.disabled = true;
  out.replaceChildren(h("p", {}, h("span", { className: "spinner" }), " Rendering… usually 10–30 s"));
  try {
    const png = await planPNG();
    const dataUrl = await asDataURL(png);
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
$("#btn-share-3d").onclick = (e) => {
  if (!lastRender) return;
  const r = e.currentTarget.getBoundingClientRect();
  popMenu(r.left, r.bottom + 4, [["Copy to clipboard", copyRender]], $("#render-dlg"));
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
  if (!typing && state.sel && !e.metaKey && !e.ctrlKey) {
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); return removeSel(); }
    if (e.key === "r" || e.key === "R") return rotateSel(e.shiftKey ? -90 : 90);
    if (e.key === "f" || e.key === "F") return flipSel();
  }
  if (e.key === "Escape") {
    if (menu) return closeMenu();
    if (costOpen()) return toggleCost(false);
    if (libOpen()) return toggleLib(false);
    if (state.addKind) return arm(null); // stop adding first; the next Esc drops the selection
    if (state.sel) return select(null);
    if (state.pinned) { state.pinned = null; renderFacts(); }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.target.closest?.("input, textarea")) { e.preventDefault(); undo(); }
});
loadIndex();
