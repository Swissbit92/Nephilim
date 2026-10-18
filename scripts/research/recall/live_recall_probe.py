import json, urllib.request

B = "http://127.0.0.1:8000"
def post(path, body, timeout=240):
    r = urllib.request.Request(B+path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())

# ⚠️ Refusal is tested BEFORE the keys. The first version of this scorer keyed
# "do i eat meat?" on ["vegetarian","no","don't","not"] and scored a REFUSAL as
# a HIT, because "not" appears in "make something up". A scorer that can read a
# refusal as a success is the instrument failure this repo has recorded twice.
REFUSALS = ("don't actually have grounded", "do not know", "don't know",
            "not sure", "haven't told", "wasn't told", "didn't tell",
            "no idea", "can't go looking", "want me to search")

PROBES = [
    ("what do i usually eat for breakfast?",        ("eggs benedict", "apple juice")),
    ("do i eat meat?",                              ("vegetarian", "hate meat", "no meat")),
    ("what games do i play?",                       ("witcher", "cyberpunk", "gta")),
    ("how much bitcoin did i buy and when?",        ("2.5", "2021")),
    ("what am i thinking about doing with my job?", ("quit", "own business", "business")),
    ("what hobbies do i have?",                     ("gym", "gta", "read", "beach")),
    ("what temperature scale do i prefer?",         ("celsius",)),
    ("whats my money situation?",                   ("runway", "eight", "8-month", "8 month")),
]

rows = []
sid = post("/sessions", {"persona_key": "gwen", "title": "recall probes v2"})["id"]
print(f"session {sid}\n")
for i, (q, keys) in enumerate(PROBES, 1):
    try:
        d = post(f"/sessions/{sid}/chat", {"persona": "gwen", "message": q})
    except Exception as e:
        print(f"{i}. {q}\n   ERROR {e}\n"); rows.append("ERROR"); continue
    a = d.get("answer") or d.get("response") or ""
    text = (" ".join(a) if isinstance(a, list) else str(a))
    low = text.lower()
    src = (d.get("metadata") or {}).get("source_type")
    if any(p in low for p in REFUSALS):
        verdict = "REFUSED"
    elif any(k in low for k in keys):
        verdict = "RECALLED"
    else:
        verdict = "OTHER"
    rows.append(verdict)
    print(f"{i}. {q}")
    print(f"   -> {text[:190]}")
    print(f"   {verdict}   (source={src})\n")

from collections import Counter
c = Counter(rows)
print("=" * 60)
print(f"RECALLED {c['RECALLED']}/{len(PROBES)}   REFUSED {c['REFUSED']}   OTHER {c['OTHER']}")
