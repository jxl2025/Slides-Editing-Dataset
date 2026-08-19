# Slide annotation task coming up! see below and attached:

## What you're doing
Each slide deck has been broken down into its objects (shapes, text, images) with
their positions and styling. Your job is to find the **groups of objects that were
meant to share something** — the same color, size, position, wording, image, etc.
— and write them down.

## What you'll find in this folder
- `<deck>.content.json` — the objects on every slide, each with an address like
  `s1.e4` and its actual attribute values. **Read-only: do not edit or renumber
  it.** Everyone (including us) annotates this exact file, which is how answers get
  compared.
- `annotation-guide.md` — the full instructions, the list of attributes you can
  use, and how your work is scored.
- `<deck>.candidates.json` — automatic suggestions of possible groups. A starting
  point only: confirm the real ones, ignore the rest, and add the many we missed.
- `<deck>.pdf` - the PDF version of the slide for visual reference if helpful.
- `good-example.md` - a partial deck, an annotation, and why it's good

## What to hand back
- `<deck>.relationships.<yourname>.json` — your groups (each: an attribute + the
  list of member addresses). This is the main deliverable.
- `<deck>.styles.<yourname>.json` — optional but valuable: bundles of your
  relationships that describe a recurring "look."

The guide shows the exact JSON shape for both. A few things worth knowing up front:
you won't be penalized for ordering, naming, or exact values — only for which
objects you group together — so focus on finding the right groups. Group by
intent, not by hair-splitting exact values. When in doubt, the guide has examples.
