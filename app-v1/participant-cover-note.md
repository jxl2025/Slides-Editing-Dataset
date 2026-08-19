Slide annotation task coming up! see below and attached:

First off, here's what I need you to do: Each slide deck has been broken down into its objects (shapes, text, images) with their positions and styling. Your job is to find the **groups of objects that were meant to share an attribute** — the same color, size, position, wording, image, etc.— and write them down.

Here's a list of stuff I'm providing you to do this task:
- `<deck>.content.json` — the objects on every slide, each with an address like `s1.e4` and its actual attribute values. **Read-only: do not edit or renumber it.** Everyone (including us) annotates this exact file, which is how answers get
  compared.
- `annotation-guide.md` — the full instructions, the list of attributes you can use, and how your work is scored.
- `<deck>.candidates.json` — automatic suggestions of possible groups. A starting point only: confirm the real ones, ignore the rest, and add the many we missed.
- `<deck>.pdf` - the PDF version of the slide for visual reference if helpful.
- `good-example.md` - a partial deck, an annotation, and why it's good

Here's how you should complete this annotation task. You should store the results in these two files and give them to me:
- `<deck>.relationships.<yourname>.json` — your groups (each: an attribute + the
  list of member addresses). This is the main deliverable.
- `<deck>.styles.<yourname>.json` — optional but valuable: bundles of your
  relationships that describe a recurring "look."
