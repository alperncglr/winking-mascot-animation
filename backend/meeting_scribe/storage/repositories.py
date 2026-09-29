from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable

from meeting_scribe.domain.models import MeetingStatus, Segment, SummaryResult
from meeting_scribe.storage.db import Database


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeetingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create_meeting(
        self,
        title: str | None,
        started_by_name: str,
        started_by_id: str,
        language: str = 'mixed',
    ) -> int:
        now = utc_now_iso()
        final_title = title or f"{started_by_name} - {now}"
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO meetings(title, date, started_by_name, started_by_id, language, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (final_title, now, started_by_name, started_by_id, language, MeetingStatus.CREATED, now),
            )
            return int(cursor.lastrowid)

    def get_or_create_participant(self, meeting_id: int, diarization_speaker_id: str) -> int:
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM participants WHERE meeting_id = ? AND diarization_speaker_id = ?",
                (meeting_id, diarization_speaker_id),
            ).fetchone()
            if existing:
                return int(existing["id"])
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM participants WHERE meeting_id = ?", (meeting_id,)
            ).fetchone()["c"]
            label = f"Kullanıcı {count + 1}"
            cursor = conn.execute(
                "INSERT INTO participants(meeting_id, diarization_speaker_id, label, talk_time_sec) VALUES (?, ?, ?, 0)",
                (meeting_id, diarization_speaker_id, label),
            )
            return int(cursor.lastrowid)

    def get_meeting(self, meeting_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            return dict(row) if row else None

    def list_meetings(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM meetings ORDER BY id DESC").fetchall()
            return [dict(row) for row in rows]

    def abandoned_candidates(self, updated_before: str) -> list[dict]:
        active_statuses = (
            MeetingStatus.CREATED,
            MeetingStatus.RECORDING,
            MeetingStatus.RECORDED,
            MeetingStatus.REFINE_FAILED,
            MeetingStatus.SUMMARY_PENDING,
            MeetingStatus.SUMMARY_FAILED,
        )
        placeholders = ','.join('?' for _ in active_statuses)
        with self.db.connect() as conn:
            rows = conn.execute(
                f'''
                SELECT * FROM meetings
                WHERE status IN ({placeholders}) AND updated_at < ?
                ORDER BY id
                ''',
                (*active_statuses, updated_before),
            ).fetchall()
            return [dict(row) for row in rows]

    def participants(self, meeting_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM participants WHERE meeting_id = ? ORDER BY id", (meeting_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def participant(self, meeting_id: int, participant_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE meeting_id = ? AND id = ?",
                (meeting_id, participant_id),
            ).fetchone()
            return dict(row) if row else None

    def update_status(self, meeting_id: int, status: MeetingStatus) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE meetings SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), meeting_id),
            )

    def set_duration(self, meeting_id: int, duration_sec: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE meetings SET duration_sec = ?, updated_at = ? WHERE id = ?",
                (duration_sec, utc_now_iso(), meeting_id),
            )

    def mark_runtime_purged(self, meeting_id: int, status: MeetingStatus) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE meetings SET runtime_purged_at = ?, status = ?, updated_at = ? WHERE id = ?",
                (utc_now_iso(), status, utc_now_iso(), meeting_id),
            )


class SegmentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def replace_final_segments(self, meeting_id: int, segments: Iterable[Segment]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM segments WHERE meeting_id = ? AND status = 'final'", (meeting_id,)
            )
            conn.executemany(
                """
                INSERT INTO segments(
                  meeting_id, segment_id, revision, status, start_sec, end_sec,
                  diarization_speaker_id, participant_id, transcript_confidence, overlap, text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.meeting_id,
                        s.segment_id,
                        s.revision,
                        s.status,
                        s.start_sec,
                        s.end_sec,
                        s.diarization_speaker_id,
                        s.participant_id,
                        s.transcript_confidence,
                        int(s.overlap),
                        s.text,
                    )
                    for s in segments
                ],
            )

    def replace_final_segments_with_labels(
        self,
        meeting_id: int,
        segments: Iterable[Segment],
        preferred_labels: dict[str, str] | None = None,
        reserved_labels: set[str] | None = None,
    ) -> list[Segment]:
        source = list(segments)
        preferred_labels = preferred_labels or {}
        resolved: list[Segment] = []
        speaker_participants: dict[str, int] = {}
        talk_times: dict[int, float] = {}
        reserved_labels = set(reserved_labels or ()) | set(
            preferred_labels.values()
        )
        used_labels: set[str] = set()
        next_label_number = 1

        def choose_label(speaker_id: str) -> str:
            nonlocal next_label_number
            preferred = preferred_labels.get(speaker_id)
            if preferred and preferred not in used_labels:
                used_labels.add(preferred)
                return preferred
            while True:
                candidate = f'Kullanıcı {next_label_number}'
                next_label_number += 1
                if candidate not in used_labels and candidate not in reserved_labels:
                    used_labels.add(candidate)
                    return candidate

        with self.db.connect() as conn:
            conn.execute('DELETE FROM segments WHERE meeting_id = ?', (meeting_id,))
            conn.execute('DELETE FROM participants WHERE meeting_id = ?', (meeting_id,))

            for segment in source:
                participant_id = None
                speaker_id = segment.diarization_speaker_id
                if speaker_id:
                    participant_id = speaker_participants.get(speaker_id)
                    if participant_id is None:
                        label = choose_label(speaker_id)
                        cursor = conn.execute(
                            '''
                            INSERT INTO participants(
                              meeting_id, diarization_speaker_id, label, talk_time_sec
                            ) VALUES (?, ?, ?, 0)
                            ''',
                            (meeting_id, speaker_id, label),
                        )
                        participant_id = int(cursor.lastrowid)
                        speaker_participants[speaker_id] = participant_id
                    talk_times[participant_id] = talk_times.get(participant_id, 0.0) + max(
                        0.0, segment.end_sec - segment.start_sec
                    )
                resolved.append(replace(segment, participant_id=participant_id))

            conn.executemany(
                '''
                INSERT INTO segments(
                  meeting_id, segment_id, revision, status, start_sec, end_sec,
                  diarization_speaker_id, participant_id, transcript_confidence, overlap, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                [
                    (
                        s.meeting_id,
                        s.segment_id,
                        s.revision,
                        s.status,
                        s.start_sec,
                        s.end_sec,
                        s.diarization_speaker_id,
                        s.participant_id,
                        s.transcript_confidence,
                        int(s.overlap),
                        s.text,
                    )
                    for s in resolved
                ],
            )
            conn.executemany(
                'UPDATE participants SET talk_time_sec = ? WHERE id = ?',
                [(duration, participant_id) for participant_id, duration in talk_times.items()],
            )
        return resolved

    def latest_segments(self, meeting_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, p.label AS participant_label
                FROM segments s
                LEFT JOIN participants p ON p.id = s.participant_id
                WHERE s.meeting_id = ?
                ORDER BY s.start_sec, s.segment_id, s.revision
                """,
                (meeting_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def search(self, query: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM segments_fts f
                JOIN segments s ON s.rowid = f.rowid
                WHERE segments_fts MATCH ?
                  AND s.revision = (
                    SELECT MAX(newest.revision)
                    FROM segments newest
                    WHERE newest.meeting_id = s.meeting_id
                      AND newest.segment_id = s.segment_id
                  )
                ORDER BY s.meeting_id DESC, s.start_sec
                LIMIT 50
                """,
                (query,),
            ).fetchall()
            return [dict(row) for row in rows]


class SummaryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, meeting_id: int, result: SummaryResult) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO summaries(
                  meeting_id, summary_md, discussed_topics_json,
                  decisions_json, actions_json, open_questions_json,
                  open_topics_json, next_topics_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                  summary_md = excluded.summary_md,
                  discussed_topics_json = excluded.discussed_topics_json,
                  decisions_json = excluded.decisions_json,
                  actions_json = excluded.actions_json,
                  open_questions_json = excluded.open_questions_json,
                  open_topics_json = excluded.open_topics_json,
                  next_topics_json = excluded.next_topics_json
                """,
                (
                    meeting_id,
                    result.summary_md,
                    json.dumps(result.discussed_topics, ensure_ascii=False),
                    json.dumps(result.decisions, ensure_ascii=False),
                    json.dumps(result.actions, ensure_ascii=False),
                    json.dumps(result.open_questions, ensure_ascii=False),
                    # Eski istemciler için açık soruları eski alanda da tut.
                    json.dumps(result.open_questions, ensure_ascii=False),
                    '[]',
                ),
            )

    def get(self, meeting_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,)).fetchone()
            if row is None:
                return None
            payload = dict(row)
            open_questions = json.loads(
                payload.get('open_questions_json')
                or payload.get('open_topics_json')
                or '[]'
            )
            return {
                'meeting_id': payload['meeting_id'],
                'summary_md': payload['summary_md'],
                'discussed_topics': json.loads(
                    payload.get('discussed_topics_json') or '[]'
                ),
                'decisions': json.loads(payload.get('decisions_json') or '[]'),
                'actions': json.loads(payload.get('actions_json') or '[]'),
                'open_questions': open_questions,
            }
