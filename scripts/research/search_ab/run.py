#!/usr/bin/env python3
"""Search routing A/B: same questions through each backend setting, same model.

Arm A is `auto` (SearXNG first), arm B is `routed` (Brave first). Both go through
the real search executor, including the relevance filter, then gemma answers with
the production synthesis prompt. Blind judging happens outside this script, so
nothing here knows which answer won.

    python run.py <out_dir>   # needs prod env, SEARXNG_BASE_URL at a test instance
"""
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from src.coordinator import research_trace, startup  # noqa: E402,F401
from src.coordinator.config import get_settings  # noqa: E402
from src.coordinator.di import services  # noqa: E402
from src.coordinator.tools import executor_bindings  # noqa: E402
from src.coordinator.tools.synthesis_prompts import build_synthesis_prompt  # noqa: E402
from src.coordinator.tools.tool_utils import format_search_results_for_llm  # noqa: E402

MODEL = "gemma4:26b"
SYSTEM = "You are a knowledgeable, direct assistant."
GAP_SECONDS = 4


def search(arm, query):
    os.environ["WEB_SEARCH_BACKEND"] = arm
    get_settings.cache_clear()
    with research_trace.turn("ab", arm) as rec:
        results = executor_bindings._make_search_executor("web_search")({"query": query}, {})
    return results or [], rec["searches"]


def answer(query, results):
    context = format_search_results_for_llm(results) if results else "No results found."
    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": 300},
        "messages": [
            {"role": "system", "content": build_synthesis_prompt(SYSTEM, bool(results))},
            {"role": "user", "content": f"{query}\n\nSearch results:\n{context}"},
        ],
    }
    req = urllib.request.Request(
        f"{os.environ.get('OLLAMA_BASE', 'http://127.0.0.1:11434')}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["message"]["content"].strip()


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    probes = json.loads((HERE / "probes.json").read_text())
    services.init_brave_client()
    rng = random.Random(20261031)
    rows = []
    for i, query in enumerate(probes):
        arms = ["auto", "routed"]
        rng.shuffle(arms)  # so neither arm always searches second
        row = {"id": f"q{i:02d}", "query": query}
        for arm in arms:
            started = time.monotonic()
            results, searches = search(arm, query)
            row[arm] = {
                "searches": searches,
                "urls": [getattr(r, "url", "") for r in results[:5]],
                "answer": answer(query, results),
                "seconds": round(time.monotonic() - started, 1),
            }
            time.sleep(GAP_SECONDS)
        rows.append(row)
        (out / "raw.json").write_text(json.dumps(rows, indent=1))
        print(row["id"], [(a, [s["backend"] for s in row[a]["searches"]]) for a in arms], flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
