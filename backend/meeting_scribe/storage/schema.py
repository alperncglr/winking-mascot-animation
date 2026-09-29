from __future__ import annotations

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meetings (
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

CREATE TABLE IF NOT EXISTS participants (
  id INTEGER PRIMARY KEY,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  diarization_speaker_id TEXT NOT NULL,
  label TEXT NOT NULL,
  talk_time_sec REAL DEFAULT 0,
  UNIQUE (meeting_id, diarization_speaker_id)
);

CREATE TABLE IF NOT EXISTS segments (
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
);

CREATE TABLE IF NOT EXISTS summaries (
  meeting_id INTEGER PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
  summary_md TEXT NOT NULL,
  discussed_topics_json TEXT NOT NULL DEFAULT '[]',
  decisions_json TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  open_questions_json TEXT NOT NULL DEFAULT '[]',
  open_topics_json TEXT NOT NULL DEFAULT '[]',
  next_topics_json TEXT NOT NULL DEFAULT '[]'
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
  text,
  meeting_id UNINDEXED,
  segment_id UNINDEXED,
  revision UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segments_fts(rowid, text, meeting_id, segment_id, revision)
  VALUES (new.rowid, new.text, new.meeting_id, new.segment_id, new.revision);
END;

CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text, meeting_id, segment_id, revision)
  VALUES('delete', old.rowid, old.text, old.meeting_id, old.segment_id, old.revision);
END;

CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text, meeting_id, segment_id, revision)
  VALUES('delete', old.rowid, old.text, old.meeting_id, old.segment_id, old.revision);
  INSERT INTO segments_fts(rowid, text, meeting_id, segment_id, revision)
  VALUES (new.rowid, new.text, new.meeting_id, new.segment_id, new.revision);
END;
"""
