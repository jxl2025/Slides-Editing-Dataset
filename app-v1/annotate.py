"""annotate.py — the annotation layer on top of a parsed Deck.

This is the tooling a human uses (typically from the notebook) to record:
  (1) attribute linkages — shared-value links, plus containment groups and
      relational rules; and
  (2) edit examples — a prompt and an ordered op log with provenance.

The module also provides candidate seeding (so annotators confirm/reject equal
values rather than finding them by hand), a light validator for QA, and a
collision-free save.

Nothing here solves or propagates at runtime in the dataset sense — the one
exception is `delete_element`, which *records* the propagation an existence link
implies (as explicit ops tagged with provenance), because that is annotation,
not runtime behaviour.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import slide_ir as ir
from slide_ir import (
    Deck, Slide, Element, Link, Group, Rule, Edit,
    slot_addr, elem_addr, parse_slot_addr, parse_elem_addr,
    get_attr, set_attr,
)

# Attributes worth auto-seeding as link candidates (element-level only).
# Attributes worth auto-seeding as link candidates — the full palette except
# `exists` (always true -> one degenerate all-object cluster).
SEED_ATTRS = ("fill", "line_color", "font_color", "line_width", "line_dash",
              "opacity", "x", "y", "w", "h", "rot", "text", "img_content")
# Numeric tolerance for treating two values as "equal" when seeding.
NUM_TOL = 1e-4


# ===========================================================================
# (1a) Candidate seeding
# ===========================================================================
def seed_link_candidates(deck: Deck,
                         attrs: Tuple[str, ...] = SEED_ATTRS,
                         min_members: int = 2) -> List[Dict[str, Any]]:
    """Find clusters of elements sharing the same value for an attribute.

    Returns a list of candidates: {"attr", "value", "members"} where members are
    slot addresses. Annotators confirm/reject these; confirming calls add_link.
    """
    candidates: List[Dict[str, Any]] = []
    for attr in attrs:
        buckets: List[Tuple[Any, List[str]]] = []
        for s in deck.slides:
            for e in s.elements:
                val = get_attr(e, attr)
                if val in (None, "", 0):     # skip empties/defaults
                    continue
                addr = slot_addr(s.id, e.id, attr)
                placed = False
                for i, (bval, members) in enumerate(buckets):
                    if _val_eq(bval, val):
                        members.append(addr)
                        placed = True
                        break
                if not placed:
                    buckets.append((val, [addr]))
        for val, members in buckets:
            if len(members) >= min_members:
                candidates.append({"attr": attr, "value": val, "members": members})
    return candidates


def _val_eq(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= NUM_TOL
    return a == b


# ===========================================================================
# (1b) Creating links / groups / rules
# ===========================================================================
def add_link(deck: Deck, attr: str, members: List[str],
             value: Any = None, link_id: Optional[str] = None) -> Link:
    """Create a multi-way shared-value link over the given slot addresses.

    The link's value becomes the single source of truth; member elements are
    synced to it so a reader sees consistent values.
    """
    for addr in members:
        _, _, a = parse_slot_addr(addr)
        if a != attr:
            raise ValueError(f"member {addr!r} attr does not match link attr {attr!r}")
    if value is None and members:
        value = get_attr(_resolve(deck, members[0]), attr)
    lid = link_id or ir.next_link_id(deck)
    link = Link(id=lid, attr=attr, value=value, members=list(members))
    deck.links.append(link)
    _sync_link(deck, link)
    return link


def confirm_candidate(deck: Deck, candidate: Dict[str, Any]) -> Link:
    """Confirm a seeded candidate as a real link."""
    return add_link(deck, candidate["attr"], candidate["members"], candidate["value"])


def add_group(deck: Deck, slide_id: str, members: List[str]) -> Group:
    s = deck.slide(slide_id)
    if s is None:
        raise ValueError(f"no such slide: {slide_id}")
    g = Group(id=ir.next_group_id(s), members=list(members))
    s.groups.append(g)
    return g


def add_rule(deck: Deck, slide_id: str, rule_type: str,
             members: List[str], params: Optional[Dict[str, Any]] = None) -> Rule:
    s = deck.slide(slide_id)
    if s is None:
        raise ValueError(f"no such slide: {slide_id}")
    r = Rule(id=ir.next_rule_id(s), type=rule_type,
             members=list(members), params=params or {})
    s.rules.append(r)
    return r


def _resolve(deck: Deck, slot: str) -> Element:
    sid, eid, _ = parse_slot_addr(slot)
    el = deck.resolve_element(elem_addr(sid, eid))
    if el is None:
        raise ValueError(f"slot does not resolve: {slot}")
    return el


def _sync_link(deck: Deck, link: Link) -> None:
    """Write the link's value into each member element (reader convenience)."""
    for slot in link.members:
        _, _, attr = parse_slot_addr(slot)
        set_attr(_resolve(deck, slot), attr, link.value)


# ===========================================================================
# (2) Edits — provenance, op constructors, builder
# ===========================================================================
def manual() -> Dict[str, Any]:
    return {"type": "manual"}


def propagation(via_link: str, by_op: int) -> Dict[str, Any]:
    return {"type": "propagation", "via_link": via_link, "by_op": by_op}


def new_edit(deck: Deck, prompt: str,
             base: str = "as_authored", note: Optional[str] = None) -> Edit:
    e = Edit(id=ir.next_edit_id(deck), prompt=prompt, base=base, note=note)
    deck.edits.append(e)
    return e


# -- op constructors (each returns a dict; attach to an Edit via Edit.add) --- #
def op_set_attr(target: str, value: Any, cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "set_attr", "target": target, "value": value,
            "cause": cause or manual()}


def op_remove_element(target: str, cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "remove_element", "target": target, "cause": cause or manual()}


def op_add_element(slide: str, element: Dict[str, Any],
                   cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "add_element", "slide": slide, "element": element,
            "cause": cause or manual()}


def op_move_element(target: str, geometry: Dict[str, Any],
                    cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "move_element", "target": target, "geometry": geometry,
            "cause": cause or manual()}


def op_create_link(link: Dict[str, Any], cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "create_link", "link": link, "cause": cause or manual()}


def op_delete_link(link_id: str, cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "delete_link", "link_id": link_id, "cause": cause or manual()}


def op_attach(link_id: str, slot: str, cause: Dict[str, Any] = None) -> Dict[str, Any]:
    """retain: add a member to an existing shared link."""
    return {"op": "attach", "link_id": link_id, "slot": slot,
            "cause": cause or manual()}


def op_detach(link_id: str, slot: str, value: Any,
              cause: Dict[str, Any] = None) -> Dict[str, Any]:
    """detach: remove a member and freeze it to a literal value."""
    return {"op": "detach", "link_id": link_id, "slot": slot, "value": value,
            "cause": cause or manual()}


def op_fork_link(from_link_id: str, new_link_id: str, members: List[str],
                 seed_value: Any, cause: Dict[str, Any] = None) -> Dict[str, Any]:
    """fork: new link seeded from an existing one, over a new member set."""
    return {"op": "fork_link", "from_link_id": from_link_id,
            "new_link_id": new_link_id, "members": members,
            "seed_value": seed_value, "cause": cause or manual()}


def op_set_link_value(link_id: str, value: Any,
                      cause: Dict[str, Any] = None) -> Dict[str, Any]:
    """Change a shared value (propagates to all members)."""
    return {"op": "set_link_value", "link_id": link_id, "value": value,
            "cause": cause or manual()}


def op_create_group(slide: str, group: Dict[str, Any],
                    cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "create_group", "slide": slide, "group": group,
            "cause": cause or manual()}


def op_apply_rule(slide: str, rule: Dict[str, Any],
                  cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "apply_rule", "slide": slide, "rule": rule,
            "cause": cause or manual()}


def op_duplicate_group(source: List[str], result: List[str],
                       correspondence: List[List[str]],
                       link_decisions: List[Dict[str, Any]],
                       cause: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"op": "duplicate_group", "source": source, "result": result,
            "correspondence": correspondence, "link_decisions": link_decisions,
            "cause": cause or manual()}


# -- higher-level annotation convenience ------------------------------------ #
def delete_element(deck: Deck, edit: Edit, target: str) -> None:
    """Record a manual deletion, plus any deletions it propagates through an
    existence link (each tagged with provenance pointing at the manual op).
    """
    sid, eid = parse_elem_addr(target)
    manual_idx = edit.add(op_remove_element(elem_addr(sid, eid)))

    exist_slot = slot_addr(sid, eid, "exists")
    for link in deck.links:
        if link.attr != "exists" or exist_slot not in link.members:
            continue
        for other in link.members:
            if other == exist_slot:
                continue
            osid, oeid, _ = parse_slot_addr(other)
            edit.add(op_remove_element(
                elem_addr(osid, oeid),
                cause=propagation(via_link=link.id, by_op=manual_idx)))


# ===========================================================================
# Light validation (QA)
# ===========================================================================
def validate(deck: Deck) -> List[str]:
    issues: List[str] = []

    # links resolve, attrs agree, value type sane
    for link in deck.links:
        for slot in link.members:
            try:
                sid, eid, attr = parse_slot_addr(slot)
            except ValueError:
                issues.append(f"link {link.id}: malformed member {slot!r}")
                continue
            if deck.resolve_element(elem_addr(sid, eid)) is None:
                issues.append(f"link {link.id}: member {slot} does not resolve")
            if attr != link.attr:
                issues.append(f"link {link.id}: member {slot} attr != {link.attr}")
        if "color" in link.attr and isinstance(link.value, str) \
                and not link.value.startswith("#"):
            issues.append(f"link {link.id}: color value {link.value!r} not a hex")

    # groups / rules resolve
    for s in deck.slides:
        for g in s.groups:
            for m in g.members:
                if s.element(m) is None:
                    issues.append(f"group {s.id}.{g.id}: member {m} not on slide")
        for r in s.rules:
            for m in r.members:
                if s.element(m) is None:
                    issues.append(f"rule {s.id}.{r.id}: member {m} not on slide")

    # edits: provenance back-refs in range; duplicate correspondence is 1-1
    for e in deck.edits:
        n = len(e.ops)
        for i, op in enumerate(e.ops):
            c = op.get("cause", {})
            if c.get("type") == "propagation":
                bo = c.get("by_op")
                if not isinstance(bo, int) or not (0 <= bo < n):
                    issues.append(f"edit {e.id} op {i}: by_op out of range")
                if c.get("via_link") and deck.link(c["via_link"]) is None:
                    issues.append(f"edit {e.id} op {i}: via_link unknown")
            if op.get("op") == "duplicate_group":
                corr = op.get("correspondence", [])
                lefts = [p[0] for p in corr]
                rights = [p[1] for p in corr]
                if len(set(lefts)) != len(lefts) or len(set(rights)) != len(rights):
                    issues.append(f"edit {e.id} op {i}: correspondence not 1-1")
                if set(lefts) != set(op.get("source", [])) or \
                        set(rights) != set(op.get("result", [])):
                    issues.append(
                        f"edit {e.id} op {i}: correspondence != source/result")
    return issues


# ===========================================================================
# Saving with automatic, collision-free naming
# ===========================================================================
def save(deck: Deck, out_dir: str, name: Optional[str] = None) -> str:
    """Save the deck as JSON; if the name is taken, append _v2, _v3, ..."""
    os.makedirs(out_dir, exist_ok=True)
    base = name or deck.deck_id
    path = os.path.join(out_dir, f"{base}.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(out_dir, f"{base}_v{n}.json")
        n += 1
    deck.save(path)
    return path


def auto_deck_id(out_dir: str, base: str) -> str:
    """Pick a deck_id that won't collide with existing JSON files in out_dir."""
    if not os.path.exists(os.path.join(out_dir, f"{base}.json")):
        return base
    n = 2
    while os.path.exists(os.path.join(out_dir, f"{base}_v{n}.json")):
        n += 1
    return f"{base}_v{n}"
