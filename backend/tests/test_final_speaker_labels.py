from __future__ import annotations

import unittest

from meeting_scribe.domain.models import Segment, SegmentStatus
from meeting_scribe.storage.db import Database
from meeting_scribe.storage.repositories import MeetingRepository, SegmentRepository
from tests.helpers import workspace_tempdir


class FinalSpeakerLabelTests(unittest.TestCase):
    def test_same_cluster_keeps_the_same_meeting_local_label(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'db.sqlite3')
            db.initialize()
            meetings = MeetingRepository(db)
            repository = SegmentRepository(db)
            meeting_id = meetings.create_meeting('Test', 'Operator', 'operator-1')

            def segment(index: int, speaker: str | None) -> Segment:
                return Segment(
                    meeting_id=meeting_id,
                    segment_id=f'offline_{index:06d}',
                    revision=1,
                    status=SegmentStatus.FINAL,
                    start_sec=float(index - 1),
                    end_sec=float(index),
                    diarization_speaker_id=speaker,
                    participant_id=None,
                    transcript_confidence=0.9,
                    overlap=False,
                    text=f'metin {index}',
                )

            resolved = repository.replace_final_segments_with_labels(
                meeting_id,
                [
                    segment(1, 'SPEAKER_07'),
                    segment(2, 'SPEAKER_12'),
                    segment(3, 'SPEAKER_07'),
                    segment(4, None),
                ],
            )

            participants = meetings.participants(meeting_id)
            self.assertEqual(
                [participant['label'] for participant in participants],
                ['Kullanıcı 1', 'Kullanıcı 2'],
            )
            self.assertEqual(resolved[0].participant_id, resolved[2].participant_id)
            self.assertNotEqual(resolved[0].participant_id, resolved[1].participant_id)
            self.assertIsNone(resolved[3].participant_id)

    def test_preferred_live_labels_survive_offline_cluster_renaming(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'db.sqlite3')
            db.initialize()
            meetings = MeetingRepository(db)
            repository = SegmentRepository(db)
            meeting_id = meetings.create_meeting('Test', 'Operator', 'operator-1')
            meetings.get_or_create_participant(meeting_id, 'live_0')
            meetings.get_or_create_participant(meeting_id, 'live_1')

            segments = [
                Segment(
                    meeting_id=meeting_id,
                    segment_id='offline_000001',
                    revision=1,
                    status=SegmentStatus.FINAL,
                    start_sec=0.0,
                    end_sec=2.0,
                    diarization_speaker_id='OFFLINE_B',
                    participant_id=None,
                    transcript_confidence=0.9,
                    overlap=False,
                    text='ikinci kişi',
                ),
                Segment(
                    meeting_id=meeting_id,
                    segment_id='offline_000002',
                    revision=1,
                    status=SegmentStatus.FINAL,
                    start_sec=2.0,
                    end_sec=4.0,
                    diarization_speaker_id='OFFLINE_A',
                    participant_id=None,
                    transcript_confidence=0.9,
                    overlap=False,
                    text='birinci kişi',
                ),
            ]

            resolved = repository.replace_final_segments_with_labels(
                meeting_id,
                segments,
                preferred_labels={
                    'OFFLINE_A': 'Kullanıcı 1',
                    'OFFLINE_B': 'Kullanıcı 2',
                },
            )
            labels = {
                participant['id']: participant['label']
                for participant in meetings.participants(meeting_id)
            }

        self.assertEqual(labels[resolved[0].participant_id], 'Kullanıcı 2')
        self.assertEqual(labels[resolved[1].participant_id], 'Kullanıcı 1')

    def test_unmatched_live_label_is_not_reused_for_a_new_offline_speaker(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'db.sqlite3')
            db.initialize()
            meetings = MeetingRepository(db)
            repository = SegmentRepository(db)
            meeting_id = meetings.create_meeting('Test', 'Operator', 'operator-1')
            segment = Segment(
                meeting_id=meeting_id,
                segment_id='offline_000001',
                revision=1,
                status=SegmentStatus.FINAL,
                start_sec=0.0,
                end_sec=2.0,
                diarization_speaker_id='NEW_OFFLINE',
                participant_id=None,
                transcript_confidence=0.9,
                overlap=False,
                text='yeni kişi',
            )

            resolved = repository.replace_final_segments_with_labels(
                meeting_id,
                [segment],
                reserved_labels={'Kullanıcı 1', 'Kullanıcı 2'},
            )
            participant = meetings.participant(
                meeting_id,
                resolved[0].participant_id,
            )

        self.assertEqual(participant['label'], 'Kullanıcı 3')


if __name__ == '__main__':
    unittest.main()
