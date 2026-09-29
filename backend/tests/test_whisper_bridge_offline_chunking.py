import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path


def test_offline_whisper_bridge_bounds_decode_windows_and_offsets_words(monkeypatch):
    bridge_dir = Path(__file__).resolve().parents[1] / 'services' / 'whisper_bridge'
    monkeypatch.syspath_prepend(str(bridge_dir))
    fake_whisper = types.ModuleType('faster_whisper')
    fake_whisper.WhisperModel = lambda *_args, **_kwargs: object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake_whisper)
    class FakeWebSocket:
        remote_address = ('127.0.0.1', 1234)

        def __init__(self):
            self.sent = []
            self.messages = [json.dumps({'config': {
                'meeting_id': 115, 'session_id': 'offline-115',
                'sample_rate': 16000, 'offline': True,
                'word_timestamps': True,
            }})]
            self.messages += [b'\0\0' * 16000] * 35
            self.messages.append(json.dumps({'eof': 1}))

        def __aiter__(self):
            self.iterator = iter(self.messages)
            return self

        async def __anext__(self):
            try:
                return next(self.iterator)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, message):
            self.sent.append(json.loads(message))

    async def run():
        spec = importlib.util.spec_from_file_location('bridge_offline_test', bridge_dir / 'whisper_bridge.py')
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        decoded_seconds = []

        def fake_transcribe(pcm, sample_rate, *_args, **_kwargs):
            decoded_seconds.append(len(pcm) / (sample_rate * 2))
            return {'text': 'word', 'result': [
                {'word': 'word', 'start': 0.1, 'end': 0.5, 'conf': 0.9},
            ], 'language': 'tr'}

        bridge.transcribe_pcm = fake_transcribe
        socket = FakeWebSocket()
        try:
            await bridge.handle(socket)
        finally:
            bridge.executor.shutdown(wait=True)
        assert decoded_seconds == [15, 15, 5]
        assert [round(item['result'][0]['start'], 1) for item in socket.sent] == [0.1, 15.1, 30.1]
        assert [bool(item.get('done')) for item in socket.sent] == [False, False, True]

    asyncio.run(run())
