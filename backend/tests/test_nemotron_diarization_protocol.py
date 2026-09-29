import asyncio

import numpy as np

from services.nemotron_diarization.nemotron_server import (
    InferenceResult,
    NemotronSession,
    pcm16_to_float32,
    segments_to_turns,
)


def test_pcm16_to_float32() -> None:
    samples = pcm16_to_float32(b"\x00\x00\xff\x7f\x00\x80")
    assert samples.tolist() == [0.0, 32767 / 32768, -1.0]


def test_segments_to_turns_marks_different_speaker_overlap() -> None:
    turns = segments_to_turns(
        [
            {"Start": 0.0, "End": 1.2, "Speaker": 0},
            {"Start": 0.8, "End": 1.5, "Speaker": 1},
            {"Start": 1.5, "End": 2.0, "Speaker": 1},
        ],
        offset_sec=2.0,
    )
    assert turns == [
        {
            "speaker_id": "speaker_0",
            "start_sec": 2.0,
            "end_sec": 3.2,
            "overlap": True,
        },
        {
            "speaker_id": "speaker_1",
            "start_sec": 2.8,
            "end_sec": 3.5,
            "overlap": True,
        },
        {
            "speaker_id": "speaker_1",
            "start_sec": 3.5,
            "end_sec": 4.0,
            "overlap": False,
        },
    ]


class _FakeProcessor:
    num_samples_first_audio_chunk = 10
    num_samples_per_audio_chunk = 8
    num_mel_frames_per_step = 6

    @staticmethod
    def audio_chunk_start(mel_frame_idx: int) -> int:
        return mel_frame_idx - 2


class _FakeRuntime:
    processor = _FakeProcessor()
    frame_seconds = 0.01
    sample_rate = 16

    async def infer(self, audio, speaker_cache, *, is_first, is_last):
        return InferenceResult(
            speaker_cache=(speaker_cache or 0) + 1,
            frame_count=6 if not is_last else len(audio),
            segments=[{"Start": 0.0, "End": 0.03, "Speaker": 0}],
        )


def test_session_keeps_required_overlap_and_flushes_tail() -> None:
    async def exercise() -> None:
        session = NemotronSession(_FakeRuntime())
        pcm = np.arange(16, dtype="<i2").tobytes()
        session.append_pcm16(pcm)

        payloads = await session.process_available()
        assert len(payloads) == 2
        assert session.pending_start_sample == 10
        assert session.pending.size == 6

        final = await session.finish()
        assert final is not None
        assert final["done"] is True
        assert final["processed_until_sec"] == 1.0
        assert session.speaker_cache == 3

    asyncio.run(exercise())


def test_finish_sends_done_when_no_audio_is_pending() -> None:
    class NoOverlapProcessor(_FakeProcessor):
        @staticmethod
        def audio_chunk_start(mel_frame_idx: int) -> int:
            return 10

    class NoOverlapRuntime(_FakeRuntime):
        processor = NoOverlapProcessor()

    async def exercise() -> None:
        session = NemotronSession(NoOverlapRuntime())
        session.append_pcm16(np.arange(10, dtype='<i2').tobytes())
        await session.process_available()
        assert session.pending.size == 0
        final = await session.finish()
        assert final == {
            'type': 'diarization',
            'processed_until_sec': 0.625,
            'turns': [],
            'done': True,
        }

    asyncio.run(exercise())
