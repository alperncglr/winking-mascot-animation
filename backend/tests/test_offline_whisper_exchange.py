import asyncio
import json

import websockets

from meeting_scribe.services.offline.adapters import VoskWebSocketTranscriber


def test_offline_exchange_reads_partial_results_during_upload(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.results = asyncio.Queue()
            self.first_result_read = asyncio.Event()
            self.audio_frames = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, value):
            if isinstance(value, bytes):
                self.audio_frames += 1
                if self.audio_frames == 1:
                    await self.results.put(json.dumps({'result': [
                        {'word': 'bir', 'start': 0.1, 'end': 0.3},
                    ]}))
                else:
                    # A sequential send-then-receive implementation deadlocks here.
                    await self.first_result_read.wait()
            elif json.loads(value).get('eof'):
                await self.results.put(json.dumps({'done': True}))

        async def recv(self):
            value = await self.results.get()
            self.first_result_read.set()
            return value

    async def run():
        socket = FakeSocket()
        monkeypatch.setattr(websockets, 'connect', lambda *_args, **_kwargs: socket)
        transcriber = VoskWebSocketTranscriber('ws://unused', chunk_bytes=32000)
        messages = await asyncio.wait_for(
            transcriber._exchange(b'\0' * 64000, 16000, None, 2.0), timeout=2)
        assert socket.audio_frames == 2
        assert messages[0]['result'][0]['word'] == 'bir'
        assert messages[-1]['done'] is True

    asyncio.run(run())
