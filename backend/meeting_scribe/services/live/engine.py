from __future__ import annotations

import audioop
from dataclasses import dataclass
from typing import Callable, List, Optional

from meeting_scribe.services.live.adapters import LiveDiarizer, StreamingTranscriber


def _longest_common_prefix_words(previous: List[str], current: List[str]) -> List[str]:
    prefix: List[str] = []
    for a, b in zip(previous, current):
        if a != b:
            break
        prefix.append(a)
    return prefix


@dataclass(frozen=True)
class LiveSegmentEvent:
    segment_id: str
    revision: int
    status: str  # "provisional" | "final"
    speaker_label: str
    text: str


class LiveEngine:
    """Rolling-window local-agreement live transcription.

    Reference: Macháček, Dabre, Bojar (2023) — whisper_streaming / LocalAgreement-2.
    Audio is pushed in small ticks via `push_audio`. Each tick re-transcribes the
    trailing `window_seconds` of audio; the longest common word prefix against the
    previous tick's hypothesis is treated as committed, the rest stays tentative.
    A simple RMS-energy silence heuristic stands in for Silero VAD and triggers the
    final flush of a segment, matching the same placeholder-first pattern used by
    the offline pipeline's adapters.
    """

    def __init__(
        self,
        transcriber: StreamingTranscriber,
        diarizer: LiveDiarizer,
        resolve_participant_label: Callable[[str], str],
        sample_rate: int = 16000,
        window_seconds: float = 10.0,
        language: Optional[str] = None,
        silence_rms_threshold: int = 300,
        silence_flush_seconds: float = 1.2,
        min_decode_seconds: float = 0.0,
    ) -> None:
        self.transcriber = transcriber
        self.diarizer = diarizer
        self.resolve_participant_label = resolve_participant_label
        self.sample_rate = sample_rate
        self.window_bytes = int(window_seconds * sample_rate * 2)
        self.language = language
        self.silence_rms_threshold = silence_rms_threshold
        self.silence_flush_bytes = int(silence_flush_seconds * sample_rate * 2)
        self.min_decode_bytes = int(min_decode_seconds * sample_rate * 2)

        self._buffer = bytearray()
        self._silence_bytes = 0
        self._bytes_since_decode = 0
        self._previous_words: List[str] = []
        self._segment_index = 0
        self._revision = 0
        self._segment_id = self._new_segment_id()

    def _new_segment_id(self) -> str:
        self._segment_index += 1
        return f"live_{self._segment_index:06d}"

    def push_audio(self, pcm_chunk: bytes) -> Optional[LiveSegmentEvent]:
        chunk_rms = audioop.rms(pcm_chunk, 2) if pcm_chunk else 0
        self._silence_bytes = 0 if chunk_rms >= self.silence_rms_threshold else self._silence_bytes + len(pcm_chunk)
        self._buffer.extend(pcm_chunk)
        self._bytes_since_decode += len(pcm_chunk)
        if len(self._buffer) > self.window_bytes:
            del self._buffer[: len(self._buffer) - self.window_bytes]

        if self._silence_bytes >= self.silence_flush_bytes:
            return self.flush() if self._previous_words else self._reset_silently()

        if self._bytes_since_decode < self.min_decode_bytes:
            return None

        window = bytes(self._buffer)
        self._bytes_since_decode = 0
        words = self.transcriber.transcribe_window(window, self.sample_rate, self.language).split()
        agreed = _longest_common_prefix_words(self._previous_words, words)
        self._previous_words = words

        self._revision += 1
        speaker_label = self.resolve_participant_label(self.diarizer.current_speaker(window, self.sample_rate))
        return LiveSegmentEvent(
            segment_id=self._segment_id,
            revision=self._revision,
            status="provisional",
            speaker_label=speaker_label,
            text=" ".join(words) if agreed or words else "",
        )

    def flush(self) -> LiveSegmentEvent:
        """Called when the silence heuristic (stand-in for Silero VAD) detects end of speech."""
        window = bytes(self._buffer)
        speaker_label = self.resolve_participant_label(self.diarizer.current_speaker(window, self.sample_rate))
        self._revision += 1
        event = LiveSegmentEvent(
            segment_id=self._segment_id,
            revision=self._revision,
            status="final",
            speaker_label=speaker_label,
            text=" ".join(self._previous_words),
        )
        self._start_new_segment()
        return event

    def _reset_silently(self) -> None:
        self._start_new_segment()
        return None

    def _start_new_segment(self) -> None:
        self._buffer.clear()
        self._previous_words = []
        self._bytes_since_decode = 0
        self._revision = 0
        self._segment_id = self._new_segment_id()
