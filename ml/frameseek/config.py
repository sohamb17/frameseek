"""Settings shared by the worker and the retrieval/conversation service.

Environment variables configure infrastructure (database, paths, LLM provider);
YAML files under configs/ configure the ML behaviour. The index config hash is the
SHA-256 of the raw bytes of configs/index.yaml, which the Go API computes the same
way when it builds a job's idempotency key.
"""
from __future__ import annotations

import functools
import hashlib
import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Any

import yaml

CODE_VERSION = os.environ.get("FRAMESEEK_CODE_VERSION", "dev")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env(
        "FRAMESEEK_DATABASE_URL", "postgresql://frameseek:frameseek@localhost:5432/frameseek"))
    data_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path(_env("FRAMESEEK_DATA_DIR", "./data")))
    config_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path(_env("FRAMESEEK_CONFIG_DIR", "../configs")))
    models_dir: pathlib.Path = field(default_factory=lambda: pathlib.Path(_env("FRAMESEEK_MODELS_DIR", "./data/models")))
    api_url: str = field(default_factory=lambda: _env("FRAMESEEK_API_URL", "http://localhost:8080"))
    internal_token: str = field(default_factory=lambda: _env("FRAMESEEK_INTERNAL_TOKEN", "dev-internal-token"))
    worker_id: str = field(default_factory=lambda: _env("FRAMESEEK_WORKER_ID", f"worker-{os.getpid()}"))
    lease_seconds: int = field(default_factory=lambda: int(_env("FRAMESEEK_LEASE_SECONDS", "45")))
    heartbeat_seconds: int = field(default_factory=lambda: int(_env("FRAMESEEK_HEARTBEAT_SECONDS", "10")))
    poll_seconds: float = field(default_factory=lambda: float(_env("FRAMESEEK_POLL_SECONDS", "2")))
    # Unload each model after its stage so peak RAM is the largest model, not the sum (8 GB laptops).
    low_memory: bool = field(default_factory=lambda: _env("FRAMESEEK_LOW_MEMORY", "true") == "true")
    # Fault injection for the reliability demos ("asr", "embed", ...). Never set in normal use.
    fail_stage: str = field(default_factory=lambda: _env("FRAMESEEK_FAIL_STAGE", ""))
    slow_stage: str = field(default_factory=lambda: _env("FRAMESEEK_SLOW_STAGE", ""))
    # Conversational layer
    llm_provider: str = field(default_factory=lambda: _env("FRAMESEEK_LLM_PROVIDER", "rules"))
    llm_base_url: str = field(default_factory=lambda: _env("FRAMESEEK_LLM_BASE_URL", "https://models.github.ai/inference"))
    llm_api_key: str = field(default_factory=lambda: _env("FRAMESEEK_LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("FRAMESEEK_LLM_MODEL", "openai/gpt-4o-mini"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("FRAMESEEK_LLM_TIMEOUT_S", "8")))

    @property
    def index_config_path(self) -> pathlib.Path:
        return self.config_dir / "index.yaml"

    @property
    def retrieval_config_path(self) -> pathlib.Path:
        return self.config_dir / "retrieval.yaml"


@functools.lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def load_yaml(path: pathlib.Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def index_config() -> tuple[dict[str, Any], str]:
    """Return (parsed index config, sha256 of its raw bytes)."""
    raw = settings().index_config_path.read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def retrieval_config() -> dict[str, Any]:
    return load_yaml(settings().retrieval_config_path)


@functools.lru_cache(maxsize=1)
def tool_versions() -> dict[str, str]:
    """Versions recorded in every stage manifest."""
    out: dict[str, str] = {"code": CODE_VERSION}
    try:
        first = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
        out["ffmpeg"] = first.split(" ")[2]
    except Exception:  # pragma: no cover - ffmpeg missing
        out["ffmpeg"] = "missing"
    try:
        out["tesseract"] = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split()[1]
    except Exception:  # pragma: no cover
        out["tesseract"] = "missing"
    return out
