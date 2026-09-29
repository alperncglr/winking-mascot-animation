from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import websockets


LOG = logging.getLogger("nemotron-diarization")
SAMPLE_RATE = 16_000
MAX_AUDIO_FRAME_BYTES = 1024 * 1024


@dataclass(frozen=True)
class InferenceResult:
    speaker_cache: Any
    frame_count: int
    segments: list[dict[str, Any]]


def pcm16_to_float32(payload: bytes) -> np.ndarray:
    if len(payload) % 2:
        raise ValueError("PCM16 frame contains an incomplete sample")
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0


def segments_to_turns(
    segments: list[dict[str, Any]],
    offset_sec: float,
) -> list[dict[str, Any]]:
    raw_turns: list[dict[str, Any]] = []
    for segment in segments:
        start_sec = offset_sec + float(segment["Start"])
        end_sec = offset_sec + float(segment["End"])
        if end_sec <= start_sec:
            continue
        raw_turns.append(
            {
                "speaker_id": f"speaker_{int(segment['Speaker'])}",
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "overlap": False,
            }
        )

    for index, turn in enumerate(raw_turns):
        turn["overlap"] = any(
            other_index != index
            and other["speaker_id"] != turn["speaker_id"]
            and min(turn["end_sec"], other["end_sec"])
            - max(turn["start_sec"], other["start_sec"])
            > 0
            for other_index, other in enumerate(raw_turns)
        )
    return sorted(raw_turns, key=lambda item: (item["start_sec"], item["speaker_id"]))


class NemotronRuntime:
    """Loads one model and serializes GPU forwards across live sessions."""

    def __init__(
        self,
        model_path: str,
        streaming_mode: str,
        device: str,
        dtype_name: str,
        threshold: float,
    ) -> None:
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available")
        dtype_by_name = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        try:
            dtype = dtype_by_name[dtype_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported dtype: {dtype_name}") from exc

        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        self.processor.set_streaming_mode(streaming_mode)
        self.model = AutoModelForAudioFrameClassification.from_pretrained(
            model_path,
            dtype=dtype,
            local_files_only=True,
        ).to(device)
        self.model.eval()
        self.threshold = threshold
        self.lock = asyncio.Lock()
        self.sample_rate = int(self.processor.feature_extractor.sampling_rate)
        if self.sample_rate != SAMPLE_RATE:
            raise RuntimeError(
                f"Expected a {SAMPLE_RATE} Hz model, got {self.sample_rate} Hz"
            )
        self.frame_seconds = (
            float(self.processor.feature_extractor.hop_length) / self.sample_rate
        )
        LOG.info(
            "Model ready: path=%s device=%s dtype=%s mode=%s latency_ms=%s",
            model_path,
            self.model.device,
            self.model.dtype,
            streaming_mode,
            self.processor.streaming_latency_ms,
        )

    async def infer(
        self,
        audio: np.ndarray,
        speaker_cache: Any,
        *,
        is_first: bool,
        is_last: bool,
    ) -> InferenceResult:
        async with self.lock:
            return await asyncio.to_thread(
                self._infer_sync,
                audio,
                speaker_cache,
                is_first,
                is_last,
            )

    def _infer_sync(
        self,
        audio: np.ndarray,
        speaker_cache: Any,
        is_first: bool,
        is_last: bool,
    ) -> InferenceResult:
        inputs = self.processor(
            audio,
            sampling_rate=self.sample_rate,
            is_streaming=True,
            is_first_audio_chunk=is_first,
            is_last_audio_chunk=is_last,
        ).to(self.model.device, dtype=self.model.dtype)
        with self.torch.inference_mode():
            outputs = self.model(**inputs, speaker_cache=speaker_cache)
        logits = outputs.logits
        segments = self.processor.extract_speaker_dict(
            logits,
            threshold=self.threshold,
        )[0]
        return InferenceResult(
            speaker_cache=outputs.speaker_cache,
            frame_count=int(logits.shape[1]),
            segments=segments,
        )


class NemotronSession:
    def __init__(self, runtime: NemotronRuntime) -> None:
        self.runtime = runtime
        self.pending = np.empty(0, dtype=np.float32)
        self.pending_start_sample = 0
        self.total_samples = 0
        self.first_chunk_processed = False
        self.next_mel_frame = 0
        self.speaker_cache = None
        self.emitted_frames = 0

    def append_pcm16(self, payload: bytes) -> None:
        samples = pcm16_to_float32(payload)
        if samples.size:
            self.pending = np.concatenate((self.pending, samples))
            self.total_samples += int(samples.size)

    def has_ready_chunk(self) -> bool:
        expected = (
            self.runtime.processor.num_samples_per_audio_chunk
            if self.first_chunk_processed
            else self.runtime.processor.num_samples_first_audio_chunk
        )
        return self.pending.size >= expected

    def pop_ready_chunk(self) -> tuple[np.ndarray, bool]:
        if not self.has_ready_chunk():
            raise RuntimeError("No complete streaming chunk is buffered")
        is_first = not self.first_chunk_processed
        expected = (
            self.runtime.processor.num_samples_first_audio_chunk
            if is_first
            else self.runtime.processor.num_samples_per_audio_chunk
        )
        audio = self.pending[:expected].copy()
        if is_first:
            self.first_chunk_processed = True
            self.next_mel_frame = self.runtime.processor.num_mel_frames_per_step
        else:
            self.next_mel_frame += self.runtime.processor.num_mel_frames_per_step
        next_start = int(
            self.runtime.processor.audio_chunk_start(self.next_mel_frame)
        )
        trim = next_start - self.pending_start_sample
        if trim <= 0 or trim > self.pending.size:
            raise RuntimeError(
                "Invalid streaming cursor: "
                f"base={self.pending_start_sample} next={next_start} size={self.pending.size}"
            )
        self.pending = self.pending[trim:].copy()
        self.pending_start_sample = next_start
        return audio, is_first

    async def process_available(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        while self.has_ready_chunk():
            audio, is_first = self.pop_ready_chunk()
            payloads.append(
                await self._process_chunk(audio, is_first=is_first, is_last=False)
            )
        return payloads

    async def finish(self) -> dict[str, Any] | None:
        if self.pending.size == 0:
            if self.total_samples == 0:
                return {
                    "type": "diarization",
                    "processed_until_sec": 0.0,
                    "turns": [],
                    "done": True,
                }
            return {
                "type": "diarization",
                "processed_until_sec": round(
                    self.total_samples / self.runtime.sample_rate, 3
                ),
                "turns": [],
                "done": True,
            }
        payload = await self._process_chunk(
            self.pending.copy(),
            is_first=not self.first_chunk_processed,
            is_last=True,
        )
        self.pending = np.empty(0, dtype=np.float32)
        payload["processed_until_sec"] = round(
            self.total_samples / self.runtime.sample_rate,
            3,
        )
        payload["done"] = True
        return payload

    async def _process_chunk(
        self,
        audio: np.ndarray,
        *,
        is_first: bool,
        is_last: bool,
    ) -> dict[str, Any]:
        offset_sec = self.emitted_frames * self.runtime.frame_seconds
        result = await self.runtime.infer(
            audio,
            self.speaker_cache,
            is_first=is_first,
            is_last=is_last,
        )
        self.speaker_cache = result.speaker_cache
        self.emitted_frames += result.frame_count
        return {
            "type": "diarization",
            "processed_until_sec": round(
                self.emitted_frames * self.runtime.frame_seconds,
                3,
            ),
            "turns": segments_to_turns(result.segments, offset_sec),
            "done": is_last,
        }


async def serve_session(websocket, runtime: NemotronRuntime) -> None:
    session = NemotronSession(runtime)
    meeting_id = '-'
    session_id = uuid.uuid4().hex
    await websocket.send(
        json.dumps(
            {
                "type": "status",
                "status": "ready",
                "backend": "nemotron3_diarization",
                "streaming_mode": runtime.processor.streaming_mode,
                "latency_ms": runtime.processor.streaming_latency_ms,
            }
        )
    )
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) > MAX_AUDIO_FRAME_BYTES:
                    raise ValueError("Audio frame exceeds 1 MiB")
                session.append_pcm16(message)
                for payload in await session.process_available():
                    payload.update(meeting_id=meeting_id, session_id=session_id)
                    LOG.info('meeting_id=%s session_id=%s processed_until_sec=%s turns=%s',
                             meeting_id, session_id, payload.get('processed_until_sec'),
                             len(payload.get('turns', [])))
                    await websocket.send(json.dumps(payload, ensure_ascii=False))
                continue
            command = json.loads(message)
            if isinstance(command.get('config'), dict):
                config = command['config']
                meeting_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(config.get('meeting_id', '-')))[:64]
                session_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(config.get('session_id') or session_id))[:64]
                LOG.info('meeting_id=%s session_id=%s session configured', meeting_id, session_id)
                continue
            if command.get("eof"):
                final_payload = await session.finish()
                if final_payload is not None:
                    final_payload.update(meeting_id=meeting_id, session_id=session_id)
                    await websocket.send(
                        json.dumps(final_payload, ensure_ascii=False)
                    )
                return
    except websockets.ConnectionClosed:
        LOG.info('meeting_id=%s session_id=%s Client disconnected before EOF', meeting_id, session_id)
    except Exception:
        LOG.exception('meeting_id=%s session_id=%s Diarization session failed', meeting_id, session_id)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "status",
                        "status": "error",
                        "detail": "Diarization session failed",
                    }
                )
            )
        except websockets.ConnectionClosed:
            pass
    finally:
        LOG.info('meeting_id=%s session_id=%s session closed', meeting_id, session_id)


async def run_server(host: str, port: int, runtime: NemotronRuntime) -> None:
    async def handler(websocket) -> None:
        await serve_session(websocket, runtime)

    async with websockets.serve(
        handler,
        host,
        port,
        max_size=MAX_AUDIO_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ):
        LOG.info("Listening on ws://%s:%s", host, port)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nemotron 3 diarization WebSocket canary"
    )
    parser.add_argument("--host", default=os.getenv("DIARIZATION_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("DIARIZATION_PORT", "2710"))
    )
    parser.add_argument(
        "--model-path",
        default=os.getenv("NEMOTRON_MODEL_PATH", "/models/Nemotron-3-Diarization"),
    )
    parser.add_argument(
        "--streaming-mode",
        choices=("low_latency", "very_low_latency", "ultra_low_latency"),
        default=os.getenv("NEMOTRON_STREAMING_MODE", "low_latency"),
    )
    parser.add_argument("--device", default=os.getenv("NEMOTRON_DEVICE", "cuda"))
    parser.add_argument(
        "--dtype",
        choices=("float16", "bfloat16", "float32"),
        default=os.getenv("NEMOTRON_DTYPE", "float16"),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("NEMOTRON_THRESHOLD", "0.5")),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not 0.0 < args.threshold < 1.0:
        parser.error("--threshold must be between 0 and 1")

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    runtime = NemotronRuntime(
        model_path=args.model_path,
        streaming_mode=args.streaming_mode,
        device=args.device,
        dtype_name=args.dtype,
        threshold=args.threshold,
    )
    if args.check:
        print(
            json.dumps(
                {
                    "ok": True,
                    "model_path": str(Path(args.model_path).resolve()),
                    "device": str(runtime.model.device),
                    "dtype": str(runtime.model.dtype),
                    "sample_rate": runtime.sample_rate,
                    "streaming_mode": runtime.processor.streaming_mode,
                    "latency_ms": runtime.processor.streaming_latency_ms,
                    "threshold": runtime.threshold,
                }
            )
        )
        return
    asyncio.run(run_server(args.host, args.port, runtime))


if __name__ == "__main__":
    main()
