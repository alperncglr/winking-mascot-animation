from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from meeting_scribe.storage.schema import SCHEMA_SQL


_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    'meetings': {
        'duration_sec': 'INTEGER',
        'language': "TEXT NOT NULL DEFAULT 'mixed'",
        'runtime_purged_at': 'TEXT',
    },
    'participants': {
        'talk_time_sec': 'REAL DEFAULT 0',
    },
    'segments': {
        'diarization_speaker_id': 'TEXT',
        'participant_id': 'INTEGER REFERENCES participants(id)',
        'transcript_confidence': 'REAL',
        'overlap': 'INTEGER DEFAULT 0',
    },
    'summaries': {
        'discussed_topics_json': "TEXT NOT NULL DEFAULT '[]'",
        'open_questions_json': "TEXT NOT NULL DEFAULT '[]'",
        'open_topics_json': "TEXT NOT NULL DEFAULT '[]'",
        'next_topics_json': "TEXT NOT NULL DEFAULT '[]'",
    },
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate_legacy_participants(conn)
            self._migrate_legacy_segments(conn)
            self._apply_additive_migrations(conn)
            conn.executescript(SCHEMA_SQL)

    @staticmethod
    def _migrate_legacy_participants(conn: sqlite3.Connection) -> None:
        columns = {
            str(row['name'])
            for row in conn.execute('PRAGMA table_info(participants)')
        }
        if {'diarization_speaker_id', 'label'} <= columns:
            return

        diarization_expression = (
            "COALESCE(NULLIF(diarization_speaker_id, ''), 'legacy_' || id)"
            if 'diarization_speaker_id' in columns
            else "'legacy_' || id"
        )
        talk_time_expression = (
            'COALESCE(talk_time_sec, 0)'
            if 'talk_time_sec' in columns
            else '0'
        )

        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.execute('BEGIN')
            conn.execute('DROP TABLE IF EXISTS participants_migrated')
            conn.execute(
                '''
                CREATE TABLE participants_migrated (
                  id INTEGER PRIMARY KEY,
                  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                  diarization_speaker_id TEXT NOT NULL,
                  label TEXT NOT NULL,
                  talk_time_sec REAL DEFAULT 0,
                  UNIQUE (meeting_id, diarization_speaker_id)
                )
                '''
            )
            conn.execute(
                f'''
                INSERT INTO participants_migrated(
                  id, meeting_id, diarization_speaker_id, label, talk_time_sec
                )
                SELECT
                  id,
                  meeting_id,
                  {diarization_expression},
                  'Kullanıcı ' || ROW_NUMBER() OVER (
                    PARTITION BY meeting_id ORDER BY id
                  ),
                  {talk_time_expression}
                FROM participants
                '''
            )
            conn.execute('DROP TABLE participants')
            conn.execute(
                'ALTER TABLE participants_migrated RENAME TO participants'
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')

        violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError(
                f'Foreign key violations after participants migration: {violations}'
            )

    @staticmethod
    def _migrate_legacy_segments(conn: sqlite3.Connection) -> None:
        canonical_columns = {
            'meeting_id',
            'segment_id',
            'revision',
            'status',
            'start_sec',
            'end_sec',
            'diarization_speaker_id',
            'participant_id',
            'transcript_confidence',
            'overlap',
            'text',
        }
        columns = {
            str(row['name'])
            for row in conn.execute('PRAGMA table_info(segments)')
        }
        if columns == canonical_columns:
            return

        required = {
            'meeting_id',
            'segment_id',
            'revision',
            'status',
            'start_sec',
            'end_sec',
            'text',
        }
        missing = required - columns
        if missing:
            raise RuntimeError(
                f'Cannot migrate segments table; missing columns: {sorted(missing)}'
            )

        optional_expressions = {
            'diarization_speaker_id': (
                'diarization_speaker_id'
                if 'diarization_speaker_id' in columns
                else 'NULL'
            ),
            'participant_id': (
                'participant_id' if 'participant_id' in columns else 'NULL'
            ),
            'transcript_confidence': (
                'transcript_confidence'
                if 'transcript_confidence' in columns
                else 'NULL'
            ),
            'overlap': (
                'COALESCE(overlap, 0)' if 'overlap' in columns else '0'
            ),
        }

        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.execute('BEGIN')
            conn.execute('DROP TRIGGER IF EXISTS segments_ai')
            conn.execute('DROP TRIGGER IF EXISTS segments_ad')
            conn.execute('DROP TRIGGER IF EXISTS segments_au')
            conn.execute('DROP TABLE IF EXISTS segments_migrated')
            conn.execute(
                '''
                CREATE TABLE segments_migrated (
                  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
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
                )
                '''
            )
            conn.execute(
                f'''
                INSERT INTO segments_migrated(
                  meeting_id, segment_id, revision, status, start_sec, end_sec,
                  diarization_speaker_id, participant_id,
                  transcript_confidence, overlap, text
                )
                SELECT
                  meeting_id, segment_id, revision, status, start_sec, end_sec,
                  {optional_expressions['diarization_speaker_id']},
                  {optional_expressions['participant_id']},
                  {optional_expressions['transcript_confidence']},
                  {optional_expressions['overlap']},
                  text
                FROM segments
                '''
            )
            conn.execute('DROP TABLE segments')
            conn.execute('ALTER TABLE segments_migrated RENAME TO segments')
            conn.execute('DELETE FROM segments_fts')
            conn.execute(
                '''
                INSERT INTO segments_fts(
                  rowid, text, meeting_id, segment_id, revision
                )
                SELECT rowid, text, meeting_id, segment_id, revision
                FROM segments
                '''
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')

        violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError(
                f'Foreign key violations after segments migration: {violations}'
            )

    @staticmethod
    def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
        for table, required_columns in _ADDITIVE_COLUMNS.items():
            existing_columns = {
                str(row['name'])
                for row in conn.execute(f'PRAGMA table_info({table})')
            }
            for column, declaration in required_columns.items():
                if column in existing_columns:
                    continue
                conn.execute(
                    f'ALTER TABLE {table} ADD COLUMN {column} {declaration}'
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
