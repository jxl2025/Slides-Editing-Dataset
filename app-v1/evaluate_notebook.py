# %% [markdown]
# # Scoring participants against ground truth
# Drop this next to `evaluate.py` and your work directory. It compares each
# participant's relationships (and styles) file to your GT files and prints
# per-attribute and overall F1, plus a summary table across participants.

# %%
import os
import evaluate as ev

WORK = "annotation_work"      # the --out directory the server wrote to
DECK = "lecture07"            # deck_id (the prefix on the json filenames)
GT   = "me"                   # your annotator name (the ground truth)
PARTICIPANTS = ["alice", "bob"]

def rel_file(who):    return os.path.join(WORK, f"{DECK}.relationships.{who}.json")
def style_file(who):  return os.path.join(WORK, f"{DECK}.styles.{who}.json")

gt_rels   = ev.load(rel_file(GT))
gt_styles = ev.load_styles(style_file(GT)) if os.path.exists(style_file(GT)) else ev.derive_styles(gt_rels)

# %% [markdown]
# ## Per-participant detailed report

# %%
reports = {}
for who in PARTICIPANTS:
    pred_rels = ev.load(rel_file(who))
    sf = style_file(who)
    pred_styles = ev.load_styles(sf) if os.path.exists(sf) else ev.derive_styles(pred_rels)

    rel_report = ev.score_relationships(gt_rels, pred_rels)
    style_report = ev.score_styles(gt_styles, pred_styles, gt_rels, pred_rels)
    reports[who] = (rel_report, style_report)

    ev.print_report(rel_report, style_report, label=who)
    print()

# %% [markdown]
# ## Summary table across participants

# %%
print(f"{'participant':<14} {'macroF1':>8} {'microF1':>8} {'styleF1':>8}")
for who, (rel_report, style_report) in reports.items():
    print(f"{who:<14} {rel_report['macro']['f1']:8.2f} "
          f"{rel_report['micro']['f1']:8.2f} {style_report['f1']:8.2f}")

# %% [markdown]
# ## Drilling into one attribute
# The per-attribute dict carries the raw counts (tp / predicted-pairs / gt-pairs),
# so you can see exactly where a participant diverged.

# %%
who = PARTICIPANTS[0]
for attr, v in sorted(reports[who][0]["per_attr"].items()):
    print(f"{attr:<12} F1={v['f1']:.2f}  tp={v['tp']} pred={v['pred']} gt={v['gt']}")
