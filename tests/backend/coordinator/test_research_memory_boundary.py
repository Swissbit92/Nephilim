"""A web claim must not reach fact extraction by the route research uses.

Research answers are stored as assistant turns. `no_self_ingestion.py` checks the
shape of the code; this plants a claim and looks at what the extractor is given.
"""
from unittest.mock import MagicMock

from src.coordinator import startup  # noqa: F401
from src.coordinator.fact_extractor import FactExtractor
from src.coordinator.triplet_extractor import TripletExtractor

CANARY = "CANARY-the-user-lives-in-Oslo"
TURNS = [
    {"role": "user", "content": "what is the capital of Norway?"},
    {"role": "assistant", "content": f"Sources say Oslo. {CANARY}\n\n🔍 Sources: https://x.example"},
]


def test_fact_extractor_never_sees_a_research_answer():
    rendered = FactExtractor._format_messages(MagicMock(), TURNS)
    assert CANARY not in rendered and "capital of Norway" in rendered


def test_triplet_extractor_never_sees_a_research_answer():
    rendered = TripletExtractor._format(MagicMock(), TURNS, 50)
    assert CANARY not in rendered and "capital of Norway" in rendered
