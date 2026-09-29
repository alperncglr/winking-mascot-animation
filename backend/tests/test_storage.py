from __future__ import annotations

import sqlite3
import unittest

from meeting_scribe.domain.models import Segment, SegmentStatus
from meeting_scribe.storage.db import Database
from meeting_scribe.storage.repositories import MeetingRepository, SegmentRepository
from tests.helpers import workspace_tempdir


class StorageTests(unittest.TestCase):
    def test_initialize_rebuilds_legacy_segments_identity_columns(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'legacy-segments.sqlite3')
            db.initialize()
            meeting_id = MeetingRepository(db).create_meeting(
                'Legacy', 'Operator', 'operator-1'
            )
            with db.connect() as conn:
                conn.execute('DROP TRIGGER segments_ai')
                conn.execute('DROP TRIGGER segments_ad')
                conn.execute('DROP TRIGGER segments_au')
                conn.execute('DROP TABLE segments')
                conn.execute(
                    '''
                    CREATE TABLE segments (
                      meeting_id INTEGER NOT NULL REFERENCES meetings(id),
                      segment_id TEXT NOT NULL,
                      revision INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      start_sec REAL NOT NULL,
                      end_sec REAL NOT NULL,
                      diarization_speaker_id TEXT,
                      participant_id INTEGER REFERENCES participants(id),
                      identity_status TEXT NOT NULL,
                      identity_confidence REAL,
                      transcript_confidence REAL,
                      overlap INTEGER DEFAULT 0,
                      text TEXT NOT NULL,
                      PRIMARY KEY (meeting_id, segment_id, revision)
                    )
                    '''
                )
                conn.execute(
                    '''
                    INSERT INTO segments(
                      meeting_id, segment_id, revision, status, start_sec,
                      end_sec, identity_status, text
                    ) VALUES (?, 'legacy_1', 1, 'final', 0, 2, 'unknown', ?)
                    ''',
                    (meeting_id, 'preserved text'),
                )

            db.initialize()

            with db.connect() as conn:
                columns = {
                    row['name']
                    for row in conn.execute('PRAGMA table_info(segments)')
                }
                row = conn.execute(
                    'SELECT text FROM segments WHERE segment_id = ?',
                    ('legacy_1',),
                ).fetchone()
                violations = conn.execute('PRAGMA foreign_key_check').fetchall()

            self.assertNotIn('identity_status', columns)
            self.assertNotIn('identity_confidence', columns)
            self.assertEqual(row['text'], 'preserved text')
            self.assertEqual(violations, [])

    def test_initialize_rebuilds_legacy_participants_anonymously(self) -> None:
        with workspace_tempdir() as tmp:
            db_path = tmp / 'legacy-participants.sqlite3'
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    '''
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE meetings (
                      id INTEGER PRIMARY KEY,
                      title TEXT NOT NULL,
                      date TEXT NOT NULL,
                      duration_sec INTEGER,
                      started_by_name TEXT NOT NULL,
                      started_by_id TEXT NOT NULL,
                      language TEXT NOT NULL DEFAULT 'mixed',
                      status TEXT NOT NULL,
                      runtime_purged_at TEXT,
                      updated_at TEXT NOT NULL
                    );
                    CREATE TABLE participants (
                      id INTEGER PRIMARY KEY,
                      meeting_id INTEGER NOT NULL REFERENCES meetings(id),
                      name TEXT NOT NULL,
                      language TEXT NOT NULL,
                      talk_time_sec REAL DEFAULT 0
                    );
                    CREATE TABLE segments (
                      meeting_id INTEGER NOT NULL REFERENCES meetings(id),
                      segment_id TEXT NOT NULL,
                      revision INTEGER NOT NULL,
                      status TEXT NOT NULL,
                      start_sec REAL NOT NULL,
                      end_sec REAL NOT NULL,
                      diarization_speaker_id TEXT,
                      participant_id INTEGER REFERENCES participants(id),
                      transcript_confidence REAL,
                      overlap INTEGER DEFAULT 0,
                      text TEXT NOT NULL,
                      PRIMARY KEY (meeting_id, segment_id, revision)
                    );
                    INSERT INTO meetings(
                      id, title, date, started_by_name, started_by_id,
                      language, status, updated_at
                    ) VALUES (
                      1, 'Eski toplantı', '2026-08-06', 'Operator',
                      'operator-1', 'mixed', 'recorded', '2026-08-06'
                    );
                    INSERT INTO participants(
                      id, meeting_id, name, language, talk_time_sec
                    ) VALUES (7, 1, 'Eski İsim', 'tr', 4.5);
                    INSERT INTO segments(
                      meeting_id, segment_id, revision, status, start_sec,
                      end_sec, participant_id, text
                    ) VALUES (1, 'legacy_1', 1, 'final', 0, 4.5, 7, 'metin');
                    '''
                )

            db = Database(db_path)
            db.initialize()

            with db.connect() as conn:
                columns = {
                    row['name']
                    for row in conn.execute('PRAGMA table_info(participants)')
                }
                participant = conn.execute(
                    '''
                    SELECT id, meeting_id, diarization_speaker_id, label,
                           talk_time_sec
                    FROM participants WHERE id = 7
                    '''
                ).fetchone()
                segment = conn.execute(
                    'SELECT participant_id FROM segments WHERE segment_id = ?',
                    ('legacy_1',),
                ).fetchone()
                violations = conn.execute('PRAGMA foreign_key_check').fetchall()

            self.assertEqual(
                columns,
                {
                    'id',
                    'meeting_id',
                    'diarization_speaker_id',
                    'label',
                    'talk_time_sec',
                },
            )
            self.assertEqual(participant['diarization_speaker_id'], 'legacy_7')
            self.assertEqual(participant['label'], 'Kullanıcı 1')
            self.assertEqual(participant['talk_time_sec'], 4.5)
            self.assertEqual(segment['participant_id'], 7)
            self.assertEqual(violations, [])

    def test_initialize_migrates_legacy_schema_without_losing_rows(self) -> None:
        with workspace_tempdir() as tmp:
            db_path = tmp / 'legacy.sqlite3'
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    '''
                    CREATE TABLE meetings (
                      id INTEGER PRIMARY KEY,
                      title TEXT NOT NULL,
                      date TEXT NOT NULL,
                      started_by_name TEXT NOT NULL,
                      started_by_id TEXT NOT NULL,
                      status TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    '''
                )
                conn.execute(
                    '''
                    INSERT INTO meetings(
                      title, date, started_by_name, started_by_id, status, updated_at
                    ) VALUES ('Eski toplantı', '2026-08-06', 'Operator', 'operator-1', 'created', '2026-08-06')
                    '''
                )

            db = Database(db_path)
            db.initialize()

            with db.connect() as conn:
                row = conn.execute(
                    'SELECT title, language FROM meetings WHERE id = 1'
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['title'], 'Eski toplantı')
            self.assertEqual(row['language'], 'mixed')

    def test_create_meeting_segments_and_search(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / "db.sqlite3")
            db.initialize()
            meetings = MeetingRepository(db)
            segments = SegmentRepository(db)
            meeting_id = meetings.create_meeting(
                "Weekly",
                "Operator",
                "operator@example.local",
            )
            segments.replace_final_segments(
                meeting_id,
                [
                    Segment(
                        meeting_id=meeting_id,
                        segment_id="s1",
                        revision=1,
                        status=SegmentStatus.FINAL,
                        start_sec=0,
                        end_sec=1,
                        diarization_speaker_id="speaker_0",
                        participant_id=None,
                        transcript_confidence=0.9,
                        overlap=False,
                        text="bütçe konuşuldu",
                    )
                ],
            )
            found = segments.search("bütçe")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["segment_id"], "s1")

    def test_search_excludes_superseded_segment_revisions(self) -> None:
        with workspace_tempdir() as tmp:
            db = Database(tmp / 'db.sqlite3')
            db.initialize()
            meeting_id = MeetingRepository(db).create_meeting('Search', 'Operator', 'operator-1')
            with db.connect() as conn:
                for revision, text in [(1, 'eski kelime'), (2, 'yeni kelime')]:
                    conn.execute(
                        '''INSERT INTO segments(meeting_id, segment_id, revision, status,
                           start_sec, end_sec, text) VALUES (?, 'live_1', ?, 'final', 0, 1, ?)''',
                        (meeting_id, revision, text),
                    )
            segments = SegmentRepository(db)
            self.assertEqual(segments.search('eski'), [])
            self.assertEqual([row['text'] for row in segments.search('yeni')], ['yeni kelime'])


if __name__ == "__main__":
    unittest.main()
