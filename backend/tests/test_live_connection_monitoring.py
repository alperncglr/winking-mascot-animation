import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

from meeting_scribe.server.live_connection import receive_audio, run_input_until_closed
from meeting_scribe.server.monitoring import LiveMonitor


class FakeWebSocket:
    def __init__(self, state=WebSocketState.CONNECTED, error=None):
        self.application_state = state
        self.client_state = state
        self.error = error

    async def receive_bytes(self):
        if self.error:
            self.client_state = WebSocketState.DISCONNECTED
            raise self.error
        return b'\x00\x00'


def test_receive_audio_on_closed_websocket_is_a_normal_disconnect():
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(receive_audio(FakeWebSocket(WebSocketState.DISCONNECTED)))
    with pytest.raises(WebSocketDisconnect):
        asyncio.run(receive_audio(FakeWebSocket(error=RuntimeError('WebSocket is not connected'))))


def test_downstream_failure_stops_audio_pump():
    async def run():
        stopped = asyncio.Event()

        async def pump():
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def failure():
            await asyncio.sleep(0)
            raise ValueError('receiver failed')

        receiver = asyncio.create_task(failure())
        with pytest.raises(ValueError, match='receiver failed'):
            await run_input_until_closed(pump, [receiver], asyncio.Event())
        assert stopped.is_set()

    asyncio.run(run())


def test_monitor_distinguishes_connection_from_audio_receipt():
    monitor = LiveMonitor()
    session_id = monitor.start(115, 'mixed')
    row = monitor.snapshot()['sessions'][0]
    assert row['browser_connection'] == 'connected'
    assert row['audio_stream'] == 'waiting'
    monitor.audio(session_id, 2.5)
    row = monitor.snapshot()['sessions'][0]
    assert row['audio_stream'] == 'receiving'
    monitor.close(session_id)
    row = monitor.snapshot()['recent_sessions'][0]
    assert row['browser_connection'] == 'closed'
    assert row['audio_stream'] == 'disconnected'
