"""Serialize live connections for one meeting during pause/resume handoff."""
from __future__ import annotations

import asyncio


class LiveSessionGate:
    def __init__(self) -> None:
        self._active: dict[int, asyncio.Event] = {}

    async def acquire(self, meeting_id: int, timeout: float = 30.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while meeting_id in self._active:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self._active[meeting_id].wait(), remaining)
            except asyncio.TimeoutError:
                return False
        # No await between checking and reserving: competing connections in
        # this event loop cannot both acquire the same meeting.
        self._active[meeting_id] = asyncio.Event()
        return True

    def release(self, meeting_id: int) -> None:
        finished = self._active.pop(meeting_id, None)
        if finished is not None:
            finished.set()

    def is_active(self, meeting_id: int) -> bool:
        return meeting_id in self._active
