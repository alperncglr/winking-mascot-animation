from __future__ import annotations

import unittest

from meeting_scribe.services.capture.manifest import Manifest
from meeting_scribe.services.capture.recorder import NetworkChunkWriter, resolve_staged_wav_path
from tests.helpers import workspace_tempdir


def silence(seconds: float, sample_rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)


class NetworkChunkWriterTests(unittest.TestCase):
    def test_wav_import_must_be_staged(self) -> None:
        with workspace_tempdir() as tmp:
            imports = tmp / 'imports'
            imports.mkdir()
            allowed = imports / 'allowed.wav'
            allowed.write_bytes(b'RIFF')
            outside = tmp / 'outside.wav'
            outside.write_bytes(b'RIFF')
            self.assertEqual(resolve_staged_wav_path('allowed.wav', tmp), allowed.resolve())
            with self.assertRaises(ValueError):
                resolve_staged_wav_path(str(outside.resolve()), tmp)
            with self.assertRaises(ValueError):
                resolve_staged_wav_path('../outside.wav', tmp)

    def test_splits_pushed_audio_into_fixed_size_chunks(self) -> None:
        with workspace_tempdir() as tmp:
            audio_dir = tmp / "audio"
            manifest_path = audio_dir / "manifest.jsonl"
            writer = NetworkChunkWriter(audio_dir, manifest_path, sample_rate=16000, chunk_seconds=1)

            writer.push(silence(1.5))  # one full chunk + half a chunk buffered
            chunks = Manifest(manifest_path).read_all()

            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].file_name, "chunk_000001.wav")
            self.assertAlmostEqual(chunks[0].duration_sec, 1.0)
            self.assertTrue((audio_dir / "chunk_000001.wav").exists())

    def test_finalize_flushes_remaining_partial_chunk(self) -> None:
        with workspace_tempdir() as tmp:
            audio_dir = tmp / "audio"
            manifest_path = audio_dir / "manifest.jsonl"
            writer = NetworkChunkWriter(audio_dir, manifest_path, sample_rate=16000, chunk_seconds=1)

            writer.push(silence(1.5))
            writer.finalize()
            chunks = Manifest(manifest_path).read_all()

            self.assertEqual(len(chunks), 2)
            self.assertAlmostEqual(chunks[1].duration_sec, 0.5, places=2)
            self.assertAlmostEqual(chunks[1].start_sec, 1.0, places=2)

    def test_finalize_with_no_buffered_audio_is_a_noop(self) -> None:
        with workspace_tempdir() as tmp:
            audio_dir = tmp / "audio"
            manifest_path = audio_dir / "manifest.jsonl"
            writer = NetworkChunkWriter(audio_dir, manifest_path, sample_rate=16000, chunk_seconds=1)

            writer.push(silence(1.0))
            writer.finalize()
            chunks = Manifest(manifest_path).read_all()

            self.assertEqual(len(chunks), 1)

    def test_resumed_session_continues_existing_meeting_timeline(self) -> None:
        with workspace_tempdir() as tmp:
            audio_dir = tmp / 'audio'
            manifest_path = audio_dir / 'manifest.jsonl'
            first = NetworkChunkWriter(audio_dir, manifest_path, sample_rate=16000, chunk_seconds=1)
            first.push(silence(1.5))
            first.finalize()
            resumed = NetworkChunkWriter(audio_dir, manifest_path, sample_rate=16000, chunk_seconds=1)
            self.assertAlmostEqual(resumed.elapsed_sec, 1.5)
            resumed.push(silence(1.0))
            chunks = Manifest(manifest_path).read_all()
            self.assertEqual([chunk.file_name for chunk in chunks], [
                'chunk_000001.wav', 'chunk_000002.wav', 'chunk_000003.wav',
            ])
            self.assertAlmostEqual(chunks[-1].start_sec, 1.5)


if __name__ == "__main__":
    unittest.main()
