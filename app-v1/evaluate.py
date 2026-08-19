"""evaluate.py — score a participant's annotation against a ground-truth (GT).

Implements the Part B methodology from the participant guide:

  * relationships: per-attribute co-membership pairwise Precision/Recall/F1,
    reported per attribute and aggregated (macro over attributes, micro over
    pooled pairs). Ignores ids, ordering, and the `value` field.
  * styles: best-match cohort (extent) overlap via Jaccard, both directions,
    combined by harmonic mean. Extents are taken as given, or derived from a
    style's intent (intersection of the member sets of its relationships).

Everything keys on the shared element addresses ("s1.e4"), so GT and participant
must annotate the *same* content file.

Typical use (see evaluate_notebook.py for a worked walkthrough):

    import evaluate as ev
    gt   = ev.load("deck.relationships.me.json")
    pred = ev.load("deck.relationships.alice.json")
    report = ev.score_relationships(gt, pred)
    ev.print_report(report)
"""

from __future__ import annotations

import itertools
import json
from typing import Any, Dict, List, Set, Tuple


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load(path: str) -> List[Dict[str, Any]]:
    """Load the `relationships` list from a relationships file."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("relationships", [])


def load_styles(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("styles", [])


# --------------------------------------------------------------------------- #
# Relationship scoring: per-attribute co-membership pairs
# --------------------------------------------------------------------------- #
Pair = Tuple[str, str]


def co_membership_pairs(rels: List[Dict[str, Any]], attr: str) -> Set[Pair]:
    """Unordered element pairs that share a relationship of this attribute."""
    pairs: Set[Pair] = set()
    for r in rels:
        if r.get("attr") != attr:
            continue
        members = sorted(set(r.get("members", [])))
        for a, b in itertools.combinations(members, 2):
            pairs.add((a, b))
    return pairs


def _prf(tp: int, n_pred: int, n_gt: int) -> Dict[str, float]:
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gt if n_gt else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f,
            "tp": tp, "pred": n_pred, "gt": n_gt}


def score_relationships(gt: List[Dict[str, Any]],
                        pred: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-attribute pairwise PRF plus macro and micro aggregates."""
    attrs = sorted({r.get("attr") for r in gt} | {r.get("attr") for r in pred})
    per_attr: Dict[str, Dict[str, float]] = {}
    micro_tp = micro_pred = micro_gt = 0
    for a in attrs:
        g = co_membership_pairs(gt, a)
        p = co_membership_pairs(pred, a)
        tp = len(g & p)
        per_attr[a] = _prf(tp, len(p), len(g))
        micro_tp += tp; micro_pred += len(p); micro_gt += len(g)

    macro_f1 = sum(v["f1"] for v in per_attr.values()) / len(per_attr) if per_attr else 1.0
    macro_p = sum(v["precision"] for v in per_attr.values()) / len(per_attr) if per_attr else 1.0
    macro_r = sum(v["recall"] for v in per_attr.values()) / len(per_attr) if per_attr else 1.0
    return {
        "per_attr": per_attr,
        "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f1},
        "micro": _prf(micro_tp, micro_pred, micro_gt),
    }


# --------------------------------------------------------------------------- #
# Style scoring: best-match extent (cohort) overlap
# --------------------------------------------------------------------------- #
def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    u = len(a | b)
    return len(a & b) / u if u else 0.0


def style_extent(style: Dict[str, Any],
                 rels_by_id: Dict[str, Dict[str, Any]]) -> Set[str]:
    """A style's object cohort: explicit `extent`, else intersection of its
    intent relationships' members."""
    if style.get("extent"):
        return set(style["extent"])
    sets = [set(rels_by_id[i]["members"]) for i in style.get("intent", [])
            if i in rels_by_id]
    if not sets:
        return set()
    out = set(sets[0])
    for s in sets[1:]:
        out &= s
    return out


def style_attrs(style: Dict[str, Any],
                rels_by_id: Dict[str, Dict[str, Any]]) -> Set[str]:
    """The attributes a style bundles (from its intent relationships)."""
    return {rels_by_id[i]["attr"] for i in style.get("intent", [])
            if i in rels_by_id and rels_by_id[i].get("attr")}


def style_cells(style: Dict[str, Any],
                rels_by_id: Dict[str, Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """A style as the set of (attribute, object) cells it covers — the biclique
    rectangle. Empty if either the cohort or the attribute set is empty."""
    ext = style_extent(style, rels_by_id)
    attrs = style_attrs(style, rels_by_id)
    if not ext or not attrs:
        return set()
    return {(a, o) for a in attrs for o in ext}


def _cellsets(styles: List[Dict[str, Any]],
              rels: List[Dict[str, Any]]) -> List[Set[Tuple[str, str]]]:
    by_id = {r.get("id"): r for r in rels}
    out = [style_cells(s, by_id) for s in styles]
    return [c for c in out if c]                       # drop empty styles


def _best_match_mean(src: List[Set], ref: List[Set]) -> float:
    if not src:
        return 1.0 if not ref else 0.0
    if not ref:
        return 0.0
    return sum(max(_jaccard(s, r) for r in ref) for s in src) / len(src)


def score_styles(gt_styles: List[Dict[str, Any]], pred_styles: List[Dict[str, Any]],
                 gt_rels: List[Dict[str, Any]], pred_rels: List[Dict[str, Any]]
                 ) -> Dict[str, Any]:
    """Best-match overlap of styles as attribute x object rectangles. A match
    requires agreement on *both* the objects and the bundled attributes."""
    g = _cellsets(gt_styles, gt_rels)
    p = _cellsets(pred_styles, pred_rels)
    recall = _best_match_mean(g, p)        # each GT style covered by some pred
    precision = _best_match_mean(p, g)     # each pred style justified by some GT
    f = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f,
            "n_gt": len(g), "n_pred": len(p)}


# --------------------------------------------------------------------------- #
# Optional: derive styles from relationships (maximal groups sharing >=2 attrs),
# for when a side did not author styles. Mirrors the UI's style computation.
# --------------------------------------------------------------------------- #
def derive_styles(rels: List[Dict[str, Any]],
                  min_objects: int = 2, min_attrs: int = 2) -> List[Dict[str, Any]]:
    ids = [r.get("id", f"R{i}") for i, r in enumerate(rels)]
    members = {rid: set(r.get("members", [])) for rid, r in zip(ids, rels)}
    objects = set().union(*members.values()) if members else set()
    atoms_of = {o: frozenset(rid for rid in ids if o in members[rid]) for o in objects}

    concepts = {}
    for o in objects:
        intent = atoms_of[o]
        extent = frozenset(x for x in objects if intent <= atoms_of[x])
        closed = frozenset.intersection(*[atoms_of[x] for x in extent]) if extent else intent
        if len(extent) >= min_objects and len(closed) >= min_attrs:
            concepts[(extent, closed)] = None
    return [{"id": "S:" + ",".join(sorted(intent)),
             "intent": sorted(intent), "extent": sorted(extent)}
            for extent, intent in concepts]


# --------------------------------------------------------------------------- #
# Pretty-printing
# --------------------------------------------------------------------------- #
def print_report(rel_report: Dict[str, Any],
                 style_report: Dict[str, Any] | None = None,
                 label: str = "") -> None:
    if label:
        print(f"=== {label} ===")
    print("relationships (per-attribute co-membership F1)")
    print(f"  {'attr':<12} {'P':>6} {'R':>6} {'F1':>6}   (tp/pred/gt)")
    for a, v in sorted(rel_report["per_attr"].items(),
                       key=lambda kv: -kv[1]["f1"]):
        print(f"  {a:<12} {v['precision']:6.2f} {v['recall']:6.2f} {v['f1']:6.2f}"
              f"   ({v['tp']}/{v['pred']}/{v['gt']})")
    m, mi = rel_report["macro"], rel_report["micro"]
    print(f"  {'MACRO':<12} {m['precision']:6.2f} {m['recall']:6.2f} {m['f1']:6.2f}")
    print(f"  {'MICRO':<12} {mi['precision']:6.2f} {mi['recall']:6.2f} {mi['f1']:6.2f}")
    if style_report is not None:
        s = style_report
        print("styles (attribute x object cell overlap, best-match Jaccard)")
        print(f"  P {s['precision']:.2f}  R {s['recall']:.2f}  F1 {s['f1']:.2f}"
              f"   (gt={s['n_gt']}, pred={s['n_pred']})")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Score a participant vs ground truth.")
    ap.add_argument("gt_relationships")
    ap.add_argument("pred_relationships")
    ap.add_argument("--gt-styles")
    ap.add_argument("--pred-styles")
    args = ap.parse_args()

    gt, pred = load(args.gt_relationships), load(args.pred_relationships)
    rel_report = score_relationships(gt, pred)
    style_report = None
    if args.gt_styles and args.pred_styles:
        style_report = score_styles(load_styles(args.gt_styles),
                                    load_styles(args.pred_styles), gt, pred)
    print_report(rel_report, style_report,
                 label=f"{args.pred_relationships} vs {args.gt_relationships}")