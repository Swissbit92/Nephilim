"""Graph store (Neo4j) configuration — ADR-014.

The graph holds a PROJECTION of a persona's standing rules. It is not a system of
record: the card in git is the origin and the projection is rebuildable, which is
what satisfies ADR-001's one transferable objection (another always-on service on
a box running live trading) — a service whose loss costs capability rather than
data is a different risk from one whose loss costs data.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class GraphSettings(BaseSettings):
    """Neo4j connection and the flag that gates every read of it."""

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the graph-backed rule read (ADR-014). OFF means "
            "byte-identical behaviour to before the graph existed: no driver is "
            "created, no connection is attempted, and prompt_builder renders "
            "exactly the sections it rendered on 2026-09-26. It is not a "
            "degraded mode, it is absence. "
            "WHY IT EXISTS: the measured state on dev is that ZERO of gwen's 27 "
            "card rules reach the model — PERSONA_CONSTRAINTS_IN_PROMPT defaults "
            "false and, even on, prompt_builder.py's 150-token front-pop trim "
            "drops do, dont AND the bond for her. The graph read replaces that "
            "trim for hard walls, which is a behaviour change to the most "
            "sensitive persona in the roster and therefore starts off. "
            "GATE BEFORE FLIPPING: gwen_dev renders all 6 hard walls, the same "
            "read returns a byte-identical rule_id list across unrelated inputs, "
            "and PROFILE on the read shows no Sort operator. "
            "Set GRAPH_ENABLED=true to enable."
        ),
        alias="GRAPH_ENABLED",
    )

    base_url: str = Field(
        default="",
        description=(
            "Bolt URL for Neo4j, e.g. bolt://127.0.0.1:7688. REQUIRED when "
            "GRAPH_ENABLED is true; empty means disabled, matching "
            "SEARXNG_BASE_URL's convention. "
            "DELIBERATELY AN EMPTY-STRING SENTINEL RATHER THAN A PLAUSIBLE "
            "DEFAULT, and this repo has already paid for the lesson: a worktree "
            "with no .env left PERSONA_MODEL unset, it fell back to a real model "
            "that happened to be pulled, and a test exercised a 9B smoke model at "
            "4096 context for months while reporting green. A default of "
            "bolt://localhost:7687 would silently connect to whatever graph "
            "happens to be on that port — including production, from a test run. "
            "Empty is rejected loudly by require_graph_configured(). "
            "It is also not a pydantic-required field, because "
            "config/__init__.py constructs the settings singleton at IMPORT time, "
            "so a required field would turn a missing .env into a "
            "ValidationError across the whole test suite at collection rather "
            "than one readable startup error. "
            "Note the default host port is 7688, not the conventional 7687 — see "
            "docker-compose.graph.yml for why."
        ),
        alias="NEO4J_BASE_URL",
    )

    # NAMED `username`, NOT `user`, AND THAT IS NOT COSMETIC.
    # Every settings class here sets populate_by_name=True, which makes pydantic
    # accept the FIELD NAME as well as the alias, and env lookup is
    # case-insensitive. A field called `user` therefore matches the ubiquitous
    # $USER environment variable — measured 2026-09-26, it silently resolved to
    # "swissbit." and the driver authenticated as the macOS account instead of
    # neo4j, producing an AuthError that looked like a wrong password. The alias
    # is unchanged, so NEO4J_USER still works; only the shadowing name is gone.
    # test_settings_field_names_do_not_shadow_env_vars guards the class.
    username: str = Field(
        default="neo4j",
        description="Neo4j username. Community edition has exactly one, and every "
                    "user is admin — there is no read-only credential to fall back "
                    "on, which is why the fence is the loopback port binding.",
        alias="NEO4J_USER",
    )

    password: str = Field(
        default="",
        description=(
            "Neo4j password. Separate from the URL on purpose: credentials must "
            "never be embedded in a connection string, because a URL is logged, "
            "echoed in errors and passed in argv, and tests/backend/coordinator/"
            "test_no_secret_in_argv.py exists to enforce exactly that. Empty is "
            "rejected by require_graph_configured() when GRAPH_ENABLED is true."
        ),
        alias="NEO4J_PASSWORD",
    )

    database: str = Field(
        default="neo4j",
        description="Database name, always passed explicitly to execute_query. "
                    "Community supports exactly one user database, so this is "
                    "effectively fixed — but passing it saves a home-database "
                    "resolution round-trip on every call.",
        alias="NEO4J_DATABASE",
    )

    max_pool_size: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Driver connection-pool ceiling. The driver default is 100, "
                    "which is sized for a cluster serving many callers; this is one "
                    "operator on a box that pins ~17GB for Ollama.",
        alias="NEO4J_MAX_POOL_SIZE",
    )

    rule_read_limit: int = Field(
        default=8,
        ge=1,
        le=64,
        description=(
            "How many standing rules the deterministic read returns, ordered by "
            "priority DESC then rule_id DESC. A limit is required rather than "
            "optional: the read is injected into every turn, so an unbounded one "
            "would let a persona's rule count set the prompt budget. 8 is chosen "
            "against gwen's 6 hard walls plus headroom, NOT measured — if a "
            "persona ever has more hard walls than this, the limit silently drops "
            "the lowest-priority ones, which is why the integrity query asserts "
            "hard_wall count <= rule_read_limit."
        ),
        alias="GRAPH_RULE_READ_LIMIT",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
