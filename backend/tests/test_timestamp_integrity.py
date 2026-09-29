from __future__ import annotations

import unittest

from meeting_scribe.services.offline.adapters import _words_from_vosk_messages


class TimestampIntegrityTests(unittest.TestCase):
    def test_uses_timestamps_returned_by_the_transcriber(self) -> None:
        words = _words_from_vosk_messages(
            [
                {
                    'result': [
                        {
                            'word': 'konuşalım',
                            'start': 3.2,
                            'end': 3.8,
                            'conf': 0.91,
                        }
                    ]
                }
            ]
        )

        self.assertEqual(len(words), 1)
        self.assertEqual(words[0].text, 'konuşalım')
        self.assertEqual(words[0].start_sec, 3.2)
        self.assertEqual(words[0].end_sec, 3.8)

    def test_rejects_flat_text_instead_of_inventing_word_timestamps(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'without word timestamps'):
            _words_from_vosk_messages(
                [
                    {'text': 'Bugün bütçeyi konuşalım'},
                    {'text': 'Ben de katılıyorum'},
                ]
            )


if __name__ == '__main__':
    unittest.main()
