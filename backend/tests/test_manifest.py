from __future__ import annotations

import unittest

from meeting_scribe.services.capture.manifest import Manifest, combine_chunks
from meeting_scribe.services.capture.recorder import create_silent_chunk
from tests.helpers import workspace_tempdir


class ManifestTests(unittest.TestCase):
    def test_create_and_combine_silent_chunk(self) -> None:
        with workspace_tempdir() as root:
            audio_dir = root / "audio"
            manifest = audio_dir / "manifest.jsonl"
            create_silent_chunk(audio_dir, manifest, sample_rate=16000, seconds=1)

            chunks = Manifest(manifest).read_all()
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].file_name, "chunk_000001.wav")
            self.assertEqual(chunks[0].sample_count, 16000)

            combined = combine_chunks(audio_dir, manifest, root / "combined.wav")
            self.assertTrue(combined.exists())


if __name__ == "__main__":
    unittest.main()
