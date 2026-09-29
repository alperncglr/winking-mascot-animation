from __future__ import annotations

from dataclasses import dataclass

from meeting_scribe.config import Settings, load_settings
from meeting_scribe.paths import MeetingPaths, ensure_base_dirs
from meeting_scribe.storage.db import Database
from meeting_scribe.storage.repositories import MeetingRepository, SegmentRepository, SummaryRepository


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    db: Database
    meetings: MeetingRepository
    segments: SegmentRepository
    summaries: SummaryRepository
    paths: MeetingPaths


def build_container(config_path: str = "config.yaml") -> AppContainer:
    settings = load_settings(config_path)
    ensure_base_dirs(settings)
    db = Database(settings.paths.database)
    db.initialize()
    return AppContainer(
        settings=settings,
        db=db,
        meetings=MeetingRepository(db),
        segments=SegmentRepository(db),
        summaries=SummaryRepository(db),
        paths=MeetingPaths(settings),
    )
