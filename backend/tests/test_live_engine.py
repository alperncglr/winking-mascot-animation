from __future__ import annotations

import struct
import unittest

from meeting_scribe.services.live.engine import LiveEngine


def tone_chunk(seconds: float, amplitude: int, sample_rate: int = 16000) -> bytes:
    sample_count = int(seconds * sample_rate)
    frames = bytearray()
    for _ in range(sample_count):
        frames.extend(struct.pack("<h", amplitude))
    return bytes(frames)


class FakeTranscriber:
    """Returns a scripted sequence of hypotheses, one per call, to exercise local agreement."""

    def __init__(self, hypotheses: list[str]) -> None:
        self.hypotheses = hypotheses
        self.calls = 0

    def transcribe_window(self, pcm: bytes, sample_rate: int, language) -> str:
        text = self.hypotheses[min(self.calls, len(self.hypotheses) - 1)]
        self.calls += 1
        return text


class FakeDiarizer:
    def __init__(self, speaker_id: str = "speaker_0") -> None:
        self.speaker_id = speaker_id

    def current_speaker(self, pcm: bytes, sample_rate: int) -> str:
        return self.speaker_id


class LiveEngineTests(unittest.TestCase):
    def _make_engine(self, transcriber, diarizer=None, **overrides) -> LiveEngine:
        labels: dict[str, str] = {}

        def resolve(speaker_id: str) -> str:
            return labels.setdefault(speaker_id, f"Kullanıcı {len(labels) + 1}")

        return LiveEngine(
            transcriber=transcriber,
            diarizer=diarizer or FakeDiarizer(),
            resolve_participant_label=resolve,
            silence_rms_threshold=300,
            silence_flush_seconds=0.5,
            **overrides,
        )

    def test_provisional_revisions_grow_and_text_stabilizes(self) -> None:
        transcriber = FakeTranscriber(["Merhaba", "Merhaba dünya", "Merhaba dünya nasılsın"])
        engine = self._make_engine(transcriber)
        speech = tone_chunk(0.1, amplitude=12000)

        first = engine.push_audio(speech)
        second = engine.push_audio(speech)
        third = engine.push_audio(speech)

        self.assertEqual(first.status, "provisional")
        self.assertEqual(first.revision, 1)
        self.assertEqual(second.revision, 2)
        self.assertEqual(third.revision, 3)
        self.assertEqual(third.text, "Merhaba dünya nasılsın")
        self.assertEqual(first.speaker_label, "Kullanıcı 1")
        # all three ticks belong to the same not-yet-flushed segment
        self.assertEqual({first.segment_id, second.segment_id, third.segment_id}, {first.segment_id})

    def test_silence_flushes_segment_as_final_and_starts_new_one(self) -> None:
        transcriber = FakeTranscriber(["Merhaba dünya"])
        engine = self._make_engine(transcriber)
        speech = tone_chunk(0.1, amplitude=12000)
        silence = tone_chunk(0.6, amplitude=0)

        provisional = engine.push_audio(speech)
        final = engine.push_audio(silence)

        self.assertEqual(provisional.status, "provisional")
        self.assertEqual(final.status, "final")
        self.assertEqual(final.text, "Merhaba dünya")
        self.assertEqual(final.segment_id, provisional.segment_id)

        # next speech tick starts a fresh segment id
        next_provisional = engine.push_audio(speech)
        self.assertNotEqual(next_provisional.segment_id, provisional.segment_id)
        self.assertEqual(next_provisional.revision, 1)

    def test_leading_silence_with_no_speech_yet_emits_nothing(self) -> None:
        transcriber = FakeTranscriber([""])
        engine = self._make_engine(transcriber)
        silence = tone_chunk(0.6, amplitude=0)

        event = engine.push_audio(silence)

        self.assertIsNone(event)

    def test_different_speakers_get_distinct_labels(self) -> None:
        transcriber = FakeTranscriber(["Merhaba"])
        diarizer = FakeDiarizer("speaker_0")
        engine = self._make_engine(transcriber, diarizer=diarizer)
        speech = tone_chunk(0.1, amplitude=12000)

        first = engine.push_audio(speech)
        diarizer.speaker_id = "speaker_1"
        second = engine.push_audio(speech)

        self.assertEqual(first.speaker_label, "Kullanıcı 1")
        self.assertEqual(second.speaker_label, "Kullanıcı 2")


if __name__ == "__main__":
    unittest.main()
