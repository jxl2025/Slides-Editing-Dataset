"""compare_struct.py — structural diff + edit-correctness evaluation.

Instead of hashing rendered pixels, this reads the actual OOXML (via parse.py's
IR, which already resolves *effective* geometry/colours/text) and compares decks
element-by-element. Because targets and responses are edits of a *copy* of the
original — no objects added or removed — elements line up by their in-slide
address (s3.e5), so a diff is just a per-(element, attribute) comparison.

Two things are produced:

  * structural signatures per slide (drop-in for the pixel hashes) — a slide is
    "unchanged" iff every element's attributes match.
  * an evaluation of a response against the target, scored on the *diff from the
    original*: did the response change what the target changed (and to the right
    value), and — just as important — leave everything else alone. Overlaps that
    an edit introduces are penalised.

.ppt inputs are converted to .pptx via LibreOffice first (python-pptx can't read
the old binary format).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

import parse
import compare_core

# scalar attributes compared per element
SCALAR_ATTRS = ("x", "y", "w", "h", "rot",
                "fill", "line_color", "line_width", "line_dash", "opacity")
NUM_TOL = {"x": 1e-3, "y": 1e-3, "w": 1e-3, "h": 1e-3, "rot": 0.5,
           "line_width": 0.1, "opacity": 0.01}
OVERLAP_PENALTY = 0.1          # subtracted from the score per introduced overlap


# --------------------------------------------------------------------------- #
# Parsing decks into per-slide element structures
# --------------------------------------------------------------------------- #
def _ensure_pptx(src: str, work_dir: str, force: bool = False) -> str:
    """Return a .pptx path for `src`. Converts .ppt via LibreOffice; with
    `force=True`, also re-saves native .pptx through LibreOffice so every deck
    goes through the *same* serializer (kills representation-only diffs)."""
    if src.lower().endswith(".pptx") and not force:
        return src
    out_dir = os.path.join(work_dir, "converted")
    os.makedirs(out_dir, exist_ok=True)
    st = os.stat(src)
    key = hashlib.sha1(f"{os.path.abspath(src)}|{st.st_mtime_ns}|{force}"
                       .encode()).hexdigest()[:10]
    out = os.path.join(out_dir, key + ".pptx")
    if os.path.exists(out):
        return out
    soffice = compare_core._soffice()
    if not soffice or not os.path.exists(soffice):
        if src.lower().endswith(".pptx"):
            return src                              # can't normalize; use as-is
        raise RuntimeError("need LibreOffice to read .ppt")
    tmp = tempfile.mkdtemp()
    subprocess.run([soffice, "--headless", "--convert-to", "pptx",
                    "--outdir", tmp, src],
                   check=True, capture_output=True, timeout=180)
    produced = os.path.join(tmp, os.path.splitext(os.path.basename(src))[0] + ".pptx")
    if not os.path.exists(produced):
        raise RuntimeError("failed to convert to .pptx")
    shutil.move(produced, out)
    return out


def _runs(el) -> List[Dict[str, Any]]:
    out = []
    if el.text:
        for p in el.text.paragraphs:
            for r in p.runs:
                out.append({"text": r.t, "color": r.color,
                            "bold": bool(r.bold), "italic": bool(r.italic),
                            "underline": bool(r.underline),
                            "highlight": r.highlight})
    return out


def _elem(el) -> Dict[str, Any]:
    g, s = el.geometry, el.style
    return {"id": el.id, "type": el.type,
            "x": round(g.x, 4), "y": round(g.y, 4),
            "w": round(g.w, 4), "h": round(g.h, 4), "rot": round(g.rot, 2),
            "fill": s.fill, "line_color": s.line_color,
            "line_width": s.line_width, "line_dash": s.line_dash,
            "opacity": s.opacity, "runs": _runs(el)}


def parse_struct(src: str, work_dir: str,
                 normalize: bool = False):
    """(-> slides, slide_aspect). Per-slide element dicts plus the deck's true
    slide aspect (w/h), needed to place boxes when the rendered image letterboxes
    the slide. With normalize, the deck is re-serialized through LibreOffice."""
    deck = parse.load(_ensure_pptx(src, work_dir, force=normalize))
    slides = [[_elem(el) for el in slide.elements] for slide in deck.slides]
    aspect = 4 / 3
    if deck.slides:
        sz = deck.slides[0].size or {}
        w, h = sz.get("w_emu"), sz.get("h_emu")
        if w and h:
            aspect = w / h
    return slides, aspect


def struct_payload(slides: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Signatures + light geometry (for drawing boxes) for the frontend."""
    sigs, elements = [], []
    for elems in slides:
        canon = json.dumps(sorted(elems, key=lambda e: e["id"]), sort_keys=True)
        sigs.append(hashlib.sha1(canon.encode("utf-8")).hexdigest())
        elements.append([{"id": e["id"], "x": e["x"], "y": e["y"],
                          "w": e["w"], "h": e["h"], "rot": e["rot"]} for e in elems])
    return {"sigs": sigs, "elements": elements}


# --------------------------------------------------------------------------- #
# Cell-level diff between two versions of a slide
# --------------------------------------------------------------------------- #
Cell = Tuple[str, Any]        # (attribute-key, value)


def _num_close(a, b, tol) -> bool:
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) <= tol


def _val_eq(key: str, a, b) -> bool:
    base = key.split(".")[0]
    if base in NUM_TOL:
        return _num_close(a, b, NUM_TOL[base])
    return a == b


def _elem_cells(e: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an element into {attribute-key: value}."""
    cells = {k: e[k] for k in SCALAR_ATTRS}
    for i, r in enumerate(e.get("runs", [])):
        for prop in ("color", "bold", "italic", "underline", "highlight", "text"):
            cells[f"run{i}.{prop}"] = r[prop]
    return cells


def _changed_cells(v0: Dict[str, Any], v1: Dict[str, Any]) -> Dict[str, Any]:
    """Keys that differ between two versions of the same element -> v1's value."""
    c0, c1 = _elem_cells(v0), _elem_cells(v1)
    out = {}
    for k in set(c0) | set(c1):
        a, b = c0.get(k), c1.get(k)
        if not _val_eq(k, a, b):
            out[k] = b
    # A stroke's width/dash only matter when a border is meaningfully visible
    # (a colour AND a width above hairline). Serializers materialise a default
    # sub-point width / default colour on borderless shapes, which would
    # otherwise read as a phantom edit.
    def _has_border(v):
        w = v.get("line_width") or 0.0
        return v.get("line_color") is not None and w > 1.0
    if not _has_border(v0) and not _has_border(v1):
        out.pop("line_width", None)
        out.pop("line_dash", None)
    return out


# --------------------------------------------------------------------------- #
# Overlaps
# --------------------------------------------------------------------------- #
def _overlaps(elems: List[Dict[str, Any]]) -> Set[frozenset]:
    boxes = [(e["id"], e["x"], e["y"], e["w"], e["h"]) for e in elems
             if e["w"] > 0 and e["h"] > 0]
    out = set()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            _, ax, ay, aw, ah = boxes[i]
            _, bx, by, bw, bh = boxes[j]
            ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
            iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
            if ix * iy > 1e-6:
                out.add(frozenset((boxes[i][0], boxes[j][0])))
    return out


# --------------------------------------------------------------------------- #
# Evaluation: response vs target, scored on the diff from the original
# --------------------------------------------------------------------------- #
def _prf(tp: int, pred: int, gt: int) -> Dict[str, float]:
    p = tp / pred if pred else 1.0
    r = tp / gt if gt else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f}


def evaluate(original: List[List[Dict[str, Any]]],
             target: List[List[Dict[str, Any]]],
             response: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Score a response's edit against the target's edit (both vs the original)."""
    n = min(len(original), len(target), len(response))
    gt_keys: Set[Tuple] = set(); pred_keys: Set[Tuple] = set()
    strict_tp = 0
    bad_overlaps = 0
    slides_out = []

    for si in range(n):
        o = {e["id"]: e for e in original[si]}
        t = {e["id"]: e for e in target[si]}
        r = {e["id"]: e for e in response[si]}
        ids = o.keys() & t.keys() & r.keys()
        aligned = (len(o) == len(t) == len(r) == len(ids))

        gt_here: Dict[str, Dict[str, Any]] = {}
        pred_here: Dict[str, Dict[str, Any]] = {}
        per_elem: Dict[str, Dict[str, List[str]]] = {}
        for eid in ids:
            gch = _changed_cells(o[eid], t[eid])     # target's edit
            pch = _changed_cells(o[eid], r[eid])     # response's edit
            if gch:
                gt_here[eid] = gch
            if pch:
                pred_here[eid] = pch
            missed = [k for k in gch if k not in pch]
            over = [k for k in pch if k not in gch]
            wrong = [k for k in gch if k in pch and not _val_eq(k, gch[k], pch[k])]
            if missed or over or wrong:
                per_elem[eid] = {"missed": missed, "over": over, "wrong": wrong}

        for eid, ch in gt_here.items():
            for k, v in ch.items():
                gt_keys.add((si, eid, k))
        for eid, ch in pred_here.items():
            for k, v in ch.items():
                pred_keys.add((si, eid, k))
                if eid in gt_here and k in gt_here[eid] \
                        and _val_eq(k, gt_here[eid][k], v):
                    strict_tp += 1

        introduced = _overlaps(response[si]) - _overlaps(target[si])
        bad_overlaps += len(introduced)

        slides_out.append({
            "index": si, "aligned": aligned,
            "mistakes": [{"id": eid, **kinds} for eid, kinds in per_elem.items()],
            "target_edited": sorted(gt_here.keys()),
            "overlaps": [sorted(pair) for pair in introduced],
        })

    tp = len(gt_keys & pred_keys)
    scope = _prf(tp, len(pred_keys), len(gt_keys))
    strict = _prf(strict_tp, len(pred_keys), len(gt_keys))
    value_acc = strict_tp / tp if tp else 1.0
    score = max(0.0, strict["f1"] - OVERLAP_PENALTY * bad_overlaps)

    return {
        "metrics": {
            "scope": scope, "strict": strict,
            "value_accuracy": value_acc,
            "bad_overlaps": bad_overlaps,
            "gt_edits": len(gt_keys), "pred_edits": len(pred_keys),
            "score": score,
        },
        "slides": slides_out,
    }
