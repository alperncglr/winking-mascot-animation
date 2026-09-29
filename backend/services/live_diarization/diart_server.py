from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import re
import sys
import threading
import types
from pathlib import Path
from typing import Any

import numpy as np
import websockets
import torch


def _install_diart_compatibility_shims() -> None:
    import torchaudio

    if not hasattr(torchaudio, 'set_audio_backend'):
        torchaudio.set_audio_backend = lambda *_args, **_kwargs: None

    try:
        import torchaudio.io  # noqa: F401
    except ImportError:
        torchaudio_io = types.ModuleType('torchaudio.io')

        class UnsupportedStreamReader:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError('StreamReader is not used by the push-audio service')

        torchaudio_io.StreamReader = UnsupportedStreamReader
        sys.modules['torchaudio.io'] = torchaudio_io

    try:
        import sounddevice  # noqa: F401
    except (ImportError, OSError):
        sys.modules['sounddevice'] = types.ModuleType('sounddevice')

    try:
        import websocket_server  # noqa: F401
    except ImportError:
        websocket_server = types.ModuleType('websocket_server')

        class UnsupportedWebsocketServer:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError(
                    'diart WebsocketServer is not used by this service'
                )

        websocket_server.WebsocketServer = UnsupportedWebsocketServer
        sys.modules['websocket_server'] = websocket_server


os.environ.setdefault('PYANNOTE_METRICS_ENABLED', '0')
_install_diart_compatibility_shims()

from diart import SpeakerDiarization, SpeakerDiarizationConfig
from diart.inference import StreamingInference
from diart.models import EmbeddingModel, PowersetAdapter, SegmentationModel
from diart.sources import AudioSource
from pyannote.audio import Model


LOG = logging.getLogger('diart-server')


class PushAudioSource(AudioSource):
    def __init__(self, uri: str, sample_rate: int) -> None:
        super().__init__(uri, sample_rate)
        self._items: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=100)
        self._closed = False

    def push_pcm16(self, payload: bytes) -> None:
        if self._closed:
            return
        if len(payload) % 2:
            raise ValueError('PCM16 frame contains an incomplete sample')
        samples = np.frombuffer(payload, dtype='<i2').astype(np.float32) / 32768.0
        self._items.put(samples.reshape(1, -1))

    def read(self) -> None:
        try:
            while True:
                item = self._items.get()
                if item is None:
                    break
                self.stream.on_next(item)
            self.stream.on_completed()
        except BaseException as exc:
            self.stream.on_error(exc)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._items.put(None)


class DiartSession:
    def __init__(
        self,
        session_id: str,
        pipeline_config: SpeakerDiarizationConfig,
        event_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.source = PushAudioSource(session_id, pipeline_config.sample_rate)
        self.output: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.event_loop = event_loop
        pipeline = SpeakerDiarization(pipeline_config)
        self.inference = StreamingInference(
            pipeline,
            self.source,
            do_profile=False,
            do_plot=False,
            show_progress=False,
        )
        self.inference.attach_hooks(self._on_prediction)
        self.thread = threading.Thread(
            target=self._run,
            name=f'diart-{session_id}',
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def push(self, payload: bytes) -> None:
        self.source.push_pcm16(payload)

    async def close(self) -> None:
        self.source.close()
        await asyncio.to_thread(self.thread.join, 10)
        await self.output.put(None)

    def _run(self) -> None:
        self.inference()

    def _on_prediction(self, result) -> None:
        annotation, waveform = result
        raw_turns: list[tuple[float, float, str]] = []
        for turn, _track, label in annotation.itertracks(yield_label=True):
            raw_turns.append(
                (float(turn.start), float(turn.end), _speaker_id(label))
            )

        turns: list[dict[str, Any]] = []
        for index, (start_sec, end_sec, speaker_id) in enumerate(raw_turns):
            overlap = any(
                other_index != index
                and min(end_sec, other_end) - max(start_sec, other_start) > 0
                for other_index, (other_start, other_end, _other_speaker) in enumerate(raw_turns)
            )
            turns.append(
                {
                    'speaker_id': speaker_id,
                    'start_sec': start_sec,
                    'end_sec': end_sec,
                    'overlap': overlap,
                }
            )
        processed_until = float(waveform.extent.end)
        payload = {
            'type': 'diarization',
            'processed_until_sec': processed_until,
            'turns': turns,
        }
        self.event_loop.call_soon_threadsafe(self.output.put_nowait, payload)


def _speaker_id(label: Any) -> str:
    value = str(label).strip()
    match = re.fullmatch(r'speaker[_-]?(\d+)', value, flags=re.IGNORECASE)
    if match:
        return f'speaker_{int(match.group(1))}'
    if value.isdigit():
        return f'speaker_{int(value)}'
    return value if value.startswith('speaker_') else f'speaker_{value}'


async def serve_session(websocket, pipeline_config: SpeakerDiarizationConfig) -> None:
    session_id = f'{id(websocket):x}'
    meeting_id = '-'
    session = DiartSession(session_id, pipeline_config, asyncio.get_running_loop())
    session.start()

    async def send_predictions() -> None:
        while True:
            payload = await session.output.get()
            if payload is None:
                return
            payload.update(meeting_id=meeting_id, session_id=session_id)
            LOG.info('meeting_id=%s session_id=%s processed_until_sec=%s turns=%s',
                     meeting_id, session_id, payload.get('processed_until_sec'),
                     len(payload.get('turns', [])))
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    sender = asyncio.create_task(send_predictions())
    await websocket.send(json.dumps({'type': 'status', 'status': 'ready'}))
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) > 1024 * 1024:
                    raise ValueError('Audio frame exceeds 1 MiB')
                await asyncio.to_thread(session.push, message)
                continue
            payload = json.loads(message)
            if isinstance(payload.get('config'), dict):
                config = payload['config']
                meeting_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(config.get('meeting_id') or '-'))[:64]
                session_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(config.get('session_id') or session_id))[:64]
                LOG.info('meeting_id=%s session_id=%s session configured', meeting_id, session_id)
                continue
            if payload.get('eof'):
                break
    finally:
        await session.close()
        await sender
        LOG.info('meeting_id=%s session_id=%s session closed', meeting_id, session_id)


def build_pipeline_config(
    model_root: Path,
    step: float,
    latency: float,
) -> SpeakerDiarizationConfig:
    segmentation_path = model_root / 'segmentation' / 'pytorch_model.bin'
    embedding_path = model_root / 'embedding' / 'pytorch_model.bin'
    for path in (segmentation_path, embedding_path):
        if not path.is_file():
            raise FileNotFoundError(f'Required local model file not found: {path}')

    raw_segmentation = Model.from_pretrained(segmentation_path)
    raw_embedding = Model.from_pretrained(embedding_path)
    if raw_segmentation is None or raw_embedding is None:
        raise RuntimeError('Could not load local pyannote models')

    specifications = raw_segmentation.specifications
    if isinstance(specifications, (list, tuple)):
        specifications = specifications[0]
    duration = float(getattr(specifications, 'duration', 5.0))
    segmentation_model = raw_segmentation
    if bool(getattr(specifications, 'powerset', False)):
        segmentation_model = PowersetAdapter(raw_segmentation)

    segmentation = SegmentationModel(lambda: segmentation_model)
    embedding = EmbeddingModel(lambda: raw_embedding)
    return SpeakerDiarizationConfig(
        segmentation=segmentation,
        embedding=embedding,
        duration=duration,
        step=step,
        latency=latency,
        device=torch.device('cuda' if torch.cuda.is_available() else 'cpu'),
        sample_rate=16000,
    )


async def run_server(
    host: str,
    port: int,
    pipeline_config: SpeakerDiarizationConfig,
) -> None:

    async def handler(websocket) -> None:
        await serve_session(websocket, pipeline_config)

    async with websockets.serve(handler, host, port, max_size=1024 * 1024):
        await asyncio.Future()


def main() -> None:
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    )
    parser = argparse.ArgumentParser(description='Multi-session diart WebSocket service')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2710)
    parser.add_argument('--step', type=float, default=0.5)
    parser.add_argument('--latency', type=float, default=1.0)
    parser.add_argument(
        '--model-root',
        type=Path,
        default=os.getenv('PYANNOTE_MODEL_DIR'),
    )
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.model_root is None:
        parser.error('--model-root or PYANNOTE_MODEL_DIR is required')
    config = build_pipeline_config(args.model_root.resolve(), args.step, args.latency)
    if args.check:
        print(
            json.dumps(
                {
                    'ok': True,
                    'model_root': str(args.model_root.resolve()),
                    'sample_rate': config.sample_rate,
                    'duration': config.duration,
                    'step': config.step,
                    'latency': config.latency,
                    'device': str(config.device),
                }
            )
        )
        return
    asyncio.run(run_server(args.host, args.port, config))


if __name__ == '__main__':
    main()
