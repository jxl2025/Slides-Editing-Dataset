"use strict";

const S = { decks: [], data: null, sync: true, mode: "all", highlight: false };
const COLS = ["original", "target", "response"];
const bodyEl = (c) => document.querySelector(`#col-${c} .col-body`);
const sigOf = (c, i) => (S.data[c].ssigs || S.data[c].psigs)[i];

async function boot() {
  const d = await (await fetch("/api/decks")).json();
  S.decks = d.decks;
  const deckSel = document.getElementById("deck");
  for (const dk of S.decks) {
    const o = document.createElement("option");
    o.value = dk.id; o.textContent = dk.id; deckSel.appendChild(o);
  }
  deckSel.addEventListener("change", onDeckChange);
  document.getElementById("task").addEventListener("change", refreshResponses);
  document.getElementById("load").addEventListener("click", load);
  document.getElementById("sync").addEventListener("change", (e) => {
    S.sync = e.target.checked;
    if (S.sync) syncFrom("original");
  });
  for (const b of document.querySelectorAll("#modes button")) {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  }
  document.getElementById("highlight").addEventListener("click", () => {
    S.highlight = !S.highlight;
    document.getElementById("highlight").classList.toggle("on", S.highlight);
    render();
  });
  for (const c of COLS) {
    const col = bodyEl(c);
    col.addEventListener("scroll", () => onScroll(c));
  }
  if (S.decks.length) { deckSel.value = S.decks[0].id; onDeckChange(); }
}

function onDeckChange() {
  const dk = S.decks.find((x) => x.id === document.getElementById("deck").value);
  const taskSel = document.getElementById("task");
  taskSel.innerHTML = "";
  for (const t of (dk ? dk.tasks : [])) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t; taskSel.appendChild(o);
  }
  refreshResponses();
}

async function refreshResponses() {
  const deck = document.getElementById("deck").value;
  const task = document.getElementById("task").value;
  const sel = document.getElementById("response");
  sel.innerHTML = '<option value="">(none)</option>';
  if (!deck || !task) return;
  const d = await (await fetch(
    `/api/responses?deck=${encodeURIComponent(deck)}&task=${encodeURIComponent(task)}`)).json();
  for (const r of d.responses) {
    const o = document.createElement("option");
    o.value = r.path; o.textContent = r.label; sel.appendChild(o);
  }
}

async function load() {
  const deck = document.getElementById("deck").value;
  const task = document.getElementById("task").value;
  const response = document.getElementById("response").value;
  if (!deck || !task) return;
  setStatus("rendering… (first time is slow)");
  const url = `/api/compare?deck=${encodeURIComponent(deck)}&task=${encodeURIComponent(task)}`
    + (response ? `&response=${encodeURIComponent(response)}` : "");
  const d = await (await fetch(url)).json();
  if (!d.ok) { setStatus("error: " + d.error); return; }
  S.data = d;
  document.documentElement.style.setProperty("--slide-aspect", `${d.w} / ${d.h}`);
  const structural = !!(d.original && d.original.ssigs);
  const note = structural ? "" : " · pixel diff (structural unavailable)";
  setStatus(`${d.n} slides${d.response ? "" : " · no response loaded"}${note}`);
  showScore();
  render();
}

function showScore() {
  const el = document.getElementById("score");
  const ev = S.data && S.data.eval;
  if (!ev || ev.error || !S.data.response) {
    el.textContent = ev && ev.error ? "eval: " + ev.error : "";
    return;
  }
  const m = ev.metrics;
  el.innerHTML =
    `<b>score ${m.score.toFixed(2)}</b> · scope F1 ${m.scope.f1.toFixed(2)} ` +
    `· strict F1 ${m.strict.f1.toFixed(2)} · value ${(m.value_accuracy * 100).toFixed(0)}% ` +
    `· overlaps ${m.bad_overlaps} · edits ${m.pred_edits}/${m.gt_edits}`;
}

// eval slide record for a given slide index
function evalSlide(i) {
  const ev = S.data && S.data.eval;
  if (!ev || ev.error) return null;
  return (ev.slides || []).find((s) => s.index === i) || null;
}

// Which slide indices each column shows under the current mode.
function visibleSets() {
  const d = S.data, n = d.n;
  const all = [...Array(n).keys()];
  const differ = (a, b) => (i) => sigOf(a, i) !== sigOf(b, i);
  const has = { response: !!d.response };
  const filt = (pred) => all.filter(pred);

  if (S.mode === "1-2") {
    const set = filt(differ("original", "target"));
    return { original: set, target: set, response: all };
  }
  if (S.mode === "2-3" && has.response) {
    const set = filt(differ("target", "response"));
    return { original: all, target: set, response: set };
  }
  if (S.mode === "1-3" && has.response) {
    const set = filt(differ("original", "response"));
    return { original: set, target: all, response: set };
  }
  if (S.mode === "all3") {
    const any = (i) => sigOf("original", i) !== sigOf("target", i)
      || (has.response && (sigOf("original", i) !== sigOf("response", i)
        || sigOf("target", i) !== sigOf("response", i)));
    const set = filt(any);
    return { original: set, target: set, response: set };
  }
  return { original: all, target: all, response: all };
}

function render() {
  if (!S.data) return;
  const sets = visibleSets();
  for (const c of COLS) {
    const col = bodyEl(c);
    col.innerHTML = "";
    const src = S.data[c];
    if (!src) {                                   // no response loaded
      const p = document.createElement("div");
      p.className = "empty"; p.textContent = "no response";
      col.appendChild(p);
      continue;
    }
    for (const i of sets[c]) {
      const box = document.createElement("div");
      box.className = "slide"; box.dataset.index = i;
      const num = document.createElement("div");
      num.className = "num"; num.textContent = "s" + (i + 1);
      const img = document.createElement("img");
      img.loading = "lazy"; img.src = "/images/" + src.images[i];
      box.appendChild(num); box.appendChild(img);
      if (S.highlight) drawBoxes(box, c, i);
      col.appendChild(box);
    }
  }
  if (S.sync) syncFrom("original");
}

// Overlay boxes: on the response column, red/purple/orange for
// wrong-value/over-edit/missed; on the target column, green for intended edits.
function drawBoxes(box, col, i) {
  const src = S.data[col];
  if (!src || !src.elements) return;
  const ev = evalSlide(i);
  if (!ev) return;
  const geom = {}; for (const e of src.elements[i]) geom[e.id] = e;

  const add = (e, cls, title) => {
    if (!e) return;
    const b = document.createElement("div");
    b.className = "hlbox " + cls; b.title = title;
    b.style.left = (e.x * 100) + "%"; b.style.top = (e.y * 100) + "%";
    b.style.width = (e.w * 100) + "%"; b.style.height = (e.h * 100) + "%";
    box.appendChild(b);
  };

  if (col === "response") {
    const overlapIds = new Set();
    for (const pair of ev.overlaps) pair.forEach((id) => overlapIds.add(id));
    for (const m of ev.mistakes) {
      const cls = m.wrong.length ? "wrong" : m.over.length ? "over" : "missed";
      const attrs = [...m.wrong.map((k) => k + "≠"), ...m.over.map((k) => "+" + k),
        ...m.missed.map((k) => "−" + k)].join(" ");
      add(geom[m.id], cls, attrs);
    }
    for (const id of overlapIds) add(geom[id], "overlap", "introduced overlap");
  } else if (col === "target") {
    for (const id of ev.target_edited) add(geom[id], "intended", "edited by target");
  }
}

function setMode(mode) {
  S.mode = mode;
  for (const b of document.querySelectorAll("#modes button"))
    b.classList.toggle("on", b.dataset.mode === mode);
  render();
}

// ----- synced scroll (by slide index, robust to hidden slides) -------------
let syncing = false;

function topIndex(c) {
  const col = bodyEl(c);
  const y = col.scrollTop;
  let idx = 0;
  for (const box of col.children) {
    if (box.offsetTop <= y + 4) idx = +box.dataset.index; else break;
  }
  return idx;
}

function scrollToIndex(c, idx) {
  const col = bodyEl(c);
  let target = null;
  for (const box of col.children) {
    const bi = +box.dataset.index;
    if (bi === idx) { target = box; break; }
    if (bi < idx) target = box;                   // nearest present slide <= idx
    else break;
  }
  if (target) col.scrollTop = target.offsetTop;
}

function onScroll(driver) {
  if (!S.sync || syncing) return;
  syncing = true;
  const idx = topIndex(driver);
  for (const c of COLS) if (c !== driver) scrollToIndex(c, idx);
  requestAnimationFrame(() => { syncing = false; });
}

function syncFrom(driver) {
  const idx = topIndex(driver);
  for (const c of COLS) if (c !== driver) scrollToIndex(c, idx);
}

function setStatus(t) { document.getElementById("status").textContent = t; }

boot();
