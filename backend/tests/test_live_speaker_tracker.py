from __future__ import annotations

import unittest
import asyncio

from meeting_scribe.services.live.diarization import LiveSpeakerTracker


class LiveSpeakerTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_selects_speaker_with_most_overlap(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 6.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 2.0},
                    {'speaker_id': 'speaker_1', 'start_sec': 2.0, 'end_sec': 6.0},
                ],
            }
        )

        speaker = await tracker.wait_for_speaker(1.5, 5.0, 0.01)

        self.assertEqual(speaker, 'speaker_1')

    async def test_keeps_diart_speaker_id_stable_across_updates(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 2.0,
                'turns': [
                    {'speaker_id': 'speaker_7', 'start_sec': 0.0, 'end_sec': 2.0},
                ],
            }
        )
        await tracker.update(
            {
                'processed_until_sec': 8.0,
                'turns': [
                    {'speaker_id': 'speaker_7', 'start_sec': 6.0, 'end_sec': 8.0},
                ],
            }
        )

        self.assertEqual(tracker.dominant_speaker(6.0, 8.0), 'speaker_7')

    async def test_waits_for_diarization_to_catch_up_before_assigning(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 2.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 2.0},
                ],
            }
        )

        waiting = asyncio.create_task(tracker.wait_for_speaker(2.0, 6.0, 1.0))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        await tracker.update(
            {
                'processed_until_sec': 5.0,
                'turns': [
                    {'speaker_id': 'speaker_1', 'start_sec': 2.0, 'end_sec': 5.0},
                ],
            }
        )

        self.assertEqual(await waiting, 'speaker_1')

    async def test_groups_words_by_speaker_inside_one_whisper_window(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 5.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 2.0},
                    {'speaker_id': 'speaker_1', 'start_sec': 2.0, 'end_sec': 5.0},
                ],
            }
        )
        groups = tracker.group_words(
            [
                {'word': 'Bugünkü', 'start': 0.2, 'end': 0.8},
                {'word': 'bütçeyi', 'start': 0.8, 'end': 1.4},
                {'word': 'konuşalım.', 'start': 1.4, 'end': 1.9},
                {'word': 'Tamam,', 'start': 2.2, 'end': 2.7},
                {'word': 'hazırladım.', 'start': 2.7, 'end': 3.5},
            ]
        )
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].speaker_id, 'speaker_0')
        self.assertEqual(groups[0].text, 'Bugünkü bütçeyi konuşalım.')
        self.assertEqual(groups[1].speaker_id, 'speaker_1')
        self.assertEqual(groups[1].text, 'Tamam, hazırladım.')

    async def test_marks_group_when_diarization_reports_overlap(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 2.0,
                'turns': [
                    {
                        'speaker_id': 'speaker_0',
                        'start_sec': 0.0,
                        'end_sec': 2.0,
                        'overlap': True,
                    },
                ],
            }
        )
        groups = tracker.group_words(
            [{'word': 'Evet.', 'start': 0.5, 'end': 1.0}]
        )
        self.assertTrue(groups[0].overlap)

    async def test_ignores_a_short_speaker_label_blip(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 3.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 1.1},
                    {'speaker_id': 'speaker_1', 'start_sec': 1.1, 'end_sec': 1.18},
                    {'speaker_id': 'speaker_0', 'start_sec': 1.18, 'end_sec': 3.0},
                ],
            }
        )

        groups = tracker.group_words(
            [
                {'word': 'Bu', 'start': 0.8, 'end': 1.05},
                {'word': 'cümle', 'start': 1.1, 'end': 1.18},
                {'word': 'devam', 'start': 1.2, 'end': 1.5},
                {'word': 'ediyor.', 'start': 1.5, 'end': 1.9},
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].speaker_id, 'speaker_0')

    async def test_keeps_a_real_short_answer_as_a_speaker_switch(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 2.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 1.0},
                    {'speaker_id': 'speaker_1', 'start_sec': 1.0, 'end_sec': 1.4},
                ],
            }
        )

        groups = tracker.group_words(
            [
                {'word': 'Tamam.', 'start': 0.6, 'end': 0.95},
                {'word': 'Evet.', 'start': 1.02, 'end': 1.32},
            ]
        )

        self.assertEqual([group.speaker_id for group in groups], ['speaker_0', 'speaker_1'])

    async def test_provisional_label_resists_weak_new_candidate(self) -> None:
        tracker = LiveSpeakerTracker()
        await tracker.update(
            {
                'processed_until_sec': 1.0,
                'turns': [
                    {'speaker_id': 'speaker_0', 'start_sec': 0.0, 'end_sec': 1.0},
                ],
            }
        )
        tracker.group_words(
            [{'word': 'Merhaba.', 'start': 0.1, 'end': 0.8}]
        )
        await tracker.update(
            {
                'processed_until_sec': 1.2,
                'turns': [
                    {'speaker_id': 'speaker_1', 'start_sec': 1.0, 'end_sec': 1.08},
                ],
            }
        )

        self.assertEqual(
            tracker.provisional_speaker(1.0, 1.1),
            'speaker_0',
        )


if __name__ == '__main__':
    unittest.main()
