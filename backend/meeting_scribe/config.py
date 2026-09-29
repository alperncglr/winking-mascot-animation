from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    name: str
    host: str
    port: int


@dataclass(frozen=True)
class PathConfig:
    database: Path
    runtime_root: Path
    archive_root: Path


@dataclass(frozen=True)
class AuthConfig:
    dev_started_by_name: str
    dev_started_by_id: str
    trusted_name_header: str
    trusted_id_header: str
    api_key_env: str


@dataclass(frozen=True)
class CaptureConfig:
    device_name_hint: str
    sample_rate: int
    channels: int
    chunk_seconds: int


@dataclass(frozen=True)
class OfflineConfig:
    whisper_backend: str
    whisper_api_base_url: str
    whisper_ws_url: str
    whisper_api_timeout_seconds: int
    whisper_ws_receive_timeout_seconds: int
    whisper_ws_chunk_bytes: int
    whisper_model: str
    whisper_compute_type: str
    diarizer_backend: str
    pyannote_model: str
    hf_token_env: str
    overlap_min_seconds: float
    pyannote_segmentation_batch_size: int = 8
    pyannote_embedding_batch_size: int = 8


@dataclass(frozen=True)
class LiveConfig:
    backend: str
    whisper_model: str
    whisper_chunk_seconds: float
    diarization_backend: str
    diarization_ws_url: str
    diarization_wait_seconds: float
    sample_rate: int
    window_seconds: float
    silence_rms_threshold: int
    silence_flush_seconds: float
    min_decode_seconds: float
    whisper_context_seconds: float = 10.0


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: int
    max_transcript_chars_per_chunk: int


@dataclass(frozen=True)
class RuntimeCleanupConfig:
    abandoned_meeting_timeout_hours: int
    scan_interval_minutes: int


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    paths: PathConfig
    auth: AuthConfig
    capture: CaptureConfig
    offline: OfflineConfig
    live: LiveConfig
    llm: LlmConfig
    runtime_cleanup: RuntimeCleanupConfig


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _resolve_model_reference(base_dir: Path, value: str) -> str:
    path = Path(value)
    if path.is_absolute() or value.startswith(('./', '../', 'models/')):
        return str(_resolve_path(base_dir, value).resolve())
    return value


def load_settings(config_path: str | Path = "config.yaml") -> Settings:
    path = Path(config_path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle)
    base_dir = path.parent
    offline_raw = dict(raw["offline"])
    offline_raw["pyannote_model"] = _resolve_model_reference(
        base_dir,
        offline_raw["pyannote_model"],
    )
    return Settings(
        app=AppConfig(**raw["app"]),
        paths=PathConfig(
            database=_resolve_path(base_dir, raw["paths"]["database"]),
            runtime_root=_resolve_path(base_dir, raw["paths"]["runtime_root"]),
            archive_root=_resolve_path(base_dir, raw["paths"]["archive_root"]),
        ),
        auth=AuthConfig(**raw["auth"]),
        capture=CaptureConfig(**raw["capture"]),
        offline=OfflineConfig(**offline_raw),
        live=LiveConfig(**raw["live"]),
        llm=LlmConfig(**raw["llm"]),
        runtime_cleanup=RuntimeCleanupConfig(**raw["runtime_cleanup"]),
    )
