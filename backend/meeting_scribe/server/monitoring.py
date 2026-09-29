"""Process-local, content-free operational metrics. No audio, tokens or transcripts."""
from __future__ import annotations

import csv
import math
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def sample_gpus():
    """Bounded read-only query; unsupported/missing GPU tools are not fatal."""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,uuid,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3, check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        devices = []
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) != 6:
                continue
            devices.append(dict(index=row[0].strip(), name=row[1].strip(), uuid=row[2].strip(),
                                utilization_pct=number(row[3]), used_mib=number(row[4]),
                                total_mib=number(row[5])))
        return {'sampled_at': utc_now(), 'devices': devices, 'error': None if devices else 'no_devices'}
    except (OSError, subprocess.SubprocessError):
        return {'sampled_at': utc_now(), 'devices': [], 'error': 'gpu_metrics_unavailable'}


class LiveMonitor:
    def __init__(self):
        self._lock = threading.RLock()
        self._active = {}
        self._recent = deque(maxlen=100)
        self._gpu = {'sampled_at': None, 'devices': [], 'error': 'not_sampled'}
        self.started_at = utc_now()

    def start(self, meeting_id, language):
        session_id = uuid.uuid4().hex
        with self._lock:
            self._active[session_id] = dict(
                session_id=session_id, meeting_id=meeting_id, started_at=utc_now(),
                state='connecting', browser_connection='connected', language=language, detected_language=None,
                received_sec=0.0, processed_sec=None, finalized_sec=None, diarized_sec=None,
                last_audio_at=None, last_audio_clock=None, last_whisper_at=None,
                last_provisional_at=None, last_final_at=None, last_forwarded_at=None,
                whisper_state='connecting', diarization_state='connecting',
                queue_ms=None, inference_ms=None, request_id=None, worker=None,
                transcript_queue=0, diarization_queue=0, diarization_dropped_frames=0,
                error=None, telemetry=False,
            )
        return session_id

    def update(self, session_id, **values):
        with self._lock:
            row = self._active.get(session_id)
            if row is not None:
                row.update(values)

    def audio(self, session_id, received_sec):
        self.update(session_id, received_sec=received_sec, last_audio_at=utc_now(),
                    last_audio_clock=time.monotonic(), state='streaming')

    def telemetry(self, session_id, payload):
        # Explicit allowlist: never retain arbitrary peer fields or error messages.
        with self._lock:
            row = self._active.get(session_id)
            if row is None:
                return
            if payload.get('phase') in {'queued', 'running', 'completed', 'error'}:
                row['whisper_state'] = payload['phase']
            row['last_whisper_at'] = utc_now()
            row['telemetry'] = True
            for key in ('queue_ms', 'inference_ms', 'request_id'):
                if key in payload:
                    row[key] = number(payload[key])
            if payload.get('phase') == 'completed':
                end = number(payload.get('processed_until_sec'))
                if end is not None:
                    row['processed_sec'] = max(row['processed_sec'] or 0, end)
            lang = payload.get('detected_language')
            if isinstance(lang, str) and lang.isalpha() and len(lang) <= 12:
                row['detected_language'] = lang
            worker = payload.get('worker')
            if isinstance(worker, str):
                row['worker'] = worker[:120]
            if payload.get('phase') == 'error':
                row['error'] = 'whisper_inference_failed'

    def transcript(self, session_id, status, end_sec):
        with self._lock:
            row = self._active.get(session_id)
            if row is None:
                return
            row['last_whisper_at'] = utc_now()
            if status == 'provisional':
                row['last_provisional_at'] = utc_now()
            elif status == 'final':
                row['last_final_at'] = utc_now()
                end = number(end_sec)
                if end is not None:
                    row['finalized_sec'] = max(row['finalized_sec'] or 0, end)

    def close(self, session_id, error=None):
        with self._lock:
            row = self._active.pop(session_id, None)
            if row:
                row.update(state='closed', browser_connection='closed', ended_at=utc_now())
                if error:
                    row['error'] = error
                self._recent.append(row)

    def set_gpu(self, value):
        with self._lock:
            self._gpu = value

    def snapshot(self):
        with self._lock:
            def expose(row):
                result = {k: v for k, v in row.items() if k != 'last_audio_clock'}
                result['audio_idle_sec'] = (
                    round(max(0, time.monotonic() - row['last_audio_clock']), 2)
                    if row['last_audio_clock'] is not None else None)
                if row['browser_connection'] != 'connected':
                    result['audio_stream'] = 'disconnected'
                elif row['last_audio_clock'] is None:
                    result['audio_stream'] = 'waiting'
                elif result['audio_idle_sec'] <= 3:
                    result['audio_stream'] = 'receiving'
                else:
                    result['audio_stream'] = 'stalled'
                result['lag_sec'] = (round(max(0, row['received_sec'] - row['processed_sec']), 3)
                                     if row['processed_sec'] is not None else None)
                return result
            active = [expose(row) for row in self._active.values()]
            return dict(
                generated_at=utc_now(), process_id=os.getpid(), started_at=self.started_at,
                scope='single_backend_process', sessions=active,
                recent_sessions=[expose(row) for row in reversed(self._recent)],
                waiting_whisper=sum(row['whisper_state'] == 'queued' for row in active),
                running_whisper=sum(row['whisper_state'] == 'running' for row in active),
                gpu=self._gpu,
            )
