"""A stable operator identity, and why memory could not work without one.

🔴 THE DEFECT THIS FIXES, measured on the live database 2026-10-18: **397 user
profiles, every one with exactly ONE session.** Identity was recovered by looking
up an LLM-EXTRACTED name and minting `user_{uuid4}` when that failed — so each
time the roleplay changed what she called him he became a new person with an
empty memory. 389 profiles carried no extracted name at all; `"Ben"` held 475
messages because he asked her to use it once; his real name held two profiles.
**130 of the 397 hold real facts** she could never reach again.

No retrieval policy and no rendering could have fixed that. There was nothing to
retrieve FROM across sessions.
"""

from __future__ import annotations

import pytest

from src.coordinator.config.memory import MemorySettings


def test_the_setting_defaults_to_empty_so_the_fix_is_opt_in():
    """Legacy behaviour is kept by default and the change is instantly reversible
    by clearing one env var — which matters because this alters which profile
    every live turn reads and writes."""
    assert MemorySettings(_env_file=None).single_operator_user_id == ""


def test_a_stable_id_short_circuits_both_legacy_paths():
    """Asserted on the SOURCE because the surrounding function needs a full app:
    the stable id must be applied BEFORE the name lookup and before the uuid
    mint, or a session where the extractor happens to produce a name would still
    fork the identity."""
    import inspect

    from src.coordinator.services import chat_session_service as svc

    src = inspect.getsource(svc)
    stable = src.index("_single = get_settings().memory.single_operator_user_id")
    by_name = src.index('deps.user_profile_repo.get_user_by_name(facts["user_name"])')
    minted = src.index('state.user_id = f"user_{uuid.uuid4().hex[:8]}"')
    assert stable < by_name < minted, (
        "the stable id is not resolved first — a session whose extractor returns a "
        "name would still fork the identity"
    )


def test_the_stable_path_links_the_session_to_the_user():
    """The bug this test exists for: `link_session_to_user` originally ran ONLY on
    the mint-a-new-uuid branch. With a stable id every new session resolves the
    right profile and would never appear in `user_sessions` — the table that maps
    a session back to its user, and the one used to find which profile owns a
    conversation."""
    import inspect

    from src.coordinator.services import chat_session_service as svc

    src = inspect.getsource(svc)
    assert src.count("link_session_to_user") >= 2, (
        "only one link call — the stable-identity branch does not link its session"
    )


@pytest.mark.parametrize("value,expected", [
    ("user_be5d9dbe", "user_be5d9dbe"),
    ("", ""),
    ("   ", "   "),
])
def test_the_value_is_used_verbatim(value, expected):
    """No normalisation, no prefixing. The id must match what is already in the
    database or the merge migration points at nothing."""
    assert MemorySettings(_env_file=None,
                          SINGLE_OPERATOR_USER_ID=value).single_operator_user_id == expected
