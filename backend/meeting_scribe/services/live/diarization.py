from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LiveSpeakerTurn:
    speaker_id: str
    start_sec: float
    end_sec: float
    overlap: bool = False


@dataclass(frozen=True)
class LiveTranscriptGroup:
    speaker_id: str | None
    start_sec: float
    end_sec: float
    text: str
    overlap: bool = False


class LiveSpeakerTracker:
    def __init__(
        self,
        history_seconds: float = 120.0,
        switch_min_evidence_seconds: float = 0.18,
        switch_margin_seconds: float = 0.04,
        assignment_padding_seconds: float = 0.06,
    ) -> None:
        self.history_seconds = history_seconds
        self.switch_min_evidence_seconds = switch_min_evidence_seconds
        self.switch_margin_seconds = switch_margin_seconds
        self.assignment_padding_seconds = assignment_padding_seconds
        self._turns: list[LiveSpeakerTurn] = []
        self._processed_until = 0.0
        self._last_confirmed_speaker: str | None = None
        self._condition = asyncio.Condition()

    @property
    def processed_until(self) -> float:
        return self._processed_until

    @property
    def last_confirmed_speaker(self) -> str | None:
        return self._last_confirmed_speaker

    async def update(self, payload: dict[str, Any]) -> None:
        turns: list[LiveSpeakerTurn] = []
        for item in payload.get('turns') or []:
            speaker_id = str(item.get('speaker_id') or '').strip()
            if not speaker_id:
                continue
            start_sec = float(item.get('start_sec', 0.0))
            end_sec = float(item.get('end_sec', start_sec))
            if end_sec <= start_sec:
                continue
            turns.append(
                LiveSpeakerTurn(
                    speaker_id=speaker_id,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    overlap=bool(item.get('overlap', False)),
                )
            )

        processed_until = float(payload.get('processed_until_sec', 0.0))
        async with self._condition:
            self._turns.extend(turns)
            self._processed_until = max(self._processed_until, processed_until)
            cutoff = max(0.0, self._processed_until - self.history_seconds)
            self._turns = [turn for turn in self._turns if turn.end_sec >= cutoff]
            self._condition.notify_all()

    def _speaker_scores(
        self,
        start_sec: float,
        end_sec: float,
        *,
        padding_seconds: float = 0.0,
    ) -> dict[str, float]:
        scores: dict[str, float] = {}
        padded_start = max(0.0, start_sec - padding_seconds)
        padded_end = end_sec + padding_seconds
        for turn in self._turns:
            overlap = max(
                0.0,
                min(padded_end, turn.end_sec)
                - max(padded_start, turn.start_sec),
            )
            if overlap > 0:
                scores[turn.speaker_id] = scores.get(turn.speaker_id, 0.0) + overlap
        return scores

    def dominant_speaker(self, start_sec: float, end_sec: float) -> str | None:
        scores = self._speaker_scores(start_sec, end_sec)
        if scores:
            return max(scores, key=scores.get)

        previous = [turn for turn in self._turns if turn.end_sec <= end_sec]
        if previous:
            return max(previous, key=lambda turn: turn.end_sec).speaker_id
        return None

    def provisional_speaker(self, start_sec: float, end_sec: float) -> str | None:
        candidate = self.dominant_speaker(start_sec, end_sec)
        current = self._last_confirmed_speaker
        if candidate is None or current is None or candidate == current:
            return candidate or current
        scores = self._speaker_scores(start_sec, end_sec)
        if (
            scores.get(candidate, 0.0)
            >= scores.get(current, 0.0) + self.switch_min_evidence_seconds
        ):
            return candidate
        return current

    def has_overlap(self, start_sec: float, end_sec: float) -> bool:
        return any(
            turn.overlap
            and min(end_sec, turn.end_sec) > max(start_sec, turn.start_sec)
            for turn in self._turns
        )

    def group_words(
        self,
        words: list[dict[str, Any]],
        fallback_speaker_id: str | None = None,
    ) -> list[LiveTranscriptGroup]:
        prepared: list[tuple[str, float, float, bool, dict[str, float]]] = []
        for item in words:
            text = str(item.get('word') or '').strip()
            if not text:
                continue
            start_sec = float(item.get('start', 0.0))
            end_sec = float(item.get('end', start_sec))
            if end_sec < start_sec:
                end_sec = start_sec
            prepared.append(
                (
                    text,
                    start_sec,
                    end_sec,
                    self.has_overlap(start_sec, end_sec),
                    self._speaker_scores(
                        start_sec,
                        end_sec,
                        padding_seconds=self.assignment_padding_seconds,
                    ),
                )
            )

        if not prepared:
            return []

        current = self._last_confirmed_speaker
        if current is None:
            current = next(
                (
                    max(scores, key=scores.get)
                    for _, _, _, _, scores in prepared
                    if scores
                ),
                fallback_speaker_id,
            )

        assignments: list[str | None] = []
        pending_speaker: str | None = None
        pending_indexes: list[int] = []
        pending_evidence = 0.0

        for _, start_sec, end_sec, overlap, scores in prepared:
            best = max(scores, key=scores.get) if scores else None
            if current is None:
                current = best or fallback_speaker_id

            current_score = scores.get(current, 0.0) if current else 0.0
            best_score = scores.get(best, 0.0) if best else 0.0
            competing = (
                best is not None
                and best != current
                and best_score >= current_score + self.switch_margin_seconds
            )
            if overlap and current_score > 0:
                competing = False

            assignments.append(current or best or fallback_speaker_id)
            if not competing:
                pending_speaker = None
                pending_indexes = []
                pending_evidence = 0.0
                continue

            if pending_speaker != best:
                pending_speaker = best
                pending_indexes = []
                pending_evidence = 0.0
            pending_indexes.append(len(assignments) - 1)
            pending_evidence += max(0.0, best_score - current_score)
            if pending_evidence < self.switch_min_evidence_seconds:
                continue

            current = best
            for index in pending_indexes:
                assignments[index] = current
            pending_speaker = None
            pending_indexes = []
            pending_evidence = 0.0

        groups: list[LiveTranscriptGroup] = []
        for (text, start_sec, end_sec, overlap, _), speaker_id in zip(
            prepared,
            assignments,
        ):
            if groups and groups[-1].speaker_id == speaker_id:
                previous = groups[-1]
                groups[-1] = LiveTranscriptGroup(
                    speaker_id=speaker_id,
                    start_sec=previous.start_sec,
                    end_sec=end_sec,
                    text=f'{previous.text} {text}'.strip(),
                    overlap=previous.overlap or overlap,
                )
            else:
                groups.append(
                    LiveTranscriptGroup(
                        speaker_id=speaker_id,
                        start_sec=start_sec,
                        end_sec=end_sec,
                        text=text,
                        overlap=overlap,
                    )
                )
        if groups and groups[-1].speaker_id:
            self._last_confirmed_speaker = groups[-1].speaker_id
        return groups

    async def wait_until_processed(
        self,
        target_sec: float,
        timeout_seconds: float,
    ) -> None:
        if self._processed_until >= target_sec:
            return

        async def wait_until_ready() -> None:
            async with self._condition:
                await self._condition.wait_for(
                    lambda: self._processed_until >= target_sec
                )

        try:
            await asyncio.wait_for(wait_until_ready(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return

    async def wait_for_speaker(
        self,
        start_sec: float,
        end_sec: float,
        timeout_seconds: float,
    ) -> str | None:
        ready_at = max(start_sec, end_sec - 1.0)

        await self.wait_until_processed(ready_at, timeout_seconds)
        return self.dominant_speaker(start_sec, end_sec)
