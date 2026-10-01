from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class MediaSettings(BaseSettings):
    """Generated-media storage (Telegram image transport, phase 1).

    Both flags default OFF. Nothing writes to disk and no endpoint exists until
    they are flipped deliberately, so merging this is inert.

    ``root_dir`` is resolved to an ABSOLUTE path at write time, not here — the
    coordinator and the Telegram gateway are separate processes with different
    working directories, and a relative root silently resolves differently in
    each. The absolute path is what travels on the response.

    Field names avoid ``path``/``root`` deliberately: ``populate_by_name`` makes
    the field name itself an env key, so a field called ``path`` would read
    ``$PATH``. ``test_settings_env_shadowing`` enforces this.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for generated-media storage. OFF means store_png() refuses "
            "and no media ever reaches a response. Flip only after the gateway's "
            "allowlist root is configured to match."
        ),
        alias="MEDIA_ENABLED",
    )
    root_dir: str = Field(
        default="data/media",
        description=(
            "Base directory for per-session media. Relative paths resolve against the "
            "process CWD, which differs between the coordinator and the gateway — set "
            "an absolute path in any deployment where both must agree."
        ),
        alias="MEDIA_ROOT",
    )
    max_bytes: int = Field(
        default=20_000_000,
        ge=1,
        le=50_000_000,
        description=(
            "Largest single image accepted. Ceiling is Telegram's sendDocument limit "
            "(50 MB); the default leaves headroom for a quality-pipeline PNG."
        ),
        alias="MEDIA_MAX_BYTES",
    )
    fixture_enabled: bool = Field(
        default=False,
        description=(
            "Exposes POST /sessions/{id}/media/fixture, which stores a generated probe "
            "PNG and returns a chat-shaped response. Development only — it proves the "
            "transport without a generation backend. 404s when OFF rather than 403, so "
            "the surface is not advertised."
        ),
        alias="MEDIA_FIXTURE_ENABLED",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
