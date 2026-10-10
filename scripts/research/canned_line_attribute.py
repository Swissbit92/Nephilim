"""Canned-line replay has a SIGNATURE that genuine model repetition does not: the text is
BYTE-IDENTICAL across turns, and it lands adjacent to an image job. The model paraphrases;
a cache does not."""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

root = Path("/Users/swissbit./nephilim-wt/reptn")
sys.path.insert(0, str(root / "tests" / "evaluation" / "persona_eval"))
import repetition_metrics as rm  # noqa: E402

excl = rm.card_ngrams(json.loads((root / "personas" / "gwen.json").read_text()))
con = sqlite3.connect("file:/Users/swissbit./nephilim-ecosystem/nephilim/data/chats.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT m.session_id, m.content, m.timestamp FROM messages m JOIN chat_sessions s "
    "ON s.id=m.session_id WHERE m.role='assistant' AND s.persona_key='gwen' "
    "ORDER BY m.session_id, m.timestamp")]
jobs = [dict(r) for r in con.execute("SELECT session_id, created_at, finished_at FROM image_jobs")]
con.close()

sess = {}
for r in rows:
    sess.setdefault(r["session_id"], []).append(r)

flagged = []
for sid, turns in sess.items():
    for i, t in enumerate(turns):
        if i == 0:
            continue
        prior = [x["content"] or "" for x in turns[:i]]
        span = rm.longest_shared_span(t["content"] or "", prior, excl)
        if span >= 8:
            exact = (t["content"] or "").strip() in {p.strip() for p in prior}
            flagged.append({"span": span, "sid": sid, "text": t["content"] or "", "exact": exact})

img_sessions = {j["session_id"] for j in jobs}
print(f"image jobs: {len(jobs)} across {len(img_sessions)} sessions\n")
exact_n = sum(1 for f in flagged if f["exact"])
in_img = sum(1 for f in flagged if f["sid"] in img_sessions)
print(f"flagged total                      : {len(flagged)}")
print(f"  byte-identical to an earlier turn : {exact_n}   <- cache replay signature")
print(f"  paraphrased (model repetition)    : {len(flagged) - exact_n}")
print(f"  in a session that ran an image job: {in_img}\n")
print("the paraphrased ones (the only candidates for a MODEL fix):")
for f in flagged:
    if not f["exact"]:
        print(f"  span={f['span']:>3}  {f['text'][:88]!r}")
print("\nexact-duplicate texts and their total occurrence count across the corpus:")
alltext = Counter((r["content"] or "").strip() for r in rows)
for f in flagged:
    if f["exact"]:
        print(f"  x{alltext[f['text'].strip()]}  {f['text'][:72]!r}")
