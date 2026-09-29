from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from meeting_scribe.services.offline.adapters import SpeakerTurn


def load_live_speaker_turns(path: Path) -> list[SpeakerTurn]:
    if not path.exists():
        return []
    turns: list[SpeakerTurn] = []
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in payload.get('turns') or []:
                speaker_id = str(item.get('speaker_id') or '').strip()
                start_sec = float(item.get('start_sec', 0.0))
                end_sec = float(item.get('end_sec', start_sec))
                if speaker_id and end_sec > start_sec:
                    turns.append(
                        SpeakerTurn(
                            speaker_id=speaker_id,
                            start_sec=start_sec,
                            end_sec=end_sec,
                            overlap=bool(item.get('overlap', False)),
                        )
                    )
    return turns


def load_live_speaker_labels(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    labels: dict[str, str] = {}
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            speaker_id = str(
                payload.get('diarization_speaker_id') or ''
            ).strip()
            label = str(payload.get('speaker_label') or '').strip()
            if speaker_id and label.startswith('Kullanıcı '):
                labels.setdefault(speaker_id, label)
    return labels


def match_offline_speakers_to_live_labels(
    offline_turns: Iterable[SpeakerTurn],
    live_turns: Iterable[SpeakerTurn],
    live_labels: dict[str, str],
) -> dict[str, str]:
    offline_intervals = _merged_intervals_by_speaker(offline_turns)
    live_intervals = _merged_intervals_by_speaker(live_turns)
    scores: dict[tuple[str, str], float] = {}

    for offline_speaker, offline_ranges in offline_intervals.items():
        for live_speaker, live_ranges in live_intervals.items():
            if live_speaker not in live_labels:
                continue
            overlap = _total_overlap(offline_ranges, live_ranges)
            if overlap > 0:
                scores[(offline_speaker, live_speaker)] = overlap

    pairs = _maximum_weight_pairs(
        sorted(offline_intervals),
        sorted(speaker for speaker in live_intervals if speaker in live_labels),
        scores,
    )
    return {
        offline_speaker: live_labels[live_speaker]
        for offline_speaker, live_speaker in pairs
    }


def _maximum_weight_pairs(
    offline_speakers: list[str],
    live_speakers: list[str],
    scores: dict[tuple[str, str], float],
) -> tuple[tuple[str, str], ...]:
    if len(live_speakers) > 16:
        return _greedy_pairs(scores)

    @lru_cache(maxsize=None)
    def solve(
        offline_index: int,
        used_live_mask: int,
    ) -> tuple[float, tuple[tuple[str, str], ...]]:
        if offline_index >= len(offline_speakers):
            return 0.0, ()

        offline_speaker = offline_speakers[offline_index]
        best_score, best_pairs = solve(offline_index + 1, used_live_mask)
        for live_index, live_speaker in enumerate(live_speakers):
            if used_live_mask & (1 << live_index):
                continue
            overlap = scores.get((offline_speaker, live_speaker), 0.0)
            if overlap <= 0:
                continue
            tail_score, tail_pairs = solve(
                offline_index + 1,
                used_live_mask | (1 << live_index),
            )
            candidate_score = overlap + tail_score
            candidate_pairs = ((offline_speaker, live_speaker),) + tail_pairs
            if (
                candidate_score > best_score
                or (
                    candidate_score == best_score
                    and candidate_pairs < best_pairs
                )
            ):
                best_score = candidate_score
                best_pairs = candidate_pairs
        return best_score, best_pairs

    return solve(0, 0)[1]


def _greedy_pairs(
    scores: dict[tuple[str, str], float],
) -> tuple[tuple[str, str], ...]:
    matched_offline: set[str] = set()
    matched_live: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for (offline_speaker, live_speaker), _score in sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    ):
        if offline_speaker in matched_offline or live_speaker in matched_live:
            continue
        pairs.append((offline_speaker, live_speaker))
        matched_offline.add(offline_speaker)
        matched_live.add(live_speaker)
    return tuple(sorted(pairs))


def _merged_intervals_by_speaker(
    turns: Iterable[SpeakerTurn],
) -> dict[str, list[tuple[float, float]]]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for turn in turns:
        if turn.end_sec > turn.start_sec:
            grouped[turn.speaker_id].append((turn.start_sec, turn.end_sec))

    merged: dict[str, list[tuple[float, float]]] = {}
    for speaker_id, intervals in grouped.items():
        compact: list[list[float]] = []
        for start_sec, end_sec in sorted(intervals):
            if compact and start_sec <= compact[-1][1]:
                compact[-1][1] = max(compact[-1][1], end_sec)
            else:
                compact.append([start_sec, end_sec])
        merged[speaker_id] = [(start, end) for start, end in compact]
    return merged


def _total_overlap(
    left: list[tuple[float, float]],
    right: list[tuple[float, float]],
) -> float:
    total = 0.0
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        total += max(0.0, min(left_end, right_end) - max(left_start, right_start))
        if left_end <= right_end:
            left_index += 1
        else:
            right_index += 1
    return total
