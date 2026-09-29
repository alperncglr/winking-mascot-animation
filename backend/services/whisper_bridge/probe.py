from __future__ import annotations

import argparse
import asyncio
import json
import wave
from pathlib import Path

import websockets


def load_pcm16_mono(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), 'rb') as wav_file:
        if wav_file.getsampwidth() != 2 or wav_file.getnchannels() != 1:
            raise ValueError('Probe requires a 16-bit mono PCM WAV file')
        return wav_file.readframes(wav_file.getnframes()), wav_file.getframerate()


async def probe(
    url: str,
    audio_path: Path,
    legacy: bool = False,
    live_metadata: bool = False,
    chunk_seconds: float | None = None,
    context_seconds: float | None = None,
    max_seconds: float = 9.0,
) -> None:
    pcm, sample_rate = load_pcm16_mono(audio_path)
    async with websockets.connect(url, proxy=None, max_size=None) as websocket:
        config = {'sample_rate': sample_rate, 'retry_low_confidence': True}
        if chunk_seconds is not None:
            config['chunk_seconds'] = chunk_seconds
        if live_metadata:
            config.update(
                {
                    'live_metadata': True,
                    'word_timestamps': True,
                    'chunk_seconds': chunk_seconds or 5.0,
                    'context_seconds': context_seconds or chunk_seconds or 5.0,
                }
            )
        elif not legacy:
            config.update({'offline': True, 'word_timestamps': True})
        await websocket.send(json.dumps({'config': config}))
        if legacy or live_metadata:
            pcm = pcm[: int(sample_rate * 2 * max_seconds)]
        for offset in range(0, len(pcm), 32000):
            await websocket.send(pcm[offset : offset + 32000])
        await websocket.send(json.dumps({'eof': 1}))
        if legacy:
            messages = []
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                except asyncio.TimeoutError:
                    break
                payload = json.loads(raw)
                if set(payload) != {'text'}:
                    raise RuntimeError(
                        f'Legacy response fields changed: {sorted(payload)}'
                    )
                if payload.get('text'):
                    messages.append(payload['text'])
            print(
                json.dumps(
                    {
                        'ok': True,
                        'legacy': True,
                        'chunk_seconds': chunk_seconds,
                        'message_count': len(messages),
                        'text_chars': sum(len(text) for text in messages),
                        'response_fields': ['text'],
                    },
                    ensure_ascii=False,
                )
            )
            return
        if live_metadata:
            messages = []
            while True:
                payload = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=300)
                )
                required = {
                    'type',
                    'sequence',
                    'segment_id',
                    'revision',
                    'status',
                    'start_sec',
                    'end_sec',
                    'text',
                    'done',
                    'result',
                }
                if not required.issubset(payload):
                    raise RuntimeError(
                        f'Live metadata fields missing: {sorted(required - set(payload))}'
                    )
                messages.append(payload)
                if payload.get('done'):
                    break
            transcript_messages = [
                payload for payload in messages if payload.get('text')
            ]
            statuses = {
                status: sum(
                    payload.get('status') == status
                    for payload in transcript_messages
                )
                for status in ('provisional', 'final')
            }
            if transcript_messages and not statuses['provisional']:
                raise RuntimeError('Live stream produced no provisional update')
            print(
                json.dumps(
                    {
                        'ok': True,
                        'live_metadata': True,
                        'message_count': len(messages),
                        'statuses': statuses,
                        'text_chars': sum(
                            len(str(payload.get('text') or ''))
                            for payload in messages
                        ),
                        'word_count': sum(
                            len(payload.get('result') or [])
                            for payload in messages
                        ),
                        'windows': [
                            {
                                'sequence': payload['sequence'],
                                'segment_id': payload['segment_id'],
                                'revision': payload['revision'],
                                'status': payload['status'],
                                'start_sec': payload['start_sec'],
                                'end_sec': payload['end_sec'],
                                'done': payload['done'],
                            }
                            for payload in messages
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return
        payload = json.loads(await asyncio.wait_for(websocket.recv(), timeout=300))

    words = payload.get('result') or []
    if payload.get('text') and not words:
        raise RuntimeError('Bridge returned text but no word timestamps')
    if not payload.get('done'):
        raise RuntimeError('Bridge did not mark the offline response done')
    preview = [
        {
            'word': item.get('word'),
            'start': item.get('start'),
            'end': item.get('end'),
        }
        for item in words[:5]
    ]
    print(
        json.dumps(
            {
                'ok': True,
                'text_chars': len(payload.get('text') or ''),
                'word_count': len(words),
                'first_words': preview,
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('audio_path', type=Path)
    parser.add_argument('--url', default='ws://127.0.0.1:2702')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--legacy', action='store_true')
    mode.add_argument('--live-metadata', action='store_true')
    parser.add_argument('--chunk-seconds', type=float)
    parser.add_argument('--context-seconds', type=float)
    parser.add_argument('--max-seconds', type=float, default=9.0)
    args = parser.parse_args()
    asyncio.run(
        probe(
            args.url,
            args.audio_path,
            legacy=args.legacy,
            live_metadata=args.live_metadata,
            chunk_seconds=args.chunk_seconds,
            context_seconds=args.context_seconds,
            max_seconds=args.max_seconds,
        )
    )


if __name__ == '__main__':
    main()
