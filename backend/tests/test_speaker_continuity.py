from __future__ import annotations

import json
import unittest

from meeting_scribe.services.offline.adapters import SpeakerTurn
from meeting_scribe.services.offline.speaker_continuity import (
    _maximum_weight_pairs,
    load_live_speaker_labels,
    load_live_speaker_turns,
    match_offline_speakers_to_live_labels,
)
from tests.helpers import workspace_tempdir


class SpeakerContinuityTests(unittest.TestCase):
    def test_matches_swapped_offline_ids_by_timeline_overlap(self) -> None:
        live = [
            SpeakerTurn('live_0', 0.0, 4.0),
            SpeakerTurn('live_1', 4.0, 10.0),
        ]
        offline = [
            SpeakerTurn('SPEAKER_09', 0.0, 4.0),
            SpeakerTurn('SPEAKER_02', 4.0, 10.0),
        ]

        result = match_offline_speakers_to_live_labels(
            offline,
            live,
            {'live_0': 'Kullanıcı 1', 'live_1': 'Kullanıcı 2'},
        )

        self.assertEqual(
            result,
            {'SPEAKER_09': 'Kullanıcı 1', 'SPEAKER_02': 'Kullanıcı 2'},
        )

    def test_repeated_rolling_predictions_are_merged_before_scoring(self) -> None:
        live = [
            SpeakerTurn('live_0', 0.0, 5.0),
            SpeakerTurn('live_0', 0.0, 5.0),
            SpeakerTurn('live_1', 5.0, 9.0),
        ]
        offline = [
            SpeakerTurn('A', 0.0, 5.0),
            SpeakerTurn('B', 5.0, 9.0),
        ]

        result = match_offline_speakers_to_live_labels(
            offline,
            live,
            {'live_0': 'Kullanıcı 1', 'live_1': 'Kullanıcı 2'},
        )

        self.assertEqual(result, {'A': 'Kullanıcı 1', 'B': 'Kullanıcı 2'})

    def test_loads_valid_turns_and_skips_bad_json(self) -> None:
        with workspace_tempdir() as tmp:
            path = tmp / 'live_diarization.jsonl'
            path.write_text(
                'not-json\n'
                + json.dumps(
                    {
                        'turns': [
                            {
                                'speaker_id': 'speaker_0',
                                'start_sec': 1.0,
                                'end_sec': 2.5,
                                'overlap': True,
                            }
                        ]
                    }
                )
                + '\n',
                encoding='utf-8',
            )

            turns = load_live_speaker_turns(path)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].speaker_id, 'speaker_0')
        self.assertTrue(turns[0].overlap)

    def test_uses_global_maximum_instead_of_greedy_local_maximum(self) -> None:
        pairs = _maximum_weight_pairs(
            ['A', 'B'],
            ['live_1', 'live_2'],
            {
                ('A', 'live_1'): 10.0,
                ('A', 'live_2'): 9.0,
                ('B', 'live_1'): 9.0,
            },
        )

        self.assertEqual(
            pairs,
            (('A', 'live_2'), ('B', 'live_1')),
        )

    def test_live_label_file_remains_source_of_truth_after_offline_rerun(self) -> None:
        with workspace_tempdir() as tmp:
            path = tmp / 'live_transcript.jsonl'
            path.write_text(
                json.dumps(
                    {
                        'diarization_speaker_id': 'live_7',
                        'speaker_label': 'Kullanıcı 2',
                        'text': 'merhaba',
                    },
                    ensure_ascii=False,
                )
                + '\n',
                encoding='utf-8',
            )

            labels = load_live_speaker_labels(path)

        self.assertEqual(labels, {'live_7': 'Kullanıcı 2'})


if __name__ == '__main__':
    unittest.main()
