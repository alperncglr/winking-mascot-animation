from __future__ import annotations

from collections import defaultdict

from meeting_scribe.domain.models import Segment, SegmentStatus
from meeting_scribe.services.offline.adapters import SpeakerTurn, Word


def dominant_speaker(word: Word, turns: list[SpeakerTurn]) -> SpeakerTurn | None:
    best: tuple[float, SpeakerTurn] | None = None
    for turn in turns:
        overlap = max(0.0, min(word.end_sec, turn.end_sec) - max(word.start_sec, turn.start_sec))
        if overlap <= 0:
            continue
        if best is None or overlap > best[0]:
            best = (overlap, turn)
    return best[1] if best else None


def align_words_to_segments(meeting_id: int, words: list[Word], turns: list[SpeakerTurn]) -> list[Segment]:
    grouped: list[tuple[str | None, bool, list[Word]]] = []
    for word in words:
        turn = dominant_speaker(word, turns)
        speaker_id = turn.speaker_id if turn else None
        overlap = bool(turn.overlap) if turn else False
        # A speaker can resume after a long pause; that is a new utterance and
        # must not inflate the segment duration (or speaker talk time).
        contiguous = grouped and word.start_sec - grouped[-1][2][-1].end_sec <= 1.0
        if contiguous and grouped[-1][0] == speaker_id and grouped[-1][1] == overlap:
            grouped[-1][2].append(word)
        else:
            grouped.append((speaker_id, overlap, [word]))

    segments: list[Segment] = []
    for index, (speaker_id, overlap, segment_words) in enumerate(grouped, start=1):
        text = " ".join(word.text for word in segment_words).strip()
        confidences = [w.confidence for w in segment_words if w.confidence is not None]
        confidence = sum(confidences) / len(confidences) if confidences else None
        segments.append(
            Segment(
                meeting_id=meeting_id,
                segment_id=f"offline_{index:06d}",
                revision=1,
                status=SegmentStatus.FINAL,
                start_sec=segment_words[0].start_sec,
                end_sec=segment_words[-1].end_sec,
                diarization_speaker_id=speaker_id,
                participant_id=None,
                transcript_confidence=confidence,
                overlap=overlap,
                text=text,
            )
        )
    return segments


def speaker_talk_time(segments: list[Segment]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for segment in segments:
        if segment.diarization_speaker_id:
            totals[segment.diarization_speaker_id] += max(0.0, segment.end_sec - segment.start_sec)
    return dict(totals)
