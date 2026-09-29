from __future__ import annotations

import os
import wave
from pathlib import Path

from meeting_scribe.config import CaptureConfig
from meeting_scribe.services.capture.manifest import AudioChunk, Manifest


class CaptureDependencyError(RuntimeError):
    pass


def _next_chunk_index(chunks: list[AudioChunk]) -> int:
    indexes: list[int] = []
    for chunk in chunks:
        stem = Path(chunk.file_name).stem
        try:
            indexes.append(int(stem.rsplit('_', 1)[-1]))
        except ValueError:
            continue
    return max(indexes, default=0) + 1


class ChunkRecorder:
    def __init__(self, config: CaptureConfig, audio_dir: Path, manifest_path: Path) -> None:
        self.config = config
        self.audio_dir = audio_dir
        self.manifest = Manifest(manifest_path)

    def record_for_seconds(self, seconds: int) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise CaptureDependencyError(
                "Install capture extras to use microphone recording: pip install -e .[capture]"
            ) from exc

        self.audio_dir.mkdir(parents=True, exist_ok=True)
        existing = self.manifest.read_all()
        chunk_index = _next_chunk_index(existing)
        elapsed = max(
            (chunk.start_sec + chunk.duration_sec for chunk in existing),
            default=0.0,
        )
        remaining = seconds
        while remaining > 0:
            duration = min(self.config.chunk_seconds, remaining)
            frames = sd.rec(
                int(duration * self.config.sample_rate),
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype="float32",
            )
            sd.wait()
            file_name = f"chunk_{chunk_index:06d}.wav"
            sf.write(self.audio_dir / file_name, frames, self.config.sample_rate)
            sample_count = int(duration * self.config.sample_rate)
            self.manifest.append(
                AudioChunk(
                    file_name=file_name,
                    start_sec=round(elapsed, 3),
                    duration_sec=float(duration),
                    sample_count=sample_count,
                    sample_rate=self.config.sample_rate,
                )
            )
            chunk_index += 1
            elapsed += duration
            remaining -= duration


class NetworkChunkWriter:
    """Writes durable WAV chunks + manifest entries from PCM pushed over the network
    (the meeting-room client app streams mic audio to the A4000 backend; this is the
    production counterpart to `ChunkRecorder`, which assumes a mic local to the server
    process and is kept only for same-machine dev/test setups)."""

    def __init__(self, audio_dir: Path, manifest_path: Path, sample_rate: int, chunk_seconds: int) -> None:
        self.audio_dir = audio_dir
        self.manifest = Manifest(manifest_path)
        self.sample_rate = sample_rate
        self.chunk_seconds = chunk_seconds
        self.chunk_bytes = chunk_seconds * sample_rate * 2  # 16-bit mono
        self._buffer = bytearray()
        existing = self.manifest.read_all()
        self._chunk_index = _next_chunk_index(existing)
        self._elapsed_sec = max(
            (chunk.start_sec + chunk.duration_sec for chunk in existing),
            default=0.0,
        )

    @property
    def elapsed_sec(self) -> float:
        return self._elapsed_sec

    def push(self, pcm_chunk: bytes) -> None:
        self._buffer.extend(pcm_chunk)
        while len(self._buffer) >= self.chunk_bytes:
            payload = bytes(self._buffer[: self.chunk_bytes])
            del self._buffer[: self.chunk_bytes]
            self._write_chunk(payload, float(self.chunk_seconds))

    def finalize(self) -> None:
        if not self._buffer:
            return
        if len(self._buffer) % 2:
            raise ValueError('PCM16 payload ended with an incomplete sample')
        duration = len(self._buffer) / (self.sample_rate * 2)
        self._write_chunk(bytes(self._buffer), duration)
        self._buffer.clear()

    def _write_chunk(self, payload: bytes, duration_sec: float) -> None:
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        file_name = f'chunk_{self._chunk_index:06d}.wav'
        final_path = self.audio_dir / file_name
        temporary_path = final_path.with_suffix('.wav.part')
        with wave.open(str(temporary_path), 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(payload)
        os.replace(temporary_path, final_path)
        self.manifest.append(
            AudioChunk(
                file_name=file_name,
                start_sec=round(self._elapsed_sec, 3),
                duration_sec=round(duration_sec, 3),
                sample_count=len(payload) // 2,
                sample_rate=self.sample_rate,
            )
        )
        self._elapsed_sec += duration_sec
        self._chunk_index += 1


def resolve_staged_wav_path(source_path: str, runtime_root: Path) -> Path:
    """Resolve a WAV import without allowing reads outside the staging area."""
    import_root = (runtime_root / 'imports').resolve()
    candidate = Path(source_path)
    candidate = (
        candidate if candidate.is_absolute() else import_root / candidate
    ).resolve(strict=True)
    if not candidate.is_relative_to(import_root):
        raise ValueError('WAV source must be inside the runtime imports directory')
    return candidate


def import_wav_as_single_chunk(source_wav: Path, audio_dir: Path, manifest_path: Path) -> None:
    from shutil import copyfile

    from meeting_scribe.services.capture.manifest import wav_duration

    if source_wav.suffix.lower() != '.wav' or not source_wav.is_file():
        raise ValueError(f'Expected an existing WAV file: {source_wav}')
    duration, sample_count, _channels = wav_duration(source_wav)
    manifest = Manifest(manifest_path)
    existing = manifest.read_all()
    index = _next_chunk_index(existing)
    start_sec = max(
        (chunk.start_sec + chunk.duration_sec for chunk in existing),
        default=0.0,
    )
    audio_dir.mkdir(parents=True, exist_ok=True)
    target = audio_dir / f'chunk_{index:06d}.wav'
    temporary = target.with_suffix('.wav.part')
    copyfile(source_wav, temporary)
    os.replace(temporary, target)
    manifest.append(
        AudioChunk(
            file_name=target.name,
            start_sec=start_sec,
            duration_sec=duration,
            sample_count=sample_count,
            sample_rate=int(sample_count / duration) if duration else 0,
        )
    )


def create_silent_chunk(audio_dir: Path, manifest_path: Path, sample_rate: int, seconds: int) -> None:
    manifest = Manifest(manifest_path)
    existing = manifest.read_all()
    index = _next_chunk_index(existing)
    start_sec = max(
        (chunk.start_sec + chunk.duration_sec for chunk in existing),
        default=0.0,
    )
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / f'chunk_{index:06d}.wav'
    temporary = path.with_suffix('.wav.part')
    sample_count = sample_rate * seconds
    with wave.open(str(temporary), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b'\x00\x00' * sample_count)
    os.replace(temporary, path)
    manifest.append(
        AudioChunk(
            file_name=path.name,
            start_sec=start_sec,
            duration_sec=float(seconds),
            sample_count=sample_count,
            sample_rate=sample_rate,
        )
    )
