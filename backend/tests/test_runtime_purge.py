from __future__ import annotations

import unittest
from pathlib import Path

from meeting_scribe.config import (
    AppConfig,
    AuthConfig,
    CaptureConfig,
    LiveConfig,
    LlmConfig,
    OfflineConfig,
    PathConfig,
    RuntimeCleanupConfig,
    Settings,
)
from meeting_scribe.domain.models import MeetingStatus
from meeting_scribe.services.runtime_cleanup import RuntimePurger
from meeting_scribe.storage.db import Database
from meeting_scribe.storage.repositories import MeetingRepository
from tests.helpers import workspace_tempdir


def build_settings(root: Path) -> Settings:
    return Settings(
        app=AppConfig("test", "127.0.0.1", 8000),
        paths=PathConfig(root / "db.sqlite3", root / "runtime", root / "archive"),
        auth=AuthConfig('Dev', 'dev@example.local', 'x-name', 'x-id', 'API_KEY'),
        capture=CaptureConfig("Jabra", 16000, 1, 30),
        offline=OfflineConfig(
            'remote_openai', 'http://10.0.111.32:2700/v1', 'ws://10.0.111.32:2700',
            300, 8, 32000, 'large-v3', 'float16',
            "placeholder", "pyannote/model", "HF_TOKEN", 1,
        ),
        live=LiveConfig(
            'placeholder', 'large-v3-turbo', 5.0, 'placeholder', 'ws://127.0.0.1:2710',
            7.0, 16000, 10.0, 300, 1.2, 0.0,
        ),
        llm=LlmConfig("http://localhost:8080/v1", "model", "KEY", 5, 1000),
        runtime_cleanup=RuntimeCleanupConfig(24, 60),
    )


class RuntimePurgeTests(unittest.TestCase):
    def test_finish_purges_only_runtime(self) -> None:
        with workspace_tempdir() as tmp:
            settings = build_settings(tmp)
            db = Database(settings.paths.database)
            db.initialize()
            meetings = MeetingRepository(db)
            meeting_id = meetings.create_meeting("m", "Dev", "dev")
            meetings.update_status(meeting_id, MeetingStatus.SUMMARIZED)
            runtime_dir = settings.paths.runtime_root / str(meeting_id)
            archive_dir = settings.paths.archive_root / str(meeting_id)
            runtime_dir.mkdir(parents=True)
            archive_dir.mkdir(parents=True)
            (runtime_dir / "temporary.wav").write_text("x", encoding="utf-8")
            (archive_dir / "final_transcript.md").write_text("keep", encoding="utf-8")

            RuntimePurger(settings, meetings).purge_after_summary(meeting_id)

            self.assertFalse(runtime_dir.exists())
            self.assertTrue((archive_dir / "final_transcript.md").exists())


if __name__ == "__main__":
    unittest.main()
