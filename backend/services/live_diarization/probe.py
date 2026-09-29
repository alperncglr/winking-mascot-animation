from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
import websockets


TARGET_SAMPLE_RATE = 16000


def read_pcm16_mono(path: Path) -> tuple[bytes, float]:
    with wave.open(str(path), 'rb') as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        payload = handle.readframes(frame_count)
    if sample_width != 2:
        raise ValueError(f'Expected PCM16 WAV, got sample_width={sample_width}')

    samples = np.frombuffer(payload, dtype='<i2')
    if channels > 1:
        usable = samples[: samples.size - (samples.size % channels)]
        samples = usable.reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        samples = samples.astype(np.float32)

    if sample_rate != TARGET_SAMPLE_RATE and samples.size:
        target_count = round(samples.size * TARGET_SAMPLE_RATE / sample_rate)
        source_positions = np.arange(samples.size, dtype=np.float64)
        target_positions = np.linspace(
            0,
            samples.size,
            target_count,
            endpoint=False,
            dtype=np.float64,
        )
        samples = np.interp(target_positions, source_positions, samples)

    pcm = np.clip(np.rint(samples), -32768, 32767).astype('<i2').tobytes()
    return pcm, len(pcm) / (TARGET_SAMPLE_RATE * 2)


async def run_probe(
    wav_path: Path,
    url: str,
    chunk_ms: int,
    receive_timeout: float,
) -> dict:
    pcm, duration_sec = read_pcm16_mono(wav_path)
    chunk_bytes = TARGET_SAMPLE_RATE * 2 * chunk_ms // 1000
    messages: list[dict] = []
    status = None
    timed_out = False

    async with websockets.connect(url, max_size=None) as websocket:
        first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
        status = first.get('status')
        for offset in range(0, len(pcm), chunk_bytes):
            await websocket.send(pcm[offset : offset + chunk_bytes])
            await asyncio.sleep(0)
        await websocket.send(json.dumps({'eof': 1}))

        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=receive_timeout,
                )
            except websockets.ConnectionClosed:
                break
            except asyncio.TimeoutError:
                timed_out = True
                break
            payload = json.loads(raw)
            if payload.get('type') == 'diarization':
                messages.append(payload)

    turns = [turn for message in messages for turn in message.get('turns', [])]
    speaker_ids = sorted(
        {
            str(turn['speaker_id'])
            for turn in turns
            if turn.get('speaker_id') is not None
        }
    )
    return {
        'ok': status == 'ready' and bool(messages) and not timed_out,
        'status': status,
        'input_duration_sec': round(duration_sec, 3),
        'message_count': len(messages),
        'turn_count': len(turns),
        'speaker_ids': speaker_ids,
        'processed_until_sec': max(
            (float(message.get('processed_until_sec', 0.0)) for message in messages),
            default=0.0,
        ),
        'timed_out': timed_out,
        'first_turns': turns[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Stream a WAV file to the live diarization WebSocket service'
    )
    parser.add_argument('wav_path', type=Path)
    parser.add_argument('--url', default='ws://127.0.0.1:2710')
    parser.add_argument('--chunk-ms', type=int, default=250)
    parser.add_argument('--receive-timeout', type=float, default=60.0)
    args = parser.parse_args()
    if args.chunk_ms <= 0:
        parser.error('--chunk-ms must be positive')
    if not args.wav_path.is_file():
        parser.error(f'WAV file not found: {args.wav_path}')
    result = asyncio.run(
        run_probe(
            args.wav_path,
            args.url,
            args.chunk_ms,
            args.receive_timeout,
        )
    )
    print(json.dumps(result, ensure_ascii=False))
    if not result['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
