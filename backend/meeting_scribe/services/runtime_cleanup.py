from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meeting_scribe.config import Settings
from meeting_scribe.domain.models import MeetingStatus
from meeting_scribe.paths import MeetingPaths, assert_inside
from meeting_scribe.storage.repositories import MeetingRepository


class RuntimePurger:
    def __init__(self, settings: Settings, meeting_repo: MeetingRepository) -> None:
        self.settings = settings
        self.paths = MeetingPaths(settings)
        self.meeting_repo = meeting_repo

    def purge_after_summary(self, meeting_id: int) -> None:
        meeting = self.meeting_repo.get_meeting(meeting_id)
        if not meeting:
            raise ValueError("Meeting not found")
        if meeting["status"] != MeetingStatus.SUMMARIZED:
            raise ValueError("Normal finish requires summarized meeting")
        self._purge(meeting_id, MeetingStatus.FINISHED)

    def force_close(self, meeting_id: int) -> None:
        self._purge(meeting_id, MeetingStatus.FINISHED)

    def purge_abandoned(self) -> list[int]:
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=self.settings.runtime_cleanup.abandoned_meeting_timeout_hours
        )
        purged: list[int] = []
        for meeting in self.meeting_repo.abandoned_candidates(cutoff.isoformat()):
            meeting_id = int(meeting['id'])
            self._purge(meeting_id, MeetingStatus.ABANDONED_CLEANED)
            purged.append(meeting_id)
        return purged

    def _purge(self, meeting_id: int, status: MeetingStatus) -> None:
        runtime_dir = self.paths.runtime_dir(meeting_id)
        assert_inside(runtime_dir, self.settings.paths.runtime_root)
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        if not runtime_dir.exists():
            self.meeting_repo.mark_runtime_purged(meeting_id, status)
