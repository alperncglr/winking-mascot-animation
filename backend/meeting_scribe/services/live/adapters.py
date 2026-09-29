from __future__ import annotations

from typing import Optional, Protocol


class StreamingTranscriber(Protocol):
    def transcribe_window(self, pcm: bytes, sample_rate: int, language: Optional[str]) -> str:
        ...


class LiveDiarizer(Protocol):
    def current_speaker(self, pcm: bytes, sample_rate: int) -> str:
        ...


class PlaceholderStreamingTranscriber:
    """Deterministic stand-in used until faster-whisper streaming is wired in."""

    def transcribe_window(self, pcm: bytes, sample_rate: int, language: Optional[str]) -> str:
        return f"[canlı transkript bekleniyor: {len(pcm)} bayt]"


class PlaceholderLiveDiarizer:
    """Single anonymous speaker stand-in.

    NOT wired to diart yet — intentionally, not an oversight. diart is Rx-based
    (audio is pushed into a Subject-backed AudioSource; speaker annotations arrive
    asynchronously on a subscription, not as a synchronous return value per chunk),
    and its public API has changed across releases. Writing an adapter against a
    remembered API signature without the library installed and a real audio stream
    to validate against would likely ship broken, false-confidence code. This needs
    `pip install diart` on the A4000 box, followed by wiring the actual current
    `diart.sources.AudioSource` / `diart.inference.StreamingInference` API to this
    Protocol's `current_speaker` call, then testing against real multi-speaker audio.
    """

    def current_speaker(self, pcm: bytes, sample_rate: int) -> str:
        return "speaker_0"


class FasterWhisperStreamingTranscriber:
    def __init__(self, model_name: str, compute_type: str = "float16") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install offline extras to use faster-whisper") from exc
        self.model = WhisperModel(model_name, device="cuda", compute_type=compute_type)

    def transcribe_window(self, pcm: bytes, sample_rate: int, language: Optional[str]) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio, language=language, word_timestamps=False, vad_filter=True
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


def build_streaming_transcriber(backend: str, model_name: str, compute_type: str = "float16") -> StreamingTranscriber:
    if backend == "local_faster_whisper":
        return FasterWhisperStreamingTranscriber(model_name, compute_type)
    return PlaceholderStreamingTranscriber()
