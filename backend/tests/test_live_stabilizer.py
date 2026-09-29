from __future__ import annotations

import unittest

from services.whisper_bridge.stabilizer import LiveHypothesisStabilizer


def words(*items: tuple[str, float, float]) -> list[dict]:
    return [
        {'word': word, 'start': start, 'end': end, 'conf': 0.9}
        for word, start, end in items
    ]


class LiveHypothesisStabilizerTests(unittest.TestCase):
    def test_first_hypothesis_is_provisional(self) -> None:
        state = LiveHypothesisStabilizer()
        events = state.update(
            words(('Merhaba', 0.0, 0.5), ('dünya', 0.6, 1.1)),
            audio_end_sec=2.0,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, 'provisional')
        self.assertEqual(events[0].text, 'Merhaba dünya')

    def test_repeated_prefix_becomes_final(self) -> None:
        state = LiveHypothesisStabilizer(holdback_seconds=0.5)
        state.update(
            words(('Merhaba', 0.0, 0.5), ('dünya', 0.6, 1.1)),
            audio_end_sec=2.0,
        )
        events = state.update(
            words(
                ('merhaba,', 0.0, 0.5),
                ('dünya', 0.6, 1.1),
                ('nasılsın', 1.2, 1.8),
            ),
            audio_end_sec=2.5,
        )
        self.assertEqual([event.status for event in events], ['final', 'provisional'])
        self.assertEqual(events[0].text, 'merhaba, dünya')
        self.assertEqual(events[1].text, 'nasılsın')
        self.assertNotEqual(events[0].segment_id, events[1].segment_id)

    def test_force_final_uses_latest_hypothesis(self) -> None:
        state = LiveHypothesisStabilizer()
        first = state.update(
            words(('Toplantı', 4.0, 4.5), ('başladı', 4.6, 5.1)),
            audio_end_sec=5.5,
        )[0]
        events = state.update([], audio_end_sec=6.0, force_final=True)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, 'final')
        self.assertEqual(events[0].segment_id, first.segment_id)
        self.assertEqual(events[0].text, 'Toplantı başladı')

    def test_force_final_does_not_truncate_visible_hypothesis(self) -> None:
        state = LiveHypothesisStabilizer()
        first = state.update(
            words(
                ('Toplantı', 0.0, 0.5),
                ('normal', 0.6, 1.0),
                ('hızda', 1.1, 1.5),
                ('devam', 1.6, 2.0),
                ('ediyor', 2.1, 2.5),
            ),
            audio_end_sec=2.8,
        )[0]

        events = state.update(
            words(('Toplantı', 0.0, 0.5), ('normal', 0.6, 1.0)),
            audio_end_sec=3.0,
            force_final=True,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].segment_id, first.segment_id)
        self.assertEqual(events[0].text, 'Toplantı normal hızda devam ediyor')

    def test_repeated_prefix_does_not_drop_previous_tail(self) -> None:
        state = LiveHypothesisStabilizer(holdback_seconds=0.0)
        state.update(
            words(
                ('bir', 0.0, 0.3),
                ('iki', 0.4, 0.7),
                ('üç', 0.8, 1.1),
                ('dört', 1.2, 1.5),
            ),
            audio_end_sec=1.8,
        )

        events = state.update(
            words(('bir', 0.0, 0.3), ('iki', 0.4, 0.7)),
            audio_end_sec=2.0,
        )

        self.assertEqual([event.status for event in events], ['final', 'provisional'])
        self.assertEqual(events[0].text, 'bir iki')
        self.assertEqual(events[1].text, 'üç dört')

    def test_committed_words_are_not_emitted_twice(self) -> None:
        state = LiveHypothesisStabilizer(holdback_seconds=0.0)
        hypothesis = words(('bir', 0.0, 0.3), ('iki', 0.4, 0.7))
        state.update(hypothesis, audio_end_sec=1.0)
        state.update(hypothesis, audio_end_sec=1.0)
        events = state.update(hypothesis, audio_end_sec=1.2)
        self.assertEqual(events, [])

    def test_uninterrupted_speech_rolls_forward_with_context_window(self) -> None:
        state = LiveHypothesisStabilizer(holdback_seconds=0.6)
        first = state.update(
            words(
                ('sıfır', 0.0, 0.4),
                ('bir', 0.5, 0.9),
                ('iki', 1.0, 1.4),
                ('üç', 1.5, 1.9),
                ('dört', 2.0, 2.4),
                ('beş', 2.5, 2.9),
            ),
            audio_end_sec=3.0,
        )[0]

        events = state.update(
            words(
                ('üç', 1.5, 1.9),
                ('dört', 2.0, 2.4),
                ('beş', 2.5, 2.9),
                ('altı', 3.0, 3.4),
                ('yedi', 3.5, 3.9),
            ),
            audio_end_sec=4.0,
        )

        self.assertEqual([event.status for event in events], ['final', 'provisional'])
        self.assertEqual(events[0].segment_id, first.segment_id)
        self.assertEqual(events[0].text, 'sıfır bir iki')
        self.assertEqual(events[1].text, 'üç dört beş altı yedi')
        self.assertNotEqual(events[0].segment_id, events[1].segment_id)

    def test_forward_rolling_window_is_not_mistaken_for_truncation(self) -> None:
        state = LiveHypothesisStabilizer()
        state.update(
            words(
                ('bir', 0.0, 0.4),
                ('iki', 0.5, 0.9),
                ('üç', 1.0, 1.4),
            ),
            audio_end_sec=1.5,
        )

        events = state.update(
            words(
                ('iki', 0.5, 0.9),
                ('üç', 1.0, 1.4),
                ('dört', 1.5, 1.9),
            ),
            audio_end_sec=2.0,
        )

        self.assertEqual(events[-1].status, 'provisional')
        self.assertEqual(events[-1].text, 'iki üç dört')


if __name__ == '__main__':
    unittest.main()
