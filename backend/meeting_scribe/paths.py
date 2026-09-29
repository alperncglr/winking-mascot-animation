from __future__ import annotations

from pathlib import Path

from meeting_scribe.config import Settings


class MeetingPaths:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def runtime_dir(self, meeting_id: int) -> Path:
        return self.settings.paths.runtime_root / str(meeting_id)

    def audio_dir(self, meeting_id: int) -> Path:
        return self.runtime_dir(meeting_id) / "audio"

    def separation_dir(self, meeting_id: int) -> Path:
        return self.runtime_dir(meeting_id) / "separation"

    def manifest_path(self, meeting_id: int) -> Path:
        return self.audio_dir(meeting_id) / "manifest.jsonl"

    def live_transcript_path(self, meeting_id: int) -> Path:
        return self.runtime_dir(meeting_id) / "live_transcript.jsonl"

    def live_diarization_path(self, meeting_id: int) -> Path:
        return self.runtime_dir(meeting_id) / 'live_diarization.jsonl'

    def archive_dir(self, meeting_id: int) -> Path:
        return self.settings.paths.archive_root / str(meeting_id)

    def final_transcript_path(self, meeting_id: int) -> Path:
        return self.archive_dir(meeting_id) / "final_transcript.md"

    def summary_path(self, meeting_id: int) -> Path:
        return self.archive_dir(meeting_id) / "summary.md"


def ensure_base_dirs(settings: Settings) -> None:
    settings.paths.database.parent.mkdir(parents=True, exist_ok=True)
    settings.paths.runtime_root.mkdir(parents=True, exist_ok=True)
    settings.paths.archive_root.mkdir(parents=True, exist_ok=True)


def assert_inside(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if parent_resolved != child_resolved and parent_resolved not in child_resolved.parents:
        raise ValueError(f"{child_resolved} is outside {parent_resolved}")
