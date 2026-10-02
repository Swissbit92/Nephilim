from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class ImageGenSettings(BaseSettings):
    """Qwen-Image-2.1 generation (model selected 2026-10-02 by bake-off).

    Everything defaults OFF. Generation holds the whole machine for ~5.5
    minutes and unloads the companion LLM to do it, so it is never something
    that happens because a flag drifted.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the generation worker. OFF means the supervisor "
            "thread never starts and queued jobs sit untouched."
        ),
        alias="IMAGE_GEN_ENABLED",
    )
    dev_endpoint_enabled: bool = Field(
        default=False,
        description=(
            "Exposes POST /sessions/{id}/image/generate. Development only; 404s "
            "when OFF so the surface is not advertised."
        ),
        alias="IMAGE_GEN_DEV_ENDPOINT",
    )
    python_bin: str = Field(
        default="/Users/swissbit./image-gen/mflux-venv/bin/python",
        description=(
            "Interpreter for the generator. A SEPARATE venv on purpose: MLX and a "
            "20 GB model must not live in the backend process, and a subprocess "
            "returns its memory to the OS on exit with certainty."
        ),
        alias="IMAGE_GEN_PYTHON",
    )
    steps: int = Field(
        default=25, ge=10, le=40,
        description="Denoise steps. 25 is what the bake-off measured at 332s.",
        alias="IMAGE_GEN_STEPS",
    )
    quantize: int = Field(
        default=8, ge=4, le=8,
        description=(
            "Weight quantisation. 8, never lower: mflux's own maintainers warn "
            "6-bit and below degrades Qwen much harder than Flux, and on Apple "
            "Silicon quantisation buys memory rather than speed, so a smaller "
            "quant costs quality for nothing."
        ),
        alias="IMAGE_GEN_QUANTIZE",
    )
    width: int = Field(default=1024, ge=256, le=2048, alias="IMAGE_GEN_WIDTH")
    height: int = Field(default=1024, ge=256, le=2048, alias="IMAGE_GEN_HEIGHT")
    memory_limit_gb: int = Field(
        default=34, ge=8, le=44,
        description=(
            "Hard MLX allocation ceiling, BELOW physical RAM. Measured on this "
            "machine: max_recommended_working_set_size is 37.44 GiB, but MLX's "
            "own default limit is 1.5x that (~56 GiB) — above the 48 GiB "
            "installed. Left at the default, an overrun does not raise, it "
            "swap-storms, and the symptom is a 20-minute unresponsive Mac rather "
            "than an error. This turns that into a clean exception."
        ),
        alias="IMAGE_GEN_MEMORY_LIMIT_GB",
    )
    stall_seconds: int = Field(
        default=180, ge=30, le=1800,
        description=(
            "No growth in the progress log for this long means the job is "
            "wedged. mflux's tqdm writes a line per step to stderr, so byte "
            "growth is the liveness signal (never readline — tqdm uses \\r)."
        ),
        alias="IMAGE_GEN_STALL_SECONDS",
    )
    max_seconds: int = Field(
        default=900, ge=60, le=7200,
        description="Hard wall clock. ~3x the measured 332s.",
        alias="IMAGE_GEN_MAX_SECONDS",
    )
    term_grace_seconds: int = Field(
        default=20, ge=1, le=120,
        description=(
            "SIGTERM-to-SIGKILL grace. 20s because MLX must tear down tens of GB "
            "of Metal buffers and Python runs its atexit handlers."
        ),
        alias="IMAGE_GEN_TERM_GRACE_SECONDS",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }
