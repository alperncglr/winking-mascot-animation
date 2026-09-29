"""Coordinate the input pump with output failures without orphaning receives."""
import asyncio
from starlette.websockets import WebSocketDisconnect, WebSocketState


async def receive_audio(websocket):
    if websocket.application_state != WebSocketState.CONNECTED:
        raise WebSocketDisconnect(code=1006)
    try:
        return await websocket.receive_bytes()
    except RuntimeError:
        # Do not mask unrelated programming errors.
        if websocket.application_state == WebSocketState.DISCONNECTED or websocket.client_state == WebSocketState.DISCONNECTED:
            raise WebSocketDisconnect(code=1006)
        raise


async def run_input_until_closed(pump, downstream, disconnected):
    input_task = asyncio.create_task(pump())
    stop_task = asyncio.create_task(disconnected.wait())
    try:
        done, _ = await asyncio.wait([input_task, stop_task, *downstream], return_when=asyncio.FIRST_COMPLETED)
        if input_task in done:
            await input_task
        else:
            for task in downstream:
                if task in done:
                    await task  # Surface failures to the session owner.
    finally:
        input_task.cancel()
        stop_task.cancel()
        await asyncio.gather(input_task, stop_task, return_exceptions=True)
