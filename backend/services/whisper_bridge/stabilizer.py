from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOKEN_EDGE = re.compile(r'(^[^\w]+|[^\w]+$)', flags=re.UNICODE)


def _normalise(value: Any) -> str:
    return _TOKEN_EDGE.sub('', str(value or '').casefold())


def _common_prefix(previous: list[dict], current: list[dict]) -> int:
    length = 0
    for old, new in zip(previous, current):
        old_token = _normalise(old.get('word'))
        new_token = _normalise(new.get('word'))
        if not old_token or old_token != new_token:
            break
        length += 1
    return length


def _prefer_complete(previous: list[dict], current: list[dict]) -> list[dict]:
    """Do not replace a visible hypothesis with a decode that lost coverage.

    Rolling-window/VAD decoding can occasionally return only the beginning of
    an utterance at the silence flush.  Treating that shorter decode as final
    makes the UI replace a good provisional sentence with a truncated one.
    """
    if not previous:
        return list(current)
    if not current:
        return list(previous)

    previous_start = float(previous[0].get('start', 0.0))
    previous_end = float(previous[-1].get('end', previous_start))
    current_start = float(current[0].get('start', 0.0))
    current_end = float(current[-1].get('end', current_start))
    tolerance = 0.25

    lost_prefix = current_start > previous_start + tolerance
    lost_suffix = current_end < previous_end - tolerance
    # A later rolling window naturally loses words at the beginning while it
    # gains newer words at the end. That is forward progress, not a truncated
    # decode. Keep the previous hypothesis only when coverage actually moves
    # backwards or shrinks.
    if lost_suffix or (lost_prefix and current_end <= previous_end + tolerance):
        return list(previous)
    return list(current)


@dataclass(frozen=True)
class StableTranscriptEvent:
    segment_id: str
    revision: int
    status: str
    start_sec: float
    end_sec: float
    text: str
    words: list[dict]

    def as_payload(self, *, sequence: int, done: bool = False) -> dict:
        return {
            'type': 'transcript', 'sequence': sequence,
            'segment_id': self.segment_id, 'revision': self.revision,
            'status': self.status, 'start_sec': self.start_sec,
            'end_sec': self.end_sec, 'text': self.text,
            'result': self.words, 'done': done,
        }

class LiveHypothesisStabilizer:
    '''LocalAgreement-2 state without model/GPU allocation.'''

    def __init__(self, holdback_seconds: float = 0.6) -> None:
        self.holdback_seconds = max(0.0, holdback_seconds)
        self.previous_words: list[dict] = []
        self.committed_until_sec = 0.0
        self._segment_index = 1
        self._revision = 0

    @property
    def has_pending(self) -> bool:
        return bool(self.previous_words)

    def _segment_id(self) -> str:
        return f'asr_{self._segment_index:06d}'

    def _new_segment(self) -> None:
        self._segment_index += 1
        self._revision = 0

    def _eligible(self, words: list[dict]) -> list[dict]:
        result = []
        for item in words:
            start = float(item.get('start', 0.0))
            end = float(item.get('end', start))
            if (start + end) / 2 > self.committed_until_sec + 0.02:
                result.append(dict(item))
        return result

    @staticmethod
    def _event(segment_id: str, revision: int, status: str, words: list[dict]) -> StableTranscriptEvent:
        text = ' '.join(str(item.get('word') or '').strip() for item in words).strip()
        start = float(words[0].get('start', 0.0)) if words else 0.0
        end = float(words[-1].get('end', start)) if words else start
        return StableTranscriptEvent(segment_id, revision, status, start, end, text, words)

    def update(self, words: list[dict], *, audio_end_sec: float, force_final: bool = False) -> list[StableTranscriptEvent]:
        current = self._eligible(words)
        events: list[StableTranscriptEvent] = []

        # Once the rolling context window advances, its first words disappear
        # by design. Commit only the old words that are now outside the new
        # window, then continue the visible hypothesis under a new segment id.
        # Without this rollover, the anti-truncation guard keeps the old
        # hypothesis forever during uninterrupted speech and the UI appears to
        # freeze until the microphone is toggled or silence forces a reset.
        if not force_final and self.previous_words and current:
            previous_start = float(self.previous_words[0].get('start', 0.0))
            previous_end = float(
                self.previous_words[-1].get('end', previous_start)
            )
            current_start = float(current[0].get('start', 0.0))
            current_end = float(current[-1].get('end', current_start))
            tolerance = 0.25
            rolling_forward = (
                current_start > previous_start + tolerance
                and current_end > previous_end + tolerance
            )
            if rolling_forward:
                expired = [
                    item
                    for item in self.previous_words
                    if float(item.get('end', item.get('start', 0.0)))
                    <= current_start + 0.05
                ]
                if expired:
                    self._revision += 1
                    events.append(
                        self._event(
                            self._segment_id(), self._revision, 'final', expired
                        )
                    )
                    self.committed_until_sec = max(
                        self.committed_until_sec,
                        float(expired[-1].get('end', 0.0)),
                    )
                    self._new_segment()
                    current = self._eligible(current)
                    self.previous_words = current
                    if current:
                        self._revision += 1
                        events.append(
                            self._event(
                                self._segment_id(),
                                self._revision,
                                'provisional',
                                current,
                            )
                        )
                    return events

        if force_final:
            current = _prefer_complete(self.previous_words, current)
            stable_count = len(current)
        else:
            stable_count = _common_prefix(self.previous_words, current)
            cutoff = audio_end_sec - self.holdback_seconds
            while stable_count and float(current[stable_count - 1].get('end', 0.0)) > cutoff:
                stable_count -= 1

        stable = current[:stable_count]
        remaining = current[stable_count:]
        if not force_final:
            previous_remaining = self.previous_words[stable_count:]
            remaining = _prefer_complete(previous_remaining, remaining)
        if stable:
            self._revision += 1
            events.append(self._event(self._segment_id(), self._revision, 'final', stable))
            self.committed_until_sec = max(
                self.committed_until_sec,
                float(stable[-1].get('end', 0.0)),
            )
            self._new_segment()
        self.previous_words = remaining
        if remaining and not force_final:
            self._revision += 1
            events.append(self._event(self._segment_id(), self._revision, 'provisional', remaining))
        if force_final:
            self.previous_words = []
        return events

    def reset_utterance(self) -> None:
        self.previous_words = []
        self._new_segment()
