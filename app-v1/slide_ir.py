"""slide_ir.py — the intermediate representation.

This module defines the *dataset language*: plain data classes that record what
is on a slide (the facts layer) and what an annotator added (the annotation
layer: groups, relational rules, shared-value links, and edit examples).

The IR does not execute, solve, or propagate anything. It only records. A
consumer can read a deck with the standard library alone; the helper classes
here are a convenience, not a requirement, for using the data.

Design commitments (see the pipeline spec):
  * Three kinds of linkage are kept in separate fields:
      - containment grouping ........ Slide.groups
      - relational constraints ...... Slide.rules
      - shared-value links .......... Deck.links   (deck-scoped, may cross slides)
  * Links are multi-way (no master). The link's `members` list is authoritative;
    element attribute values are kept in sync with it as a reader convenience.
  * `exists` is an ordinary boolean attribute so that deletion reuses the same
    link machinery as any other attribute.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Attribute slots that may be linked, and where each lives on an Element.
# --------------------------------------------------------------------------- #
STYLE_ATTRS = ("fill", "line_color", "line_width", "line_dash", "opacity")
GEOM_ATTRS = ("x", "y", "w", "h", "rot")
LINKABLE_ATTRS = STYLE_ATTRS + GEOM_ATTRS + ("font_color", "text", "img_content", "exists")


# --------------------------------------------------------------------------- #
# Facts layer
# --------------------------------------------------------------------------- #
@dataclass
class Run:
    t: str
    font: Optional[str] = None
    size_pt: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color: Optional[str] = None


@dataclass
class Paragraph:
    align: str = "left"
    runs: List[Run] = field(default_factory=list)


@dataclass
class TextContent:
    paragraphs: List[Paragraph] = field(default_factory=list)

    def plain(self) -> str:
        return "\n".join(
            "".join(r.t for r in p.runs) for p in self.paragraphs
        )


@dataclass
class Geometry:
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    rot: float = 0.0


@dataclass
class Style:
    fill: Optional[str] = None
    line_color: Optional[str] = None
    line_width: Optional[float] = None
    line_dash: Optional[str] = None
    opacity: float = 1.0


@dataclass
class ImageRef:
    hash: str
    alt: Optional[str] = None
    ext: Optional[str] = None


@dataclass
class Element:
    id: str
    type: str
    z: int = 0
    geometry: Geometry = field(default_factory=Geometry)
    style: Style = field(default_factory=Style)
    text: Optional[TextContent] = None
    image: Optional[ImageRef] = None
    exists: bool = True


# --------------------------------------------------------------------------- #
# Annotation layer
# --------------------------------------------------------------------------- #
@dataclass
class Group:
    """Containment grouping: members select/move together. Carries no values."""
    id: str
    members: List[str] = field(default_factory=list)  # element ids within the slide


@dataclass
class Rule:
    """Relational constraint over a group's members' geometry (e.g. distribute)."""
    id: str
    type: str                                          # distribute_x, align_left, ...
    members: List[str] = field(default_factory=list)   # element ids within the slide
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Slide:
    id: str
    index: int
    size: Dict[str, Any] = field(default_factory=dict)
    image: Optional[str] = None
    background: Dict[str, Any] = field(default_factory=dict)
    elements: List[Element] = field(default_factory=list)
    groups: List[Group] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)

    def element(self, eid: str) -> Optional[Element]:
        return next((e for e in self.elements if e.id == eid), None)


@dataclass
class Link:
    """A named multi-way shared variable. `members` is authoritative."""
    id: str
    attr: str
    value: Any
    members: List[str] = field(default_factory=list)   # slot addresses, may cross slides
    mode: str = "multi_way"


@dataclass
class Edit:
    """An annotated edit example: prompt + ordered op log (replayed over `base`)."""
    id: str
    prompt: str
    base: str = "as_authored"     # "as_authored" or another edit id
    ops: List[Dict[str, Any]] = field(default_factory=list)
    note: Optional[str] = None

    def add(self, op: Dict[str, Any]) -> int:
        """Append an op; return its index (used for provenance back-references)."""
        self.ops.append(op)
        return len(self.ops) - 1


@dataclass
class Deck:
    deck_id: str
    source: Dict[str, Any] = field(default_factory=dict)
    slides: List[Slide] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    edits: List[Edit] = field(default_factory=list)

    # -- lookup ----------------------------------------------------------- #
    def slide(self, sid: str) -> Optional[Slide]:
        return next((s for s in self.slides if s.id == sid), None)

    def link(self, lid: str) -> Optional[Link]:
        return next((l for l in self.links if l.id == lid), None)

    def resolve_element(self, addr: str) -> Optional[Element]:
        sid, eid = parse_elem_addr(addr)
        s = self.slide(sid)
        return s.element(eid) if s else None

    # -- serialization ---------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return _prune(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Deck":
        return _deck_from_dict(d)

    @staticmethod
    def load(path: str) -> "Deck":
        with open(path, encoding="utf-8") as fh:
            return _deck_from_dict(json.load(fh))

    # -- convenience ------------------------------------------------------ #
    def summary(self) -> str:
        lines = [f"Deck {self.deck_id}  ({self.source.get('origin','?')}, "
                 f"fidelity={self.source.get('fidelity','?')})"]
        for s in self.slides:
            kinds = {}
            for e in s.elements:
                kinds[e.type] = kinds.get(e.type, 0) + 1
            kind_str = ", ".join(f"{k}:{v}" for k, v in sorted(kinds.items()))
            lines.append(f"  {s.id}: {len(s.elements)} elements [{kind_str}]"
                         f"  groups={len(s.groups)} rules={len(s.rules)}")
        lines.append(f"  links={len(self.links)}  edits={len(self.edits)}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Address helpers:  element "s1.e3"      slot "s1.e3:fill"
# --------------------------------------------------------------------------- #
_SLOT_RE = re.compile(r"^([^.]+)\.([^:]+):(.+)$")
_ELEM_RE = re.compile(r"^([^.]+)\.([^:]+)$")


def elem_addr(slide_id: str, elem_id: str) -> str:
    return f"{slide_id}.{elem_id}"


def slot_addr(slide_id: str, elem_id: str, attr: str) -> str:
    return f"{slide_id}.{elem_id}:{attr}"


def parse_elem_addr(addr: str) -> Tuple[str, str]:
    m = _ELEM_RE.match(addr) or _SLOT_RE.match(addr)
    if not m:
        raise ValueError(f"bad element address: {addr!r}")
    return m.group(1), m.group(2)


def parse_slot_addr(addr: str) -> Tuple[str, str, str]:
    m = _SLOT_RE.match(addr)
    if not m:
        raise ValueError(f"bad slot address: {addr!r}")
    return m.group(1), m.group(2), m.group(3)


# --------------------------------------------------------------------------- #
# Reading / writing a single attribute slot on an element.
# --------------------------------------------------------------------------- #
def get_attr(el: Element, attr: str) -> Any:
    if attr in STYLE_ATTRS:
        return getattr(el.style, attr)
    if attr in GEOM_ATTRS:
        return getattr(el.geometry, attr)
    if attr == "exists":
        return el.exists
    if attr == "text":
        return el.text.plain() if el.text else ""
    if attr == "font_color":
        if el.text:
            for p in el.text.paragraphs:
                for r in p.runs:
                    if r.color:
                        return r.color            # first explicit run color
        return None
    if attr == "img_content":
        return el.image.hash if el.image else None
    raise ValueError(f"unknown attribute: {attr!r}")


def set_attr(el: Element, attr: str, value: Any) -> None:
    if attr in STYLE_ATTRS:
        setattr(el.style, attr, value)
    elif attr in GEOM_ATTRS:
        setattr(el.geometry, attr, value)
    elif attr == "exists":
        el.exists = bool(value)
    elif attr == "font_color":
        if el.text:
            for p in el.text.paragraphs:
                for r in p.runs:
                    r.color = value
    elif attr == "img_content":
        if el.image:
            el.image.hash = value
    elif attr == "text":
        # Prototype limitation: setting text collapses to one run (sub-value
        # / rich-run sharing is intentionally out of scope for v0.1).
        el.text = TextContent([Paragraph("left", [Run(t=str(value))])])
    else:
        raise ValueError(f"unknown attribute: {attr!r}")


# --------------------------------------------------------------------------- #
# Monotonic id generation (no collisions within a deck).
# --------------------------------------------------------------------------- #
def _next_id(existing: List[str], prefix: str) -> str:
    n = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for x in existing:
        m = pat.match(x)
        if m:
            n = max(n, int(m.group(1)))
    return f"{prefix}{n + 1}"


def next_element_id(slide: Slide) -> str:
    return _next_id([e.id for e in slide.elements], "e")


def next_link_id(deck: Deck) -> str:
    return _next_id([l.id for l in deck.links], "L")


def next_group_id(slide: Slide) -> str:
    return _next_id([g.id for g in slide.groups], "g")


def next_rule_id(slide: Slide) -> str:
    return _next_id([r.id for r in slide.rules], "r")


def next_edit_id(deck: Deck) -> str:
    return _next_id([e.id for e in deck.edits], "edit")


# --------------------------------------------------------------------------- #
# (De)serialization internals
# --------------------------------------------------------------------------- #
def _prune(obj: Any) -> Any:
    """Drop None values and empty lists/dicts to keep files readable."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pv = _prune(v)
            if pv is None:
                continue
            if isinstance(pv, (list, dict)) and len(pv) == 0:
                # keep 'members'/'ops' style keys only if non-empty; drop otherwise
                continue
            out[k] = pv
        return out
    if isinstance(obj, list):
        return [_prune(v) for v in obj]
    return obj


def _text_from_dict(d: Optional[dict]) -> Optional[TextContent]:
    if not d:
        return None
    paras = [
        Paragraph(p.get("align", "left"),
                  [Run(**r) for r in p.get("runs", [])])
        for p in d.get("paragraphs", [])
    ]
    return TextContent(paras)


def _element_from_dict(d: dict) -> Element:
    return Element(
        id=d["id"],
        type=d["type"],
        z=d.get("z", 0),
        geometry=Geometry(**d.get("geometry", {})),
        style=Style(**d.get("style", {})),
        text=_text_from_dict(d.get("text")),
        image=ImageRef(**d["image"]) if d.get("image") else None,
        exists=d.get("exists", True),
    )


def _deck_from_dict(d: dict) -> Deck:
    slides = []
    for sd in d.get("slides", []):
        slides.append(Slide(
            id=sd["id"],
            index=sd.get("index", 0),
            size=sd.get("size", {}),
            image=sd.get("image"),
            background=sd.get("background", {}),
            elements=[_element_from_dict(e) for e in sd.get("elements", [])],
            groups=[Group(**g) for g in sd.get("groups", [])],
            rules=[Rule(**r) for r in sd.get("rules", [])],
        ))
    links = [Link(**l) for l in d.get("links", [])]
    edits = [Edit(**e) for e in d.get("edits", [])]
    return Deck(
        deck_id=d["deck_id"],
        source=d.get("source", {}),
        slides=slides,
        links=links,
        edits=edits,
    )
