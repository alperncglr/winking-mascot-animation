from __future__ import annotations

import unittest
from pathlib import Path

from meeting_scribe.config import _resolve_model_reference


class ConfigTests(unittest.TestCase):
    def test_resolves_project_local_model_reference(self) -> None:
        project_root = Path.cwd() / 'project-root'
        resolved = _resolve_model_reference(
            project_root,
            'models/pyannote-speaker-diarization-community-1',
        )
        self.assertEqual(
            resolved,
            str(
                (
                    project_root
                    / 'models'
                    / 'pyannote-speaker-diarization-community-1'
                ).resolve()
            ),
        )

    def test_keeps_hugging_face_repo_id(self) -> None:
        self.assertEqual(
            _resolve_model_reference(
                Path('/project'),
                'pyannote/speaker-diarization-community-1',
            ),
            'pyannote/speaker-diarization-community-1',
        )


if __name__ == '__main__':
    unittest.main()
