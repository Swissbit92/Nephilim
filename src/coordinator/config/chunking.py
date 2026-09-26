"""Where a reply gets broken into chat bubbles — prompt or code (ADR-015).

Until now the model was ASKED to emit ``<msg>`` chunks and a 140-line
four-strategy fallback caught the turns where it didn't. Both halves were
defective for a lowercase-texting persona: the prompt instruction is a format
constraint competing with every other instruction in the block, and the
fallback's sentence rule is ``(?<=[.!?])\\s+(?=[A-Z])`` — an uppercase-only
lookahead that CANNOT fire on "hey. you up?", plus a 500-char floor that most
replies never reach.

Splitting in code makes the bubble contract deterministic and frees the prompt
tokens. The flag exists because the prompt half is a global change across all
nine personas, so the revert has to be one line.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class ChunkingSettings(BaseSettings):
    """The bubble-boundary decision and its two calibration numbers."""

    in_code: bool = Field(
        default=True,
        description=(
            "TRUE: bubble boundaries are a pure function of the reply text, and "
            "the prompt no longer asks for <msg> tags. FALSE: the pre-ADR-015 "
            "behaviour — prompt asks for tags, the legacy four-strategy fallback "
            "catches the rest. Model-emitted tags are HONOURED either way, so a "
            "persona that still produces them is unaffected by this flag. "
            "Set CHUNK_IN_CODE=false to revert."
        ),
        alias="CHUNK_IN_CODE",
    )

    max_bubbles: int = Field(
        default=3,
        ge=1,
        le=6,
        description=(
            "Ceiling on bubbles per reply. Default 3 from the live distribution "
            "in chats.db (903 assistant rows): 2 bubbles x103, 3 x142, 4 x48 — "
            "mean 2.81, so 3 is the mode and 4 is the tail."
        ),
        alias="CHUNK_MAX_BUBBLES",
    )

    words_per_bubble: int = Field(
        default=14,
        ge=6,
        le=40,
        description=(
            "Divisor that sets bubble count: round(words / this), clamped to "
            "[1, max_bubbles]. FITTED to this deployment, not borrowed. Grid "
            "search over the 293 real multi-message replies in chats.db: 14 "
            "maximises agreement with the model's own bubble count at 57% "
            "(the reference implementation's fixed <25/<70 word thresholds "
            "scored 31%) and lands at 21.7 emergent words per bubble against "
            "the model's own 21.0. Higher values under-split: at 21 the mean "
            "falls to 2.18 against the model's 2.81 and 63 replies collapse to "
            "a single bubble."
        ),
        alias="CHUNK_WORDS_PER_BUBBLE",
    )

    min_words: int = Field(
        default=4,
        ge=1,
        le=20,
        description=(
            "A bubble under this many words is merged into its neighbour. An "
            "orphan stub is the most obviously-mechanical artifact a reader can "
            "see, and it is the defect the reference implementation (openhuman) "
            "shipped and then had to fix."
        ),
        alias="CHUNK_MIN_WORDS",
    )


chunking_settings = ChunkingSettings()
