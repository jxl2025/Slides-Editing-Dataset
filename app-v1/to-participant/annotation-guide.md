# Slide Annotation Guide (for participants)

You will be given the **content** of a slide deck — the objects on each slide and
their geometry — and asked to find **attribute-link relationships**: groups of
objects that were *meant* to share something (the same color, the same width, the
same text, the same position, …). You record what you find in a JSON file.

This guide has two parts: how to write your file (Part A) and how your file is
compared to the reference (Part B, so you know what actually matters).

---

## Part A — How to annotate

### What you receive
One file per deck, `<deck>.content.json`, listing slides and objects:

```json
{
  "deck_id": "lecture07",
  "slides": [
    { "id": "s1",
      "elements": [
        { "addr": "s1.e1", "type": "RECTANGLE",
          "x": 0.21, "y": 0.19, "w": 0.24, "h": 0.12,
          "fill": "#4BACC6", "text": "",
          "attrs": { "fill": "#4BACC6", "line_color": null, "w": 0.24, ... } }
      ] }
  ]
}
```

Every object has an **address** of the form `s<slide>.e<element>` (e.g. `s1.e1`).
You refer to objects only by these addresses. `attrs` lists each object's actual
values, which you can use to decide what goes together.

You also receive `<deck>.candidates.json` — a list of **suggested** groups the
tool found automatically (objects that happen to share an exact value on some
attribute). These are only a starting point, not answers: some are real
relationships, many are coincidences, and plenty of real relationships are *not*
in the list. Use them however helps — as a checklist to confirm/reject, or ignore
them and work from the slides. You are not scored on the candidates.

### What you produce
One file per deck, `<deck>.relationships.<yourname>.json`:

```json
{
  "deck_id": "lecture07",
  "annotator": "yourname",
  "relationships": [
    { "id": "R1", "attr": "fill",  "members": ["s1.e1", "s3.e4", "s5.e2"] },
    { "id": "R2", "attr": "text",  "members": ["s1.e9", "s2.e9", "s3.e9"] },
    { "id": "R3", "attr": "w",     "members": ["s4.e1", "s4.e2", "s4.e3"] }
  ]
}
```

A **relationship** is one group of objects you judge to intentionally share **one
attribute**. That's the whole unit. Rules:

1. **One attribute per relationship** (the `attr` field). If a group of objects
   shares several things (say the same footer text *and* the same position),
   write one relationship per shared attribute over that same group.
2. **`members` are addresses** copied from the content file, at least two of them.
3. **Cross-slide is normal** — a footer repeated on 20 slides is one relationship
   with 20 members.
4. **Group by intent, not by exact bytes.** If three boxes are "the same width"
   but differ by a rounding hair, still group them. If a footer is one shade off
   on a dark slide, still group it. You are recording what was *meant* to match.
5. **`id`** can be anything unique in your file (`R1`, `R2`, …). It is only a
   label.

You do **not** need to write a `value` — it is computed from the members. You do
**not** need to write styles — those are derived from your relationships
automatically. Just find the groups.

### The attributes you can use
`fill`, `line_color`, `font_color` (text color), `line_width`, `line_dash`,
`opacity`, `x`, `y`, `w`, `h`, `rot` (rotation), `text` (text content),
`img_content` (image identity), `exists`.

Notes:
- `img_content`: group images you believe are the **same picture**, including
  scaled or re-exported copies (you decide by eye — the addresses are enough).
- `x`, `y` capture shared position (e.g. everything left-aligned to the same edge);
  `w`, `h` shared size; `rot` shared rotation.
- Use `text` for identical/echoed wording, `font_color` for shared text color.

### A worked example
Slides 1–3 each have a gray footer at the bottom-left with identical wording, and
slide 4 has three equal-width bars in a row. You might write:

```json
{ "id": "R1", "attr": "text",       "members": ["s1.e9","s2.e9","s3.e9"] }
{ "id": "R2", "attr": "font_color", "members": ["s1.e9","s2.e9","s3.e9"] }
{ "id": "R3", "attr": "y",          "members": ["s1.e9","s2.e9","s3.e9"] }
{ "id": "R4", "attr": "w",          "members": ["s4.e1","s4.e2","s4.e3"] }
```

Same three footer objects, three separate relationships (one per shared
attribute); the bars are a fourth relationship on width.

### Also record styles
A **style** is a recurring "look": a group of objects that share *several*
attributes at once — e.g. every section header uses the same font color, the same
size, and the same left edge. Styles are the higher-level pattern we ultimately
care about, so please record the ones you notice. They're quick, because you build
them out of relationships you already wrote.

Put them in `<deck>.styles.<yourname>.json`:

```json
{
  "deck_id": "lecture07",
  "annotator": "yourname",
  "styles": [
    { "id": "S1", "intent": ["R1", "R2", "R3"] }
  ]
}
```

- `intent` lists the ids of **two or more of your own relationships** that a
  common set of objects all belong to. Read it as "these objects go together and
  share all of these things at once."
- You do **not** need to list the objects — that shared cohort is computed from
  the relationships. (If you'd like to be explicit you may add an `extent` array
  of addresses, but it's optional.)

In the footer example above, `R1`/`R2`/`R3` (text, font color, and position of the
same three footers) form one style: `{ "id": "S1", "intent": ["R1","R2","R3"] }`.

## Part B — How your annotation is scored

The comparison is designed to be **fair to content, not formatting**. The
following are **ignored** entirely:

- relationship `id`s and the order of relationships,
- the order of `members`,
- the `value` field,
- your file name / annotator name.

What is measured is the only thing that matters: **for each attribute, which
objects you put together.**

### The core metric: per-attribute co-membership F1
For a given attribute (say `fill`), think of every relationship as a group. Two
objects are "linked" if they sit in the *same* group. We compare the set of
linked object-pairs you produced against the reference's:

- **Precision** = of the pairs you linked, the fraction the reference also linked.
- **Recall** = of the pairs the reference linked, the fraction you also linked.
- **F1** = the harmonic mean of the two (one number, 0–1).

This gives **partial credit** (getting 18 of 20 footers is scored as such, not
all-or-nothing) and doesn't care how you split or labeled things — only who ends
up grouped with whom. It's computed per attribute, then reported two ways:

- **Macro-F1**: the average of the per-attribute F1 scores (every attribute
  counts equally).
- **Micro-F1**: all attributes' pairs pooled into one score (bigger groups count
  more).

### Styles (secondary)
A style is treated as what it is — a bundle of attribute-links over a cohort of
objects, i.e. a rectangle of (attribute, object) cells. Your styles are compared
to the reference's by **best-match overlap of those cells** (Jaccard), in both
directions. This means a style is only credited when it agrees on **both** the
objects *and* the attributes it bundles: right objects but wrong attributes, or
right attributes but wrong objects, both score low. If you didn't author styles,
they're derived from your relationships (the maximal groups of objects sharing ≥2
attributes) so the comparison still runs — but authoring them yourself is better,
since it captures which bundles *you* judged to be real.

### What this means for you
Focus your effort on **finding the right groups**. You are not penalized for
ordering, naming, how you split the work across the file, or exact values — only
for missing a real group, inventing a group that isn't there, or putting the
wrong objects together.