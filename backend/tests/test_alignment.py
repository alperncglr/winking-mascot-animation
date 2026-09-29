

from __future__ import annotations

import unittest

from meeting_scribe.services.offline.adapters import SpeakerTurn, Word
from meeting_scribe.services.offline.alignment import align_words_to_segments


class AlignmentTests(unittest.TestCase):
    def test_groups_adjacent_words_by_dominant_speaker(self) -> None:
        words = [
            Word("Merhaba", 0.0, 0.5, 0.9),
            Word("dunya", 0.5, 1.0, 0.8),
            Word("tamam", 1.2, 1.5, 0.7),
        ]
        turns = [
            SpeakerTurn("speaker_0", 0.0, 1.1),
            SpeakerTurn("speaker_1", 1.1, 2.0),
        ]
        segments = align_words_to_segments(1, words, turns)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].diarization_speaker_id, "speaker_0")
        self.assertEqual(segments[0].text, "Merhaba dunya")
        self.assertEqual(segments[1].diarization_speaker_id, "speaker_1")

    def test_splits_same_speaker_after_long_silence(self) -> None:
        words = [Word("ilk", 0.0, 0.4, 0.9), Word("sonra", 4.0, 4.5, 0.9)]
        turns = [SpeakerTurn("speaker_0", 0.0, 5.0)]
        segments = align_words_to_segments(1, words, turns)
        self.assertEqual([segment.text for segment in segments], ["ilk", "sonra"])
        self.assertEqual([segment.end_sec - segment.start_sec for segment in segments], [0.4, 0.5])


if __name__ == "__main__":
    unittest.main()
