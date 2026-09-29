import asyncio

from meeting_scribe.server.live_session_gate import LiveSessionGate


def test_resume_waits_for_previous_session_to_finish() -> None:
    async def exercise() -> None:
        gate = LiveSessionGate()
        assert await gate.acquire(121)
        assert gate.is_active(121)
        resumed = asyncio.create_task(gate.acquire(121, timeout=1))
        await asyncio.sleep(0)
        assert not resumed.done()
        gate.release(121)
        assert not gate.is_active(121)
        assert await resumed
        gate.release(121)

    asyncio.run(exercise())


def test_other_meetings_are_not_blocked() -> None:
    async def exercise() -> None:
        gate = LiveSessionGate()
        assert await gate.acquire(121)
        assert await gate.acquire(122)
        gate.release(121)
        gate.release(122)

    asyncio.run(exercise())
