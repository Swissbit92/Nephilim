#!/usr/bin/env python3
"""Freeze search results and pages, and the v0.10.1 snippet-only answers.

Everything a later arm needs goes to a local directory outside this public repo:
full page text is not ours to republish. `manifest.json` here records a sha256 per
question, so a later run can prove it used the same evidence.

    python capture.py ~/nephilim-eval/research/v0.10.1
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from src.coordinator import startup  # noqa: E402,F401
from src.coordinator.config import get_settings  # noqa: E402
from src.coordinator.di import services  # noqa: E402
from src.coordinator.services.web_fetch_service import fetch_url  # noqa: E402
from src.coordinator.tools import executor_bindings  # noqa: E402
from src.coordinator.tools.synthesis_prompts import build_synthesis_prompt  # noqa: E402
from src.coordinator.tools.tool_utils import format_search_results_for_llm  # noqa: E402

MODEL = "gemma4:26b"
SYSTEM = "You are a knowledgeable, direct assistant."
GAP_SECONDS = 3
PAGES = 4


def chat(system, user):
    body = {
        "model": MODEL, "stream": False, "think": False,
        "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": 400, "seed": 42},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        f"{os.environ.get('OLLAMA_BASE', 'http://127.0.0.1:11434')}/api/chat",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)["message"]["content"].strip()


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    questions = json.loads((HERE / "questions.json").read_text())
    os.environ["WEB_SEARCH_BACKEND"] = "routed"
    get_settings.cache_clear()
    services.init_brave_client()
    search = executor_bindings._make_search_executor("web_search")
    rows = []
    for q in questions:
        results = search({"query": q["query"]}, {}) or []
        urls = [r.url for r in results[:PAGES]]
        with ThreadPoolExecutor(PAGES) as pool:
            pages = list(pool.map(fetch_url, urls))
        context = format_search_results_for_llm(results) if results else "No results found."
        answer = chat(build_synthesis_prompt(SYSTEM, bool(results)),
                      f"{q['query']}\n\nSearch results:\n{context}")
        rows.append({**q, "results": [asdict(r) for r in results],
                     "pages": dict(zip(urls, pages)), "baseline_answer": answer})
        (out / "fixtures.json").write_text(json.dumps(rows, indent=1))
        print(q["id"], len(results), sum(not p.startswith("ERROR") for p in pages), flush=True)
        time.sleep(GAP_SECONDS)
    manifest = {r["id"]: hashlib.sha256(json.dumps(
        [r["results"], r["pages"]], sort_keys=True).encode()).hexdigest() for r in rows}
    (HERE / "manifest.json").write_text(json.dumps(
        {"captured": time.strftime("%F"), "brave_queries": len(rows), "sha256": manifest}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
