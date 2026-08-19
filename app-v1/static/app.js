"use strict";

let gDragged = false;   // set when a drag-select just finished; suppresses the next click

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------
const S = {
  deckId: null, annotator: null,
  content: null, candidates: [], drafts: [],
  attrChoices: [], colorAttrs: [],
  elemByAddr: {},            // "s1.e1" -> element object (+ slideId)
  activeKind: null,          // "candidate" | "draft" | null
  activeId: null,
  styles: [], stylesById: {}, styleSel: null,   // derived styles + selection
  boxColor: "#8b0000",
  overlaySlideId: null,
  hideFilmed: false,
  selectedSlides: new Set(),                     // grid slide selection (for removal)
  slideFocus: null,                              // slide whose players are highlighted
  focusOnly: false,                              // show only entries related to focus
  candSort: "none",                              // "none" | "desc" | "asc"
};

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------
async function boot() {
  const d = await (await fetch("/api/deck")).json();
  S.deckId = d.deck_id; S.annotator = d.annotator;
  S.content = d.content; S.candidates = d.candidates;
  S.drafts = d.relationships || [];
  S.attrChoices = d.attr_choices; S.colorAttrs = d.color_attrs;
  S.participants = d.participants || [];
  S.reviewMode = null;                           // annotator name when reviewing

  for (const s of S.content.slides)
    for (const e of s.elements) S.elemByAddr[e.addr] = { ...e, slideId: s.id };

  document.getElementById("deck-title").textContent =
    `${S.deckId}  ·  ${S.annotator}`;
  setBoxColor(S.boxColor);
  wireControls();
  renderAll();
}

function wireControls() {
  document.getElementById("box-color").addEventListener("input", (e) =>
    setBoxColor(e.target.value));
  document.getElementById("add-draft").addEventListener("click", () => {
    if (S.reviewMode) return;
    const r = createDraft(null); setActive("draft", r.id); renderAll();
  });
  document.getElementById("save-btn").addEventListener("click", save);
  document.getElementById("overlay-close").addEventListener("click", closeOverlay);
  document.getElementById("overlay-backdrop").addEventListener("click", closeOverlay);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeOverlay();
  });

  const hf = document.getElementById("hide-filmed");
  hf.addEventListener("click", () => {
    S.hideFilmed = !S.hideFilmed; hf.classList.toggle("on", S.hideFilmed); renderAll();
  });
  document.getElementById("remove-slides").addEventListener("click", removeSelectedSlidesObjects);
  document.getElementById("focus-only").addEventListener("click", () => {
    S.focusOnly = !S.focusOnly;
    document.getElementById("focus-only").classList.toggle("on", S.focusOnly);
    renderAll();
  });
  document.getElementById("sort-cand").addEventListener("click", () => {
    S.candSort = S.candSort === "desc" ? "asc" : "desc"; renderPanels();
  });

  const rv = document.getElementById("review-select");
  for (const name of S.participants) {
    if (name === S.annotator) continue;          // that's "your annotation"
    const o = el("option"); o.value = name; o.textContent = name; rv.appendChild(o);
  }
  rv.addEventListener("change", (e) => {
    if (e.target.value) enterReview(e.target.value); else exitReview();
  });
  document.getElementById("export-cands").addEventListener("click", exportCandidates);

  // Suppress the click that follows a drag-select.
  document.addEventListener("mousedown", () => { gDragged = false; }, true);

  // Grid: drag-select exposed (non-filmed) slides.
  const wrap = document.getElementById("grid-wrap");
  wrap.addEventListener("scroll", updateOffscreen);
  rubberband(wrap, document.getElementById("grid"),
    () => true, selectSlidesInRect);
}

function setBoxColor(c) {
  S.boxColor = c;
  document.documentElement.style.setProperty("--box-color", c);
  document.getElementById("box-color").value = c;
}

// --------------------------------------------------------------------------
// Active relationship helpers
// --------------------------------------------------------------------------
function activeRel() {
  if (!S.activeId) return null;
  const list = S.activeKind === "candidate" ? S.candidates : S.drafts;
  return list.find((r) => r.id === S.activeId) || null;
}
function setActive(kind, id) {
  S.activeKind = kind; S.activeId = id;
  S.selectedSlides.clear();                       // slide selection is per-relationship
}
function toggleActive(kind, id) {
  if (S.activeKind === kind && S.activeId === id) setActive(null, null);
  else { S.styleSel = null; setActive(kind, id); }
  renderAll();
}
function implicatedSlides(rel) {
  const set = new Set();
  if (rel) for (const m of rel.members) set.add(m.split(".")[0]);
  return set;
}
function membersOnSlide(rel, slideId) {
  return rel ? rel.members.filter((m) => m.split(".")[0] === slideId) : [];
}

// --------------------------------------------------------------------------
// Drafts: create / edit / delete
// --------------------------------------------------------------------------
function nextDraftId() {
  let n = 0;
  for (const r of S.drafts) {
    const m = /^R(\d+)$/.exec(r.id);
    if (m) n = Math.max(n, +m[1]);
  }
  return "R" + (n + 1);
}

function sameSet(a, b) {
  if (a.length !== b.length) return false;
  const s = new Set(a);
  return b.every((x) => s.has(x));
}

// A candidate is "consumed" if an unrevised draft was promoted from it.
function isConsumed(c) {
  return S.drafts.some((d) => d.derived_from === c.id &&
                              d.attr === c.attr && sameSet(d.members, c.members));
}

// Ephemeral popup (~1s).
let toastTimer = null;
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 1000);
}

// Drop drafts that are complete duplicates (same attr + member set). Keeps first.
function dedupeDrafts() {
  const seen = new Set(); const kept = []; let removed = 0;
  for (const d of S.drafts) {
    const key = d.attr + "|" + [...d.members].sort().join(",");
    if (seen.has(key)) { removed++; continue; }
    seen.add(key); kept.push(d);
  }
  S.drafts = kept;
  if (S.activeKind === "draft" && !S.drafts.some((d) => d.id === S.activeId))
    setActive(null, null);
  return removed;
}

// Promote a candidate to a draft, unless an identical draft already exists.
function promoteCandidate(c) {
  if (S.reviewMode) return;
  const existing = S.drafts.find((d) => d.attr === c.attr && sameSet(d.members, c.members));
  if (existing) {
    showToast(`Already a draft (${existing.id})`);
    setActive("draft", existing.id); renderAll();
    return;
  }
  const r = createDraft(c); setActive("draft", r.id); renderAll();
}
function createDraft(fromCandidate) {
  const r = fromCandidate
    ? { id: nextDraftId(), attr: fromCandidate.attr,
        members: [...fromCandidate.members], derived_from: fromCandidate.id }
    : { id: nextDraftId(), attr: "fill", members: [], derived_from: null };
  S.drafts.push(r);
  return r;
}
function deleteDraft(id) {
  if (S.reviewMode) return;
  S.drafts = S.drafts.filter((r) => r.id !== id);
  if (S.activeKind === "draft" && S.activeId === id) setActive(null, null);
  renderAll();
}

// Toggle an element's membership in the active relationship (copy-on-edit).
function toggleMember(addr) {
  if (S.reviewMode) return;
  let rel = activeRel();
  if (!rel) { flashHint("Select a candidate or create a draft first."); return; }
  if (S.activeKind === "candidate") {           // copy-on-edit -> new draft
    rel = createDraft(rel);
    setActive("draft", rel.id);
  }
  const i = rel.members.indexOf(addr);
  if (i >= 0) rel.members.splice(i, 1);
  else rel.members.push(addr);
  renderAll();
  if (S.overlaySlideId) renderOverlay();
}

// Add-only membership (used by drag-select); copy-on-edit from a candidate.
function addMember(addr) {
  if (S.reviewMode) return;
  let rel = activeRel();
  if (!rel) return;
  if (S.activeKind === "candidate") { rel = createDraft(rel); setActive("draft", rel.id); }
  if (!rel.members.includes(addr)) rel.members.push(addr);
}

// Remove, from the active relationship, all objects on the selected slides.
function removeSelectedSlidesObjects() {
  if (S.reviewMode) return;
  let rel = activeRel();
  if (!rel || S.selectedSlides.size === 0) return;
  if (S.activeKind === "candidate") {              // edit happens on a copy
    rel = createDraft(rel); setActive("draft", rel.id);
  }
  rel.members = rel.members.filter((m) => !S.selectedSlides.has(m.split(".")[0]));
  S.selectedSlides.clear();
  renderAll();
}

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------
function renderAll() { renderTopbar(); renderPanels(); renderGrid(); }

function renderTopbar() {
  const reviewing = !!S.reviewMode;
  document.getElementById("deck-title").textContent =
    reviewing ? `${S.deckId}  ·  reviewing ${S.reviewMode}` : `${S.deckId}  ·  ${S.annotator}`;
  const addBtn = document.getElementById("add-draft");
  if (addBtn) addBtn.disabled = reviewing;
  const saveBtn = document.getElementById("save-btn");
  if (saveBtn) saveBtn.disabled = reviewing;
  document.body.classList.toggle("reviewing", reviewing);

  const info = document.getElementById("active-info");
  if (S.styleSel && S.stylesById[S.styleSel]) {
    const s = S.stylesById[S.styleSel];
    const nSlides = new Set(s.extent.map((m) => m.split(".")[0])).size;
    info.textContent =
      `style S: ${s.intent.length} attrs · ${s.extent.length} objects · ${nSlides} slides`;
    return;
  }
  const rel = activeRel();
  if (!rel) { info.textContent = "no relationship selected"; return; }
  const nSlides = implicatedSlides(rel).size;
  const valTxt = S.activeKind === "draft" ? summaryText(rel) : fmtVal(rel.value);
  info.textContent =
    `${rel.id}: ${rel.attr} = ${valTxt} · ` +
    `${rel.members.length} objects · ${nSlides} slides`;
}

function renderPanels() {
  computeStyles();
  S._H = computeHighlights();
  S._F = slideFocusSets();
  const focusing = S.focusOnly && S.slideFocus;

  let cands = S.candidates;
  if (focusing) cands = cands.filter((c) => S._F.cands.has(c.id));
  if (S.candSort !== "none") {
    cands = [...cands].sort((a, b) =>
      S.candSort === "desc" ? b.members.length - a.members.length
                            : a.members.length - b.members.length);
  }
  // consumed (promoted-as-is) candidates sink to the end regardless of sort
  cands = [...cands].sort((a, b) => (isConsumed(a) ? 1 : 0) - (isConsumed(b) ? 1 : 0));
  const cl = document.getElementById("candidate-list"); cl.innerHTML = "";
  for (const c of cands) cl.appendChild(candidateEntry(c));

  let drafts = S.drafts;
  if (focusing) drafts = drafts.filter((d) => S._F.drafts.has(d.id));
  const dl = document.getElementById("draft-list"); dl.innerHTML = "";
  for (const r of drafts) dl.appendChild(draftEntry(r));

  let styles = S.styles;
  if (focusing) styles = styles.filter((s) => S._F.styles.has(s.id));
  const sl = document.getElementById("style-list"); sl.innerHTML = "";
  styles.forEach((s) => sl.appendChild(styleEntry(s, S.styles.indexOf(s))));

  const rm = document.getElementById("remove-slides");
  if (rm) rm.disabled = !(activeRel() && S.selectedSlides.size > 0);
  const fo = document.getElementById("focus-only");
  if (fo) fo.disabled = !S.slideFocus;
}

function candidateEntry(c) {
  const hl = S._F && S._F.cands.has(c.id) ? " hl" : "";
  const consumed = isConsumed(c) ? " consumed" : "";
  const e = el("div", "entry" + hl + consumed + (isActive("candidate", c.id) ? " active" : ""));
  const promote = el("button", "promote"); promote.textContent = "➕";
  promote.title = "promote to draft";
  promote.addEventListener("click", (ev) => {
    ev.stopPropagation();
    promoteCandidate(c);
  });
  const h = head(c); h.appendChild(promote);
  e.appendChild(h);
  e.appendChild(meta(`${c.members.length} objects · ${c.slides} slides`));
  e.addEventListener("click", () => toggleActive("candidate", c.id));
  return e;
}

function draftEntry(r) {
  const hl = ((S._H && S._H.draftHL.has(r.id)) ||
              (S._F && S._F.drafts.has(r.id))) ? " hl" : "";
  const e = el("div", "entry draft" + hl + (isActive("draft", r.id) ? " active" : ""));
  const ro = !!S.reviewMode;                      // read-only while reviewing

  const h = el("div");
  h.appendChild(document.createTextNode(r.id + "  "));
  const a = el("span", "attr"); a.textContent = r.attr; h.appendChild(a);
  h.appendChild(document.createTextNode(" = "));
  h.appendChild(summaryNode(r));                 // derived interval / value-set
  if (!ro) {
    const del = el("button", "del"); del.textContent = "🗑"; del.title = "delete draft";
    del.addEventListener("click", (ev) => { ev.stopPropagation(); deleteDraft(r.id); });
    h.appendChild(del);                          // rightmost (float right)
  }
  if (spanWarn(r)) {
    const w = el("span", "warn"); w.textContent = "⚠";
    w.title = "wide value span — members vary a lot on this attribute";
    h.appendChild(w);                            // floats to the left of the trash
  }
  e.appendChild(h);

  if (!ro) {
    // choose which attribute this link is about (value is derived from members)
    const row = el("div", "editrow");
    const sel = el("select");
    for (const attr of S.attrChoices) {
      const o = el("option"); o.value = attr; o.textContent = attr;
      if (attr === r.attr) o.selected = true; sel.appendChild(o);
    }
    sel.addEventListener("click", (ev) => ev.stopPropagation());
    sel.addEventListener("change", (ev) => { r.attr = ev.target.value; renderAll(); });
    row.appendChild(sel);
    e.appendChild(row);
  }

  const nSlides = implicatedSlides(r).size;
  e.appendChild(meta(`${r.members.length} objects · ${nSlides} slides`));
  if (!ro && r.derived_from)
    e.appendChild(el("div", "lineage")).textContent = "from " + r.derived_from;

  e.addEventListener("click", () => toggleActive("draft", r.id));
  return e;
}

function head(r) {                                // used by candidates (exact value)
  const h = el("div");
  if (S.colorAttrs.includes(r.attr) && looksHex(r.value)) {
    const sw = el("span", "swatch"); sw.style.background = r.value; h.appendChild(sw);
  }
  const a = el("span", "attr"); a.textContent = r.attr; h.appendChild(a);
  const v = el("span", "val"); v.textContent = " = " + fmtVal(r.value); h.appendChild(v);
  return h;
}

// --------------------------------------------------------------------------
// Derived value summaries: interval [min,max] for numeric attrs, set of
// distinct values (swatches for colors) for categorical attrs.
// --------------------------------------------------------------------------
const NUMERIC_ATTRS = new Set(["line_width", "opacity", "x", "y", "w", "h", "rot"]);

function memberValues(rel) {
  const out = [];
  for (const m of rel.members) {
    const e = S.elemByAddr[m];
    const v = e && e.attrs ? e.attrs[rel.attr] : null;
    if (v !== null && v !== undefined && v !== "") out.push(v);
  }
  return out;
}

function summarize(rel) {
  const vals = memberValues(rel);
  if (!vals.length) return { kind: "empty" };
  if (NUMERIC_ATTRS.has(rel.attr)) {
    const nums = vals.map(Number).filter((x) => !Number.isNaN(x));
    if (!nums.length) return { kind: "empty" };
    return { kind: "interval", min: Math.min(...nums), max: Math.max(...nums) };
  }
  const distinct = [...new Set(vals.map(String))];
  return { kind: "set", values: distinct };
}

function summaryText(rel) {
  const s = summarize(rel);
  if (s.kind === "empty") return "∅";
  if (s.kind === "interval")
    return s.min === s.max ? fmtNum(s.min) : `[${fmtNum(s.min)}, ${fmtNum(s.max)}]`;
  if (rel.attr === "img_content")
    return s.values.length === 1 ? "same image" : `${s.values.length} distinct images`;
  const vals = s.values.map(trunc);
  return vals.length === 1 ? vals[0]
    : `{${vals.slice(0, 4).join(", ")}${vals.length > 4 ? ", …" : ""}}`;
}

function summaryNode(rel) {
  const span = el("span", "val");
  const s = summarize(rel);
  if (s.kind === "set" && S.colorAttrs.includes(rel.attr)) {
    for (const v of s.values.slice(0, 6)) {
      if (looksHex(v)) { const sw = el("span", "swatch"); sw.style.background = v; span.appendChild(sw); }
    }
    span.appendChild(document.createTextNode(summaryText(rel)));
  } else {
    span.textContent = summaryText(rel);
  }
  return span;
}

function fmtNum(v) {
  return String(Math.round(v * 1000) / 1000);
}

// Warn when a numeric interval spans "too much" (per-attribute thresholds).
const SPAN_WARN = { x: 0.15, y: 0.15, w: 0.15, h: 0.15, rot: 20, opacity: 0.25, line_width: 3 };
function spanWarn(rel) {
  if (!NUMERIC_ATTRS.has(rel.attr)) return false;
  const s = summarize(rel);
  const thr = SPAN_WARN[rel.attr];
  return s.kind === "interval" && thr != null && (s.max - s.min) > thr;
}
function trunc(s, n = 22) {
  s = String(s);
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// --------------------------------------------------------------------------
// Styles = maximal bicliques (formal concepts) over the drafts
// --------------------------------------------------------------------------
function computeStyles() {
  const drafts = S.drafts.filter((d) => d.members && d.members.length);
  const byId = {}; drafts.forEach((d) => (byId[d.id] = d));

  // object -> set of draft ids it belongs to
  const objAttrs = {};
  for (const d of drafts)
    for (const o of d.members) (objAttrs[o] ||= new Set()).add(d.id);

  // concept intents = closure-under-intersection of the object attribute-sets
  const intents = []; const seen = new Set();
  const add = (set) => {
    const key = [...set].sort().join(",");
    if (!seen.has(key)) { seen.add(key); intents.push(set); return true; }
    return false;
  };
  Object.values(objAttrs).forEach((s) => add(new Set(s)));
  for (let changed = true; changed; ) {
    changed = false; const cur = [...intents];
    for (let i = 0; i < cur.length; i++)
      for (let j = i + 1; j < cur.length; j++) {
        const inter = new Set([...cur[i]].filter((x) => cur[j].has(x)));
        if (add(inter)) changed = true;
      }
  }

  // build concepts with a floor of >=2 objects and >=2 attributes
  const uniq = {};
  for (const Y of intents) {
    if (Y.size < 2) continue;
    let X = null;
    for (const did of Y) {
      const mem = new Set(byId[did].members);
      X = X === null ? mem : new Set([...X].filter((o) => mem.has(o)));
    }
    if (!X || X.size < 2) continue;
    const intent = [...Y].sort();
    uniq["S:" + intent.join(",")] = { id: "S:" + intent.join(","), intent, extent: [...X] };
  }

  const list = Object.values(uniq).sort(
    (a, b) => b.extent.length - a.extent.length || b.intent.length - a.intent.length);

  // immediate parents (more general: strict subset of intent, maximal)
  for (const c of list) {
    const anc = list.filter((p) => p !== c && isSubset(p.intent, c.intent));
    c.parents = anc
      .filter((p) => !anc.some((q) => q !== p && isSubset(p.intent, q.intent)))
      .map((p) => p.id);
  }

  S.styles = list;
  S.stylesById = Object.fromEntries(list.map((s) => [s.id, s]));
  if (S.styleSel && !S.stylesById[S.styleSel]) S.styleSel = null;   // drop stale selection
}

function computeHighlights() {
  const draftHL = new Set(); const styleBg = {}; let styleActive = null;

  if (S.styleSel && S.stylesById[S.styleSel]) {          // a style is selected
    const s = S.stylesById[S.styleSel]; styleActive = s.id;
    s.intent.forEach((id) => draftHL.add(id));
    const dep = ancestorDepth(s);
    for (const [sid, d] of Object.entries(dep)) styleBg[sid] = lightnessCss(d);
  }
  if (S.activeKind === "draft" && S.activeId) {          // a draft is active
    S.styles
      .filter((s) => s.intent.includes(S.activeId))
      .sort((a, b) => a.extent.length - b.extent.length)
      .forEach((s, i) => {
        if (s.id !== styleActive && !(s.id in styleBg)) styleBg[s.id] = lightnessCss(i + 1);
      });
  }
  return { draftHL, styleBg, styleActive };
}

// Which candidates / drafts / styles involve the focused slide.
function slideFocusSets() {
  const out = { cands: new Set(), drafts: new Set(), styles: new Set() };
  const sid = S.slideFocus;
  if (!sid) return out;
  const on = (members) => members.some((m) => m.split(".")[0] === sid);
  for (const c of S.candidates) if (on(c.members)) out.cands.add(c.id);
  for (const d of S.drafts) if (on(d.members)) out.drafts.add(d.id);
  for (const s of S.styles) if (on(s.extent)) out.styles.add(s.id);
  return out;
}

function ancestorDepth(s) {
  const depth = {}; let frontier = [...(s.parents || [])]; let d = 1;
  while (frontier.length && d < 60) {
    const next = [];
    for (const pid of frontier) {
      if (depth[pid] === undefined) depth[pid] = d;
      for (const gp of S.stylesById[pid]?.parents || []) next.push(gp);
    }
    frontier = next; d++;
  }
  return depth;
}

function styleEntry(s, idx) {
  const H = S._H;
  const active = H && s.id === H.styleActive;
  const focusHl = S._F && S._F.styles.has(s.id) ? " hl" : "";
  const e = el("div", "entry style" + (active ? " active" : "") + focusHl);
  if (!active && H && H.styleBg[s.id]) e.style.background = H.styleBg[s.id];

  const h = el("div");
  const label = el("span", "attr"); label.textContent = "S" + (idx + 1); h.appendChild(label);
  e.appendChild(h);

  const intent = el("div", "meta");
  s.intent.forEach((did, i) => {
    const d = S.drafts.find((x) => x.id === did);
    if (i) intent.appendChild(document.createTextNode(", "));
    if (d) {
      const sm = summarize(d);
      if (sm.kind === "set" && S.colorAttrs.includes(d.attr)) {
        for (const v of sm.values.slice(0, 3))
          if (looksHex(v)) { const sw = el("span", "swatch"); sw.style.background = v; intent.appendChild(sw); }
      }
      intent.appendChild(document.createTextNode(`${d.attr}=${summaryText(d)}`));
    } else {
      intent.appendChild(document.createTextNode(did));
    }
  });
  e.appendChild(intent);
  e.appendChild(meta(`${s.extent.length} objects · ${s.intent.length} attrs`));

  e.addEventListener("click", () => toggleStyle(s.id));
  return e;
}

function toggleStyle(id) {
  S.styleSel = S.styleSel === id ? null : id;
  renderAll();          // also refresh grid to show/hide the style's objects
}

function isSubset(sub, sup) {
  return sub.length < sup.length && sub.every((x) => sup.includes(x));
}
function lightnessCss(depth) {
  const L = Math.max(45, 90 - depth * 9);   // depth 1 lightest, deeper darker
  return `hsl(210, 42%, ${L}%)`;
}

function renderGrid() {
  const members = gridMembers();                 // array of addrs, or null
  const memberSet = members ? new Set(members) : null;
  const imp = new Set(members ? members.map((m) => m.split(".")[0]) : []);
  const grid = document.getElementById("grid"); grid.innerHTML = "";

  for (const s of S.content.slides) {
    const implicated = imp.has(s.id);
    if (memberSet && S.hideFilmed && !implicated) continue;   // show only highlighted

    const tile = el("div", "tile");
    tile.dataset.slide = s.id;
    tile.dataset.hl = memberSet && implicated ? "1" : "0";
    if (S.selectedSlides.has(s.id)) tile.classList.add("selected");
    const ar = el("div", "ar");
    ar.style.paddingTop = aspectPct(s.aspect) + "%";
    tile.appendChild(ar);

    if (s.image) { const img = el("img"); img.src = s.image; tile.appendChild(img); }
    else { const ni = el("div", "noimg"); ni.textContent = "(no render)"; tile.appendChild(ni); }

    if (memberSet) {
      for (const m of members) {
        if (m.split(".")[0] !== s.id) continue;
        const e = S.elemByAddr[m]; if (!e) continue;
        tile.appendChild(boxDiv(e, "box"));
      }
      if (!implicated) tile.appendChild(el("div", "film"));
    }

    const lab = el("div", "label" + (s.id === S.slideFocus ? " focus" : ""));
    lab.textContent = s.id;
    lab.title = "show related candidates / drafts / styles";
    lab.addEventListener("click", (ev) => {
      ev.stopPropagation();                        // don't open the overlay
      S.slideFocus = S.slideFocus === s.id ? null : s.id;
      renderAll();
    });
    tile.appendChild(lab);
    if (s.id === S.slideFocus) tile.classList.add("focus");
    tile.addEventListener("click", () => {
      if (gDragged) { gDragged = false; return; }   // was a drag-select, not a click
      openOverlay(s.id);
    });
    grid.appendChild(tile);
  }
  updateOffscreen();
}

// Objects the grid should highlight: a selected style's extent (the shared
// cohort = intersection of its links' members), else the active relationship.
function gridMembers() {
  if (S.styleSel && S.stylesById[S.styleSel]) return S.stylesById[S.styleSel].extent;
  const rel = activeRel();
  return rel ? rel.members : null;
}

// Generic rubber-band drag over `listenEl`; band drawn in `contentEl` coords.
// onSelect(rectInContentCoords) is called on release if the pointer moved.
function rubberband(listenEl, contentEl, canStart, onSelect) {
  listenEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || !canStart(e)) return;
    e.preventDefault();                            // stop the browser text-selection
    document.body.classList.add("dragging");
    const cr = contentEl.getBoundingClientRect();
    const x0 = e.clientX - cr.left, y0 = e.clientY - cr.top;
    const band = el("div", "rubber"); contentEl.appendChild(band);
    let moved = false, rect = null;
    const move = (ev) => {
      const x1 = ev.clientX - cr.left, y1 = ev.clientY - cr.top;
      if (Math.abs(x1 - x0) + Math.abs(y1 - y0) > 5) moved = true;
      const L = Math.min(x0, x1), T = Math.min(y0, y1);
      rect = { L, T, R: Math.max(x0, x1), B: Math.max(y0, y1) };
      band.style.left = L + "px"; band.style.top = T + "px";
      band.style.width = (rect.R - L) + "px"; band.style.height = (rect.B - T) + "px";
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.classList.remove("dragging");
      band.remove();
      if (moved && rect) { gDragged = true; onSelect(rect); }
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

function _intersects(a, el0) {
  const L = el0.offsetLeft, T = el0.offsetTop;
  const R = L + el0.offsetWidth, B = T + el0.offsetHeight;
  return !(R < a.L || L > a.R || B < a.T || T > a.B);
}

// Grid drag: select exposed (non-filmed) slides for later object removal.
function selectSlidesInRect(rect) {
  const grid = document.getElementById("grid");
  for (const tile of grid.querySelectorAll(".tile")) {
    if (tile.dataset.hl === "1" && _intersects(rect, tile))
      S.selectedSlides.add(tile.dataset.slide);
  }
  renderAll();
}

// Off-screen indicators: count highlighted slides above / below the viewport.
function updateOffscreen() {
  const wrap = document.getElementById("grid-wrap");
  const up = document.getElementById("offscreen-up");
  const down = document.getElementById("offscreen-down");
  if (!wrap || !up || !down) return;
  const top = wrap.scrollTop, bottom = top + wrap.clientHeight;
  let above = 0, below = 0, firstBelow = null, lastAbove = null;
  for (const tile of wrap.querySelectorAll(".tile[data-hl='1']")) {
    const t = tile.offsetTop, b = t + tile.offsetHeight;
    if (b < top) { above++; lastAbove = tile; }
    else if (t > bottom) { below++; if (!firstBelow) firstBelow = tile; }
  }
  _setOffscreen(up, above, lastAbove, "▲");
  _setOffscreen(down, below, firstBelow, "▼");
}
function _setOffscreen(elm, count, target, arrow) {
  if (count > 0 && target) {
    elm.textContent = `${arrow} ${count} highlighted`;
    elm.classList.remove("hidden");
    elm.onclick = () => target.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    elm.classList.add("hidden"); elm.onclick = null;
  }
}

// --------------------------------------------------------------------------
// Overlay (enlarged slide, click to select)
// --------------------------------------------------------------------------
function openOverlay(slideId) { S.overlaySlideId = slideId; renderOverlay(); }
function closeOverlay() {
  S.overlaySlideId = null;
  document.getElementById("overlay").classList.add("hidden");
}

function renderOverlay() {
  const s = S.content.slides.find((x) => x.id === S.overlaySlideId);
  if (!s) return closeOverlay();
  const rel = activeRel();

  // Size the slide canvas, leaving room for the object-list panel.
  const [rw, rh] = aspectWH(s.aspect);
  const listW = 210, gap = 12, pad = 24;
  let W = Math.min(window.innerWidth * 0.92 - listW - gap - pad, 1100);
  let H = W * (rh / rw);
  const maxH = window.innerHeight * 0.8;
  if (H > maxH) { H = maxH; W = H * (rw / rh); }

  const memberSet = new Set(rel ? rel.members : []);
  const boxByAddr = {}, listByAddr = {};
  const hi = (addr, on) => {
    if (boxByAddr[addr]) boxByAddr[addr].classList.toggle("hover", on);
    if (listByAddr[addr]) listByAddr[addr].classList.toggle("hover", on);
  };

  // --- slide canvas with labelled, clickable boxes ---
  const canvas = document.getElementById("overlay-canvas");
  canvas.innerHTML = "";
  canvas.style.width = W + "px"; canvas.style.height = H + "px";
  if (s.image) {
    const img = el("img"); img.src = s.image;
    img.style.width = W + "px"; img.style.height = H + "px"; canvas.appendChild(img);
  } else {
    const ni = el("div", "noimg");
    ni.style.width = W + "px"; ni.style.height = H + "px";
    ni.textContent = "(no render)"; canvas.appendChild(ni);
  }
  for (const e of s.elements) {
    const p = boxDiv(e, "pick" + (memberSet.has(e.addr) ? " member" : ""));
    p.dataset.addr = e.addr;
    const lab = el("div", "plabel"); lab.textContent = e.id; p.appendChild(lab);
    p.title = `${e.id} · ${e.type}${e.text ? " · " + e.text : ""}`;
    p.addEventListener("click", () => {
      if (gDragged) { gDragged = false; return; }
      toggleMember(e.addr);
    });
    p.addEventListener("mouseenter", () => hi(e.addr, true));
    p.addEventListener("mouseleave", () => hi(e.addr, false));
    boxByAddr[e.addr] = p; canvas.appendChild(p);
  }

  // --- object list (scrollable; click to toggle, hover to cross-highlight) ---
  const list = document.getElementById("overlay-objlist");
  list.innerHTML = ""; list.style.maxHeight = H + "px";
  for (const e of s.elements) {
    const it = el("div", "objentry" + (memberSet.has(e.addr) ? " member" : ""));
    it.dataset.addr = e.addr;
    const oid = el("span", "oid"); oid.textContent = e.id; it.appendChild(oid);
    if (looksHex(e.fill)) {
      const sw = el("span", "oswatch"); sw.style.background = e.fill; it.appendChild(sw);
    }
    const ot = el("span", "otype");
    ot.textContent = e.type + (e.text ? " · " + e.text : ""); it.appendChild(ot);
    it.addEventListener("click", () => {
      if (gDragged) { gDragged = false; return; }
      toggleMember(e.addr);
    });
    it.addEventListener("mouseenter", () => hi(e.addr, true));
    it.addEventListener("mouseleave", () => hi(e.addr, false));
    listByAddr[e.addr] = it; list.appendChild(it);
  }

  // Drag-select: rubber-band on the slide (start on empty area, not on a box),
  // and on the object list — both ADD the covered objects to the relationship.
  if (!canvas._rb) {
    canvas._rb = true;
    rubberband(canvas, canvas,
      (ev) => !ev.target.classList.contains("pick") && !ev.target.classList.contains("plabel"),
      (rect) => { _addObjectsInRect(canvas, rect); });
    rubberband(list, list, () => true, (rect) => { _addObjectsInRect(list, rect); });
  }

  const hint = document.getElementById("overlay-hint");
  hint.textContent = rel
    ? `Editing ${rel.id} (${rel.attr} = ${fmtVal(rel.value)}) — click a box or list item to add/remove`
    : "Select a candidate or create a draft to start selecting objects";
  document.getElementById("overlay").classList.remove("hidden");
}

function flashHint(msg) {
  const hint = document.getElementById("overlay-hint");
  if (hint) hint.textContent = msg;
}

// Add every addr-tagged child (.pick or .objentry) intersecting the drag rect.
function _addObjectsInRect(container, rect) {
  if (!activeRel()) { flashHint("Select a candidate or create a draft first."); return; }
  let n = 0;
  for (const node of container.querySelectorAll("[data-addr]")) {
    if (_intersects(rect, node)) { addMember(node.dataset.addr); n++; }
  }
  if (n) { renderAll(); renderOverlay(); }
}

// --------------------------------------------------------------------------
// Save
// --------------------------------------------------------------------------
// --------------------------------------------------------------------------
// Reviewing a participant's annotation (read-only). Your own drafts are stashed
// and restored on exit; nothing about the participant's file is editable/saved.
// --------------------------------------------------------------------------
async function enterReview(name) {
  if (!S.reviewMode) S._ownDrafts = S.drafts;
  const d = await (await fetch("/api/review?annotator=" + encodeURIComponent(name))).json();
  if (!d.ok) { showToast("could not load " + name); return; }
  S.reviewMode = name;
  S.drafts = (d.relationships || []).map((r, i) => ({
    id: r.id || "R" + (i + 1), attr: r.attr, members: r.members || [],
    derived_from: r.derived_from ?? null,
  }));
  setActive(null, null);
  document.getElementById("review-select").value = name;
  renderAll();
  showToast(`Reviewing ${name} (read-only)`);
}

function exitReview() {
  if (!S.reviewMode) return;
  S.reviewMode = null;
  S.drafts = S._ownDrafts || [];
  setActive(null, null);
  document.getElementById("review-select").value = "";
  renderAll();
}

async function exportCandidates() {
  const res = await (await fetch("/api/export_candidates", { method: "POST" })).json();
  showToast(res.ok ? `Exported ${res.count} candidates` : "export failed");
}

async function save() {
  if (S.reviewMode) { showToast("Reviewing — exit review to save your own work"); return; }
  const removed = dedupeDrafts();                // clean logs before persisting
  if (removed) renderAll();
  const payload = {
    relationships: S.drafts.map((r) => ({
      id: r.id, attr: r.attr, value: summarize(r),
      members: r.members, derived_from: r.derived_from ?? null,
    })),
    styles: S.styles.map((s) => ({ id: s.id, intent: s.intent, extent: s.extent })),
  };
  const res = await (await fetch("/api/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })).json();
  const st = document.getElementById("save-status");
  st.textContent = res.ok
    ? `saved ${res.counts.relationships} links, ${res.counts.styles} styles · ${new Date().toLocaleTimeString()}`
    : "save failed";
  if (res.ok && removed) showToast(`Removed ${removed} duplicate draft${removed > 1 ? "s" : ""}`);
}

// --------------------------------------------------------------------------
// Small utilities
// --------------------------------------------------------------------------
function el(tag, cls) { const n = document.createElement(tag); if (cls) n.className = cls; return n; }
function isActive(kind, id) { return S.activeKind === kind && S.activeId === id; }
function meta(txt) { const m = el("div", "meta"); m.textContent = txt; return m; }
function boxDiv(e, cls) {
  const b = el("div", cls);
  b.style.left = e.x * 100 + "%"; b.style.top = e.y * 100 + "%";
  b.style.width = e.w * 100 + "%"; b.style.height = e.h * 100 + "%";
  return b;
}
function fmtVal(v) { return v === "" || v == null ? "∅" : trunc(String(v), 24); }
function looksHex(v) { return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v); }
function aspectWH(a) {
  const m = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(a || "16:9");
  return m ? [parseFloat(m[1]), parseFloat(m[2])] : [16, 9];
}
function aspectPct(a) { const [w, h] = aspectWH(a); return (h / w) * 100; }

boot();
