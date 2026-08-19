"""annotation_core.py — headless domain layer for the annotation UI.

Responsibilities (no UI, no web):
  * parse a .pptx into the content (facts) layer and render slide PNGs;
  * build attribute-link *candidates* with stable ids (so a draft's lineage
    survives a reload);
  * build the compact payload the frontend consumes;
  * load / save the relationships file (the annotation output).

Content is never mutated. Relationships are declarations: a relationship's
declared (attr, value) is independent of its members' actual values (an off-blue
or even red shape may sit in a "fill = blue" relationship — that is allowed).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

import parse
import annotate as A
from slide_ir import Deck, parse_slot_addr, elem_addr, get_attr

# Attributes offered when creating / editing a relationship in the UI.
ATTR_CHOICES = ["fill", "line_color", "font_color", "line_width", "line_dash",
                "opacity", "x", "y", "w", "h", "rot", "text", "img_content", "exists"]
COLOR_ATTRS = {"fill", "line_color", "font_color"}


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
def parse_content(pptx_path: str, work_dir: str, render: bool = True) -> Deck:
    """Parse a .pptx into a facts-layer Deck and (best-effort) render PNGs."""
    os.makedirs(work_dir, exist_ok=True)
    deck = parse.load(pptx_path)
    if render:
        parse.render_slides(pptx_path, deck, work_dir)
    return deck


def content_payload(deck: Deck) -> Dict[str, Any]:
    """Compact, read-only view of the deck for the frontend."""
    slides = []
    for s in deck.slides:
        img = ("/images/" + os.path.basename(s.image)) if s.image else None
        elements = []
        for e in s.elements:
            g = e.geometry
            attrs = {}
            for a in ATTR_CHOICES:
                v = get_attr(e, a)
                attrs[a] = round(v, 5) if isinstance(v, float) else v
            elements.append({
                "id": e.id,
                "addr": elem_addr(s.id, e.id),
                "type": e.type,
                "x": round(g.x, 5), "y": round(g.y, 5),
                "w": round(g.w, 5), "h": round(g.h, 5),
                "fill": e.style.fill,
                "text": (e.text.plain()[:60] if e.text else ""),
                "attrs": attrs,
            })
        slides.append({
            "id": s.id,
            "index": s.index,
            "image": img,
            "aspect": s.size.get("aspect", "16:9"),
            "elements": elements,
        })
    return {"deck_id": deck.deck_id, "source": deck.source, "slides": slides}


# --------------------------------------------------------------------------- #
# Candidates (derived, not persisted; stable ids for lineage)
# --------------------------------------------------------------------------- #
def candidate_id(attr: str, value: Any, members: List[str]) -> str:
    key = f"{attr}|{value}|" + ";".join(sorted(members))
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"cand:{attr}:{h}"


def make_candidates(deck: Deck) -> List[Dict[str, Any]]:
    """Seed candidates and normalize to element addresses + stable ids."""
    out = []
    for c in A.seed_link_candidates(deck):
        members = [_slot_to_elem(m) for m in c["members"]]
        cid = candidate_id(c["attr"], c["value"], members)
        out.append({
            "id": cid,
            "attr": c["attr"],
            "value": c["value"],
            "members": members,
            "slides": _slide_count(members),
        })
    return out


def _slot_to_elem(slot: str) -> str:
    sid, eid, _ = parse_slot_addr(slot)
    return elem_addr(sid, eid)


def _slide_count(members: List[str]) -> int:
    return len({m.split(".")[0] for m in members})


# --------------------------------------------------------------------------- #
# Relationships file (the annotation output)
# --------------------------------------------------------------------------- #
def relationships_path(work_dir: str, deck_id: str, annotator: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in annotator)
    return os.path.join(work_dir, f"{deck_id}.relationships.{safe}.json")


def styles_path(work_dir: str, deck_id: str, annotator: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in annotator)
    return os.path.join(work_dir, f"{deck_id}.styles.{safe}.json")


def content_path(work_dir: str, deck_id: str) -> str:
    return os.path.join(work_dir, f"{deck_id}.content.json")


def candidates_path(work_dir: str, deck_id: str) -> str:
    return os.path.join(work_dir, f"{deck_id}.candidates.json")


def save_candidates(path: str, deck_id: str,
                    candidates: List[Dict[str, Any]]) -> str:
    """Export the seeded candidates so participants start on equal footing."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"deck_id": deck_id, "candidates": candidates},
                  fh, indent=2, ensure_ascii=False)
    return path


def list_participants(work_dir: str, deck_id: str) -> List[str]:
    """Annotator names with a relationships file in the work dir."""
    prefix = f"{deck_id}.relationships."
    out = []
    if os.path.isdir(work_dir):
        for fn in sorted(os.listdir(work_dir)):
            if fn.startswith(prefix) and fn.endswith(".json"):
                out.append(fn[len(prefix):-len(".json")])
    return out


def load_styles(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("styles", [])


def load_relationships(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("relationships", [])


def _backup_write(path: str, payload: Dict[str, Any]) -> str:
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def save_relationships(path: str, deck_id: str, annotator: str,
                       relationships: List[Dict[str, Any]]) -> str:
    """Overwrite the annotator's relationships file (single resumable file)."""
    return _backup_write(path, {"deck_id": deck_id, "annotator": annotator,
                                "relationships": relationships})


def save_styles(path: str, deck_id: str, annotator: str,
                styles: List[Dict[str, Any]]) -> str:
    return _backup_write(path, {"deck_id": deck_id, "annotator": annotator,
                                "styles": styles})


def save_content(path: str, payload: Dict[str, Any]) -> str:
    """Write the read-only structured content (the parsing result)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path
