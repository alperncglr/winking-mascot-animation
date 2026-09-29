from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
class StrEnum(str, Enum):
    pass


class MeetingStatus(StrEnum):
    CREATED = "created"
    RECORDING = "recording"
    RECORDED = "recorded"
    REFINING = "refining"
    REFINED = "refined"
    SUMMARIZING = "summarizing"
    SUMMARY_PENDING = "summary_pending"
    SUMMARY_FAILED = "summary_failed"
    SUMMARIZED = "summarized"
    FINISHED = "finished"
    ABANDONED_CLEANED = "abandoned_cleaned"
    REFINE_FAILED = "refine_failed"
    RECORDING_FAILED = "recording_failed"


class SegmentStatus(StrEnum):
    PROVISIONAL = "provisional"
    FINAL = "final"


@dataclass(frozen=True)
class Segment:
    meeting_id: int
    segment_id: str
    revision: int
    status: SegmentStatus
    start_sec: float
    end_sec: float
    diarization_speaker_id: str | None
    participant_id: int | None
    transcript_confidence: float | None
    overlap: bool
    text: str


@dataclass(frozen=True)
class SummaryResult:
    summary_md: str
    discussed_topics: list[str]
    decisions: list[str]
    actions: list[dict[str, str]]
    open_questions: list[str]
