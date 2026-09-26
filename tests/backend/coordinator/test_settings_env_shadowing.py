"""A settings FIELD NAME must not collide with a common environment variable.

WHY THIS EXISTS, and it is a measured bug rather than a hypothetical. Every
settings class in this repo sets `populate_by_name: True`, so pydantic-settings
accepts the field name in addition to the declared alias, and its env lookup is
case-insensitive. A field named `user` therefore reads `$USER`.

On 2026-09-26 that shadowed GraphSettings.user with the macOS account name, and
the Neo4j driver authenticated as "swissbit." instead of "neo4j". The symptom was
an AuthError that reads exactly like a wrong password — the alias NEO4J_USER was
set correctly the whole time, and the value that won came from an environment
variable nobody in this repo has ever written.

The class is nastier than the instance: it only fires where the operator's shell
happens to export that name, so it is invisible in CI, invisible in Docker, and
reproducible only on the machine that has the variable.
"""

from __future__ import annotations

import inspect
import os

import pytest
from pydantic_settings import BaseSettings

from src.coordinator import config as cfg

# NEAR-UNIVERSAL POSIX names only. Deliberately short.
#
# A first draft of this list also carried username, password, token, key, port,
# debug and version — and it failed on EmailSettings.username and .password,
# which are perfectly good field names for an SMTP config. Neither $USERNAME nor
# $PASSWORD is exported in a normal POSIX shell ($USERNAME is a Windows
# convention), so those were false positives, and a guard that cries wolf on
# reasonable names is one people delete.
#
# The rule for adding a name here: it must be exported by essentially every
# POSIX login shell, so that a field with that name is broken for EVERYONE. Local
# and CI-specific variables are the other test's job.
UNIVERSAL_POSIX_ENV_NAMES = {
    "user", "home", "path", "shell", "pwd", "lang", "term",
    "logname", "tmpdir", "editor", "display", "hostname",
}


def _settings_classes():
    for name, obj in vars(cfg).items():
        if inspect.isclass(obj) and issubclass(obj, BaseSettings) and obj is not BaseSettings:
            yield name, obj


def test_there_are_settings_classes_to_check():
    """Guard the guard: if the import shape changes this test must fail loudly
    rather than iterate an empty list and report green."""
    assert list(_settings_classes()), "no BaseSettings classes found via coordinator.config"


@pytest.mark.parametrize("cls_name,cls", list(_settings_classes()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_no_field_name_shadows_a_universal_posix_env_var(cls_name, cls):
    """Catches the case that is broken on every machine, for everyone."""
    if not cls.model_config.get("populate_by_name"):
        pytest.skip(f"{cls_name} does not populate_by_name, so field names are not env keys")
    shadowed = sorted(f for f in cls.model_fields if f.lower() in UNIVERSAL_POSIX_ENV_NAMES)
    assert not shadowed, (
        f"{cls_name} has field(s) {shadowed} whose NAME matches a variable exported by "
        f"essentially every POSIX shell. With populate_by_name=True that name is itself "
        f"an env key, so the field silently takes the shell's value and the declared "
        f"alias never gets a chance. Rename the field — the alias can stay."
    )


@pytest.mark.parametrize("cls_name,cls", list(_settings_classes()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_no_field_name_shadows_a_variable_in_THIS_environment(cls_name, cls):
    """Catches whatever the curated list forgets — empirically, on this machine.

    This is the half that found the real bug. GraphSettings.user resolved to
    "swissbit." from $USER and the Neo4j driver authenticated as the macOS account
    instead of neo4j, presenting as an AuthError that reads exactly like a wrong
    password. No curated list would have been trusted to contain every name; the
    environment itself is the authority.

    It is also the half that can pass on one machine and fail on another, which is
    the honest shape of the hazard rather than a flaw in the test: a field named
    after a variable your shell happens to export IS broken for you and fine for
    someone else. The failure message says which variable, so the diagnosis is
    immediate rather than a three-hour hunt through driver internals.
    """
    if not cls.model_config.get("populate_by_name"):
        pytest.skip(f"{cls_name} does not populate_by_name, so field names are not env keys")
    live = {k.lower() for k in os.environ}
    shadowed = sorted(f for f in cls.model_fields if f.lower() in live)
    assert not shadowed, (
        f"{cls_name} field(s) {shadowed} are shadowed by a variable exported in THIS "
        f"environment: "
        + ", ".join(f"${f.upper()}={os.environ.get(f.upper(), '<case-differs>')!r}"
                    for f in shadowed)
        + ". The field takes that value, not the alias's. Rename the field."
    )
