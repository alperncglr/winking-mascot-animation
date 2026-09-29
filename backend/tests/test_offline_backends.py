from __future__ import annotations

import unittest
from unittest.mock import patch

from meeting_scribe.config import OfflineConfig, Settings
from meeting_scribe.services.offline.adapters import PlaceholderDiarizer, PlaceholderTranscriber
from meeting_scribe.services.offline.pipeline import build_diarizer, build_transcriber
from tests.test_runtime_purge import build_settings as build_full_settings
from tests.helpers import workspace_tempdir


def _settings_with_offline(offline: OfflineConfig) -> Settings:
    with workspace_tempdir() as tmp:
        base = build_full_settings(tmp)
    from dataclasses import replace

    return replace(base, offline=offline)


class BackendDispatchTests(unittest.TestCase):
    def test_build_transcriber_rejects_unknown_backend(self) -> None:
        offline = OfflineConfig(
            'unknown', 'http://x', 'ws://x', 5, 2, 32000, 'large-v3',
            'float16', 'placeholder', 'model', 'HF_TOKEN', 1,
        )
        settings = _settings_with_offline(offline)
        with self.assertRaises(ValueError):
            build_transcriber(settings)

    def test_build_diarizer_defaults_to_placeholder(self) -> None:
        offline = OfflineConfig(
            'remote_openai', 'http://x', 'ws://x', 5, 2, 32000, 'large-v3',
            'float16', 'placeholder', 'model', 'HF_TOKEN', 1,
        )
        settings = _settings_with_offline(offline)
        diarizer = build_diarizer(settings)
        self.assertIsInstance(diarizer, PlaceholderDiarizer)

    def test_build_diarizer_dispatches_to_pyannote_without_loading_model(self) -> None:
        offline = OfflineConfig(
            'remote_openai', 'http://x', 'ws://x', 5, 2, 32000, 'large-v3',
            'float16', 'pyannote', 'model', 'HF_TOKEN', 1,
        )
        settings = _settings_with_offline(offline)
        with patch(
            'meeting_scribe.services.offline.pipeline.PyannoteDiarizer'
        ) as diarizer_class:
            build_diarizer(settings)
        diarizer_class.assert_called_once_with(
            model_name='model',
            hf_token=None,
            segmentation_batch_size=8,
            embedding_batch_size=8,
        )



if __name__ == "__main__":
    unittest.main()
