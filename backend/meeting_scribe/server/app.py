from __future__ import annotations

import asyncio
import logging
import os
import secrets
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

from meeting_scribe.domain.models import MeetingStatus
from meeting_scribe.server.dependencies import AppContainer, build_container
from meeting_scribe.server.schemas import (
    ImportWavRequest,
    MeetingCreate,
    RecordSecondsRequest,
    SearchRequest,
)
from meeting_scribe.server.ws_tokens import OneTimeWebSocketTokenStore
from meeting_scribe.server.monitoring import LiveMonitor, sample_gpus, utc_now, number
from meeting_scribe.server.live_connection import receive_audio, run_input_until_closed
from meeting_scribe.server.live_session_gate import LiveSessionGate
from meeting_scribe.log_context import install_log_context, meeting_log_context
from meeting_scribe.services.capture.recorder import (
    CaptureDependencyError,
    ChunkRecorder,
    NetworkChunkWriter,
    create_silent_chunk,
    import_wav_as_single_chunk,
    resolve_staged_wav_path,
)
from meeting_scribe.services.capture.manifest import Manifest
from meeting_scribe.services.live.adapters import PlaceholderLiveDiarizer, build_streaming_transcriber
from meeting_scribe.services.live.engine import LiveEngine
from meeting_scribe.services.live.diarization import (
    LiveSpeakerTracker,
    LiveTranscriptGroup,
)
from meeting_scribe.services.offline.pipeline import (
    OfflinePipeline,
    get_cached_diarizer,
    get_cached_transcriber,
)
from meeting_scribe.services.runtime_cleanup import RuntimePurger
from meeting_scribe.services.summarize import SummaryClient, write_summary


load_dotenv(Path(__file__).resolve().parents[2] / '.env')
# Önceden hiçbir seviye ayarlanmamıştı; log.info(...) çağrıları journalctl'de
# hiç görünmüyordu (yalnızca log.exception gibi WARNING üstü seviyeler
# lastResort handler'la basılıyordu). Transkript/özet üretim ilerlemesini
# görebilmek için INFO seviyesi açıkça açılıyor.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s: %(message)s')
log = logging.getLogger('meeting-scribe')
install_log_context()


def create_app(container: Optional[AppContainer] = None) -> FastAPI:
    c = container or build_container()
    app = FastAPI(title='T3AI DEFTER API')
    monitor = LiveMonitor()
    app.state.live_monitor = monitor
    admin_key = os.getenv('MEETING_SCRIBE_ADMIN_KEY', '')

    # Frontend (deft3r-notebook) ayrı bir portta/origin'de çalıştığından
    # tarayıcı istekleri cross-origin sayılıyor; CORS_ALLOW_ORIGINS env'i
    # virgülle ayrılmış origin listesi olarak verilebilir, yoksa tümüne açık.
    cors_origins_raw = os.getenv('CORS_ALLOW_ORIGINS', '*')
    cors_origins = (
        ['*'] if cors_origins_raw.strip() == '*'
        else [origin.strip() for origin in cors_origins_raw.split(',') if origin.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    live_session_gate = LiveSessionGate()
    websocket_tokens = OneTimeWebSocketTokenStore(ttl_seconds=60.0)
    api_key = os.getenv(c.settings.auth.api_key_env)
    loopback_hosts = {'127.0.0.1', 'localhost', '::1'}
    if c.settings.app.host not in loopback_hosts and not api_key:
        raise RuntimeError(
            f'{c.settings.auth.api_key_env} is required when binding outside loopback'
        )

    @app.middleware('http')
    async def require_api_key(request: Request, call_next):
        if request.url.path.rstrip('/') == '/admin/monitor':
            if len(admin_key) < 32:
                return JSONResponse(status_code=503, content={'detail': 'Admin monitoring is not configured'}, headers={'Cache-Control': 'no-store'})
            supplied = request.headers.get('x-admin-key', '')
            if not secrets.compare_digest(supplied.encode(), admin_key.encode()):
                return JSONResponse(status_code=401, content={'detail': 'Unauthorized'}, headers={'Cache-Control': 'no-store'})
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            return response
        if request.url.path not in {'/', '/health'} and api_key:
            supplied = request.headers.get('x-api-key', '')
            if not secrets.compare_digest(supplied, api_key):
                return JSONResponse(status_code=401, content={'detail': 'Unauthorized'})
        return await call_next(request)

    @app.get('/admin/monitor')
    def admin_monitor() -> dict:
        snapshot = monitor.snapshot()
        meetings = c.meetings.list_meetings()
        by_id = {item['id']: item for item in meetings}
        for row in snapshot['sessions'] + snapshot['recent_sessions']:
            meeting = by_id.get(row['meeting_id'], {})
            row['title'] = meeting.get('title')
            row['meeting_status'] = meeting.get('status')
        # Database state, NOT a durable job queue or proof of a running worker.
        groups = {
            'active_meetings': {'recording'},
            'processing_meetings': {'recorded', 'refining', 'refined', 'summarizing', 'summary_pending'},
            'completed_meetings': {'summarized', 'finished'},
            'failed_meetings': {'recording_failed', 'refine_failed', 'summary_failed'},
        }
        for name, statuses in groups.items():
            snapshot[name] = [
                {key: item.get(key) for key in ('id', 'title', 'status', 'updated_at')}
                for item in meetings if item.get('status') in statuses
            ][:100]
        return snapshot

    async def monitor_gpu_loop():
        while True:
            monitor.set_gpu(await asyncio.to_thread(sample_gpus))
            await asyncio.sleep(5)

    async def cleanup_loop() -> None:
        interval = max(1, c.settings.runtime_cleanup.scan_interval_minutes) * 60
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(RuntimePurger(c.settings, c.meetings).purge_abandoned)

    async def warm_offline_models() -> None:
        # Whisper/pyannote modelleri ilk toplantı biter bitmez değil, sunucu
        # açılışında (GPU henüz boşken) yüklensin diye önceden ısıtılıyor.
        try:
            await asyncio.to_thread(get_cached_transcriber, c.settings)
            await asyncio.to_thread(get_cached_diarizer, c.settings)
            log.info('Offline whisper/pyannote modelleri önceden yüklendi.')
        except Exception:
            log.exception('Offline model ön yükleme başarısız oldu; ilk toplantı bitişinde tekrar denenecek.')

    @app.on_event('startup')
    async def start_cleanup_loop() -> None:
        app.state.cleanup_task = asyncio.create_task(cleanup_loop())
        app.state.model_warmup_task = asyncio.create_task(warm_offline_models())
        app.state.monitor_gpu_task = asyncio.create_task(monitor_gpu_loop())

    @app.on_event('shutdown')
    async def stop_cleanup_loop() -> None:
        gpu_task = getattr(app.state, 'monitor_gpu_task', None)
        if gpu_task:
            gpu_task.cancel()
            try:
                await gpu_task
            except asyncio.CancelledError:
                pass
        task = getattr(app.state, 'cleanup_task', None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        ui_path = Path(__file__).parent / "templates" / "index.html"
        return ui_path.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict:
        return {
            'ok': True,
            'whisper_backend': c.settings.offline.whisper_backend,
            'diarizer_backend': c.settings.offline.diarizer_backend,
            'live_diarizer_backend': c.settings.live.diarization_backend,
        }

    @app.post('/auth/websocket-token')
    def create_websocket_token() -> dict:
        return {
            'token': websocket_tokens.issue(),
            'expires_in_seconds': 60,
        }

    @app.post("/meetings")
    def create_meeting(
        payload: MeetingCreate,
        request: Request,
    ) -> dict:
        started_by_name = request.headers.get(c.settings.auth.trusted_name_header)
        started_by_id = request.headers.get(c.settings.auth.trusted_id_header)
        meeting_id = c.meetings.create_meeting(
            title=payload.title,
            started_by_name=started_by_name or c.settings.auth.dev_started_by_name,
            started_by_id=started_by_id or c.settings.auth.dev_started_by_id,
            language=payload.language,
        )
        c.paths.runtime_dir(meeting_id).mkdir(parents=True, exist_ok=True)
        c.paths.archive_dir(meeting_id).mkdir(parents=True, exist_ok=True)
        return {"meeting_id": meeting_id}

    @app.get("/meetings")
    def list_meetings() -> list[dict]:
        return c.meetings.list_meetings()

    @app.get("/meetings/{meeting_id}")
    def get_meeting(meeting_id: int) -> dict:
        meeting = c.meetings.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        meeting["participants"] = c.meetings.participants(meeting_id)
        meeting["segments"] = c.segments.latest_segments(meeting_id)
        meeting["summary"] = c.summaries.get(meeting_id)
        return meeting

    @app.get("/meetings/{meeting_id}/transcript")
    def get_transcript(meeting_id: int) -> dict:
        meeting = _require_meeting(c, meeting_id)
        transcript_path = c.paths.final_transcript_path(meeting_id)
        if not transcript_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Transcript not ready yet (status: {meeting['status']})",
            )
        return {"transcript": transcript_path.read_text(encoding="utf-8")}

    @app.post("/meetings/{meeting_id}/recording/start")
    def start_recording(meeting_id: int) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(
            meeting,
            {MeetingStatus.CREATED, MeetingStatus.RECORDED, MeetingStatus.RECORDING_FAILED},
        )
        c.paths.audio_dir(meeting_id).mkdir(parents=True, exist_ok=True)
        c.meetings.update_status(meeting_id, MeetingStatus.RECORDING)
        return {"status": MeetingStatus.RECORDING}

    @app.post("/meetings/{meeting_id}/recording/record-seconds")
    def record_seconds(meeting_id: int, payload: RecordSecondsRequest) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(meeting, {MeetingStatus.RECORDING})
        recorder = ChunkRecorder(c.settings.capture, c.paths.audio_dir(meeting_id), c.paths.manifest_path(meeting_id))
        try:
            recorder.record_for_seconds(payload.seconds)
        except CaptureDependencyError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return {"recorded_seconds": payload.seconds}

    @app.post("/meetings/{meeting_id}/recording/import-wav")
    def import_wav(meeting_id: int, payload: ImportWavRequest) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(meeting, {MeetingStatus.CREATED, MeetingStatus.RECORDING})
        try:
            source_path = resolve_staged_wav_path(
                payload.source_path, c.settings.paths.runtime_root
            )
            import_wav_as_single_chunk(
                source_path,
                c.paths.audio_dir(meeting_id),
                c.paths.manifest_path(meeting_id),
            )
        except (OSError, ValueError, EOFError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        c.meetings.update_status(meeting_id, MeetingStatus.RECORDED)
        return {"manifest": str(c.paths.manifest_path(meeting_id))}

    @app.post("/meetings/{meeting_id}/recording/create-silent")
    def silent(meeting_id: int, payload: RecordSecondsRequest) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(meeting, {MeetingStatus.CREATED, MeetingStatus.RECORDING})
        create_silent_chunk(
            c.paths.audio_dir(meeting_id),
            c.paths.manifest_path(meeting_id),
            c.settings.capture.sample_rate,
            payload.seconds,
        )
        c.meetings.update_status(meeting_id, MeetingStatus.RECORDED)
        return {"manifest": str(c.paths.manifest_path(meeting_id))}

    @app.websocket('/meetings/{meeting_id}/live/ws')
    async def live_ws(websocket: WebSocket, meeting_id: int) -> None:
        if api_key:
            supplied = websocket.query_params.get('ws_token', '')
            if not websocket_tokens.consume(supplied):
                await websocket.close(code=4401)
                return
        meeting = c.meetings.get_meeting(meeting_id)
        if not meeting:
            await websocket.close(code=4004)
            return
        if meeting['status'] not in {
            MeetingStatus.CREATED,
            MeetingStatus.RECORDING,
            MeetingStatus.RECORDED,
            MeetingStatus.RECORDING_FAILED,
        }:
            await websocket.close(code=4409)
            return
        # A paused browser closes its socket immediately, while Whisper and the
        # diarizer may still be finalizing that session. Wait for their cleanup
        # before accepting a resume instead of rejecting it as a duplicate.
        if not await live_session_gate.acquire(meeting_id):
            await websocket.close(code=1013)
            return
        meeting = c.meetings.get_meeting(meeting_id)
        if not meeting or meeting['status'] not in {
            MeetingStatus.CREATED,
            MeetingStatus.RECORDING,
            MeetingStatus.RECORDED,
            MeetingStatus.RECORDING_FAILED,
        }:
            live_session_gate.release(meeting_id)
            await websocket.close(code=4409)
            return

        def release_live_session() -> None:
            live_session_gate.release(meeting_id)

        try:
            await websocket.accept()
        except Exception:
            release_live_session()
            raise

        def resolve_label(speaker_id: str) -> str:
            participant_id = c.meetings.get_or_create_participant(meeting_id, speaker_id)
            participant = c.meetings.participant(meeting_id, participant_id)
            return participant["label"] if participant else speaker_id

        live_cfg = c.settings.live
        if live_cfg.backend == 'remote_vosk_ws':
            try:
                session_id = monitor.start(meeting_id, meeting['language'])
            except Exception:
                release_live_session()
                raise
            session_error = None
            try:
                with meeting_log_context(meeting_id, session_id):
                    log.info('Live session started')
                    try:
                        await _run_remote_vosk_live(websocket, c, meeting_id, meeting, monitor, session_id)
                    except Exception:
                        log.exception('Live session failed')
                        raise
                    finally:
                        log.info('Live session ended')
            except Exception:
                session_error = 'live_session_failed'
                raise
            finally:
                try:
                    monitor.close(session_id, session_error)
                finally:
                    release_live_session()
            return
        try:
            engine = LiveEngine(
                transcriber=build_streaming_transcriber(live_cfg.backend, live_cfg.whisper_model),
                diarizer=PlaceholderLiveDiarizer(),
                resolve_participant_label=resolve_label,
                sample_rate=live_cfg.sample_rate,
                window_seconds=live_cfg.window_seconds,
                language=None if meeting['language'] == 'mixed' else meeting['language'],
                silence_rms_threshold=live_cfg.silence_rms_threshold,
                silence_flush_seconds=live_cfg.silence_flush_seconds,
                min_decode_seconds=live_cfg.min_decode_seconds,
            )
            live_path = c.paths.live_transcript_path(meeting_id)
            live_path.parent.mkdir(parents=True, exist_ok=True)
            chunk_writer = NetworkChunkWriter(
                c.paths.audio_dir(meeting_id),
                c.paths.manifest_path(meeting_id),
                c.settings.capture.sample_rate,
                c.settings.capture.chunk_seconds,
            )
            c.meetings.update_status(meeting_id, MeetingStatus.RECORDING)
        except Exception:
            release_live_session()
            raise

        try:
            while True:
                pcm_chunk = await websocket.receive_bytes()
                chunk_writer.push(pcm_chunk)
                event = await asyncio.to_thread(engine.push_audio, pcm_chunk)
                if event is None:
                    continue
                payload = asdict(event)
                with live_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            try:
                chunk_writer.finalize()
                current = c.meetings.get_meeting(meeting_id)
                if current and current['status'] == MeetingStatus.RECORDING:
                    c.meetings.update_status(meeting_id, MeetingStatus.RECORDED)
            finally:
                release_live_session()

    @app.post("/meetings/{meeting_id}/recording/stop")
    def stop_recording(meeting_id: int, background_tasks: BackgroundTasks) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(meeting, {MeetingStatus.RECORDING, MeetingStatus.RECORDED})
        if live_session_gate.is_active(meeting_id):
            raise HTTPException(
                status_code=409,
                detail='Live audio is still finalizing; retry shortly',
            )
        chunks = Manifest(c.paths.manifest_path(meeting_id)).read_all()
        if not chunks:
            raise HTTPException(
                status_code=409,
                detail=(
                    'No audio was captured. Grant microphone permission, start '
                    'the live stream, and speak before stopping the recording.'
                ),
            )
        duration = max(
            (chunk.start_sec + chunk.duration_sec for chunk in chunks),
            default=0.0,
        )
        c.meetings.set_duration(meeting_id, round(duration))
        c.meetings.update_status(meeting_id, MeetingStatus.RECORDED)
        background_tasks.add_task(_run_offline, c, meeting_id)
        return {"status": MeetingStatus.RECORDED, "offline": "queued"}

    @app.post("/meetings/{meeting_id}/offline/run")
    def run_offline_now(meeting_id: int) -> dict:
        meeting = _require_meeting(c, meeting_id)
        _require_status(meeting, {MeetingStatus.RECORDED, MeetingStatus.REFINE_FAILED})
        final_path = OfflinePipeline(c.settings, c.meetings, c.segments).run(meeting_id)
        return {"final_transcript": str(final_path)}

    @app.post("/meetings/{meeting_id}/summary")
    def summarize(meeting_id: int) -> dict:
        # Kayıt durunca özet zaten otomatik üretilmeye başlıyor (bkz. _run_offline);
        # bu endpoint artık çoğunlukla "otomatik üretim başarısız oldu, tekrar dene"
        # senaryosu için manuel bir tetikleyici olarak kalıyor.
        meeting = _require_meeting(c, meeting_id)
        if meeting["status"] not in {MeetingStatus.REFINED, MeetingStatus.SUMMARY_FAILED, MeetingStatus.SUMMARY_PENDING}:
            raise HTTPException(status_code=409, detail="Final transcript is not ready")
        try:
            result = _generate_summary(c, meeting_id)
        except Exception as exc:
            c.meetings.update_status(meeting_id, MeetingStatus.SUMMARY_FAILED)
            raise HTTPException(
                status_code=502,
                detail='Summary service failed; audio was preserved and the request can be retried',
            ) from exc
        return {
            "summary_md": result.summary_md,
            "discussed_topics": result.discussed_topics,
            "decisions": result.decisions,
            "actions": result.actions,
            "open_questions": result.open_questions,
        }

    @app.post("/meetings/{meeting_id}/finish")
    def finish(meeting_id: int) -> dict:
        _require_meeting(c, meeting_id)
        try:
            RuntimePurger(c.settings, c.meetings).purge_after_summary(meeting_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": MeetingStatus.FINISHED}

    @app.post("/admin/meetings/{meeting_id}/force-close")
    def force_close(meeting_id: int) -> dict:
        _require_meeting(c, meeting_id)
        RuntimePurger(c.settings, c.meetings).force_close(meeting_id)
        return {"status": MeetingStatus.FINISHED}

    @app.post("/search")
    def search(payload: SearchRequest) -> list[dict]:
        return c.segments.search(payload.query)

    return app


def _require_meeting(container: AppContainer, meeting_id: int) -> dict:
    meeting = container.meetings.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


def _require_status(meeting: dict, allowed: set[MeetingStatus]) -> None:
    current = meeting['status']
    if current not in allowed:
        expected = ', '.join(sorted(status.value for status in allowed))
        raise HTTPException(
            status_code=409,
            detail=f'Invalid meeting state {current}; expected one of: {expected}',
        )


async def _run_remote_vosk_live(
    websocket: WebSocket,
    container: AppContainer,
    meeting_id: int,
    meeting: dict,
    monitor: LiveMonitor | None = None,
    session_id: str = '',
) -> None:
    def observe(**values):
        if monitor:
            monitor.update(session_id, **values)
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError('Install websockets to use remote_vosk_ws') from exc

    settings = container.settings
    live_path = container.paths.live_transcript_path(meeting_id)
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_diarization_path = container.paths.live_diarization_path(meeting_id)
    writer = NetworkChunkWriter(
        container.paths.audio_dir(meeting_id),
        container.paths.manifest_path(meeting_id),
        settings.capture.sample_rate,
        settings.capture.chunk_seconds,
    )
    session_offset_sec = writer.elapsed_sec
    session_prefix = session_id[:8]

    def meeting_speaker_id(speaker_id: str | None) -> str | None:
        return f'{session_prefix}_{speaker_id}' if speaker_id else None

    config = {
        'meeting_id': meeting_id,
        'session_id': session_id,
        'telemetry': True,
        'sample_rate': settings.capture.sample_rate,
        'retry_low_confidence': True,
        'chunk_seconds': settings.live.whisper_chunk_seconds,
        'context_seconds': settings.live.whisper_context_seconds,
        'live_metadata': True,
        'word_timestamps': True,
        'silence_rms_threshold': settings.live.silence_rms_threshold,
        'silence_flush_seconds': settings.live.silence_flush_seconds,
        'stability_holdback_seconds': 0.6,
        'language': None if meeting['language'] == 'mixed' else meeting['language'],
        'task': 'transcribe',
    }
    container.meetings.update_status(meeting_id, MeetingStatus.RECORDING)
    speaker_tracker = LiveSpeakerTracker()
    diarization_audio: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
    audio_elapsed_sec = 0.0
    dropped_frames = 0
    client_disconnected = asyncio.Event()
    last_ack = 0.0
    browser_send_lock = asyncio.Lock()

    async def send_browser(payload: dict) -> None:
        async with browser_send_lock:
            await websocket.send_json(payload)

    async def run_diarization() -> None:
        if settings.live.diarization_backend != 'remote_diart_ws':
            return
        try:
            async with websockets.connect(
                settings.live.diarization_ws_url,
                open_timeout=settings.offline.whisper_ws_receive_timeout_seconds,
                proxy=None,
                max_size=None,
            ) as diarization_ws:
                # Both the legacy Diart and Nemotron protocols accept config frames.
                await diarization_ws.send(json.dumps({'config': {
                    'meeting_id': meeting_id, 'session_id': session_id,
                    'sample_rate': settings.capture.sample_rate,
                }}))
                observe(diarization_state='connected')
                async def receive_predictions() -> None:
                    async for raw in diarization_ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode('utf-8')
                        payload = json.loads(raw)
                        if payload.get('status') == 'error':
                            observe(diarization_state='error', error='diarization_failed')
                        if payload.get('type') == 'diarization':
                            observe(diarized_sec=number(payload.get('processed_until_sec')),
                                    diarization_state='processing')
                            stored_payload = {
                                **payload,
                                'processed_until_sec': round(
                                    (number(payload.get('processed_until_sec')) or 0.0) + session_offset_sec, 3
                                ),
                                'turns': [
                                    {
                                        **turn,
                                        'speaker_id': meeting_speaker_id(turn.get('speaker_id')),
                                        'start_sec': round((number(turn.get('start_sec')) or 0.0) + session_offset_sec, 3),
                                        'end_sec': round((number(turn.get('end_sec')) or 0.0) + session_offset_sec, 3),
                                    }
                                    for turn in payload.get('turns') or []
                                ],
                            }
                            with live_diarization_path.open(
                                'a',
                                encoding='utf-8',
                            ) as handle:
                                handle.write(
                                    json.dumps(stored_payload, ensure_ascii=False) + '\n'
                                )
                            await speaker_tracker.update(payload)

                receiver = asyncio.create_task(receive_predictions())
                try:
                    while True:
                        pcm = await diarization_audio.get()
                        observe(diarization_queue=diarization_audio.qsize())
                        if pcm is None:
                            await diarization_ws.send(json.dumps({'eof': 1}))
                            break
                        await diarization_ws.send(pcm)
                    await asyncio.wait_for(
                        receiver,
                        timeout=settings.live.diarization_wait_seconds,
                    )
                except asyncio.TimeoutError:
                    observe(diarization_state='timeout', error='diarization_timeout')
                    log.warning(
                        'Live diarization receiver timed out: meeting_id=%s',
                        meeting_id,
                    )
                    receiver.cancel()
                except Exception:
                    observe(diarization_state='error', error='diarization_stream_failed')
                    log.exception(
                        'Live diarization stream failed: meeting_id=%s',
                        meeting_id,
                    )
                    receiver.cancel()
        except Exception:
            observe(diarization_state='error', error='diarization_connection_failed')
            log.exception(
                'Could not connect to live diarization: meeting_id=%s url=%s',
                meeting_id,
                settings.live.diarization_ws_url,
            )
            return

    diarization_task = asyncio.create_task(run_diarization())

    try:
        async with websockets.connect(
            settings.offline.whisper_ws_url,
            open_timeout=settings.offline.whisper_ws_receive_timeout_seconds,
            proxy=None,
            max_size=None,
            ping_interval=None,
        ) as whisper_ws:
            observe(whisper_state='connected')
            await whisper_ws.send(json.dumps({'config': config}))

            transcript_messages: asyncio.Queue[dict | None] = asyncio.Queue()

            async def receive_transcripts() -> None:
                nonlocal audio_elapsed_sec
                while True:
                    raw = await whisper_ws.recv()
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    payload = json.loads(raw)
                    if payload.get('type') == 'telemetry':
                        if monitor:
                            monitor.telemetry(session_id, payload)
                        continue
                    if payload.get('type') == 'status':
                        continue
                    text = str(payload.get('text') or '').strip()
                    end_sec = float(
                        payload.get('end_sec', audio_elapsed_sec)
                    )
                    start_sec = float(
                        payload.get(
                            'start_sec',
                            max(
                                0.0,
                                end_sec - settings.live.whisper_chunk_seconds,
                            ),
                        )
                    )
                    if text:
                        if monitor:
                            monitor.transcript(session_id, str(payload.get('status') or 'final'), end_sec)
                        await transcript_messages.put(
                            {
                                'text': text,
                                'start_sec': start_sec,
                                'end_sec': end_sec,
                                'words': payload.get('result') or [],
                                'segment_id': str(
                                    payload.get('segment_id') or ''
                                ),
                                'revision': int(payload.get('revision') or 1),
                                'status': str(
                                    payload.get('status') or 'final'
                                ),
                            }
                        )
                        observe(transcript_queue=transcript_messages.qsize())
                    if payload.get('done'):
                        await transcript_messages.put(None)
                        return

            async def forward_transcripts() -> None:
                index = 0
                client_accepts_updates = True
                while True:
                    message = await transcript_messages.get()
                    observe(transcript_queue=transcript_messages.qsize())
                    if message is None:
                        return
                    text = str(message['text'])
                    start_sec = float(message['start_sec'])
                    end_sec = float(message['end_sec'])
                    source_segment_id = str(message.get('segment_id') or '')
                    if not source_segment_id:
                        index += 1
                        source_segment_id = f'live_{index:06d}'
                    source_segment_id = f'{session_prefix}_{source_segment_id}'
                    revision = int(message.get('revision') or 1)
                    status = str(message.get('status') or 'final')
                    if status == 'provisional':
                        speaker_id = speaker_tracker.provisional_speaker(
                            start_sec, end_sec
                        )
                        speaker_id = meeting_speaker_id(speaker_id)
                        if speaker_id:
                            participant_id = (
                                container.meetings.get_or_create_participant(
                                    meeting_id, speaker_id
                                )
                            )
                            participant = container.meetings.participant(
                                meeting_id, participant_id
                            )
                            speaker_label = (
                                participant['label']
                                if participant
                                else speaker_id
                            )
                        else:
                            speaker_label = 'Konuşmacı belirlenemedi'
                        event = {
                            'segment_id': source_segment_id,
                            'revision': revision,
                            'status': 'provisional',
                            'start_sec': start_sec + session_offset_sec,
                            'end_sec': end_sec + session_offset_sec,
                            'diarization_speaker_id': speaker_id,
                            'speaker_label': speaker_label,
                            'overlap': speaker_tracker.has_overlap(
                                start_sec, end_sec
                            ),
                            'text': text,
                        }
                        if client_accepts_updates:
                            try:
                                await send_browser(event)
                                observe(last_forwarded_at=utc_now())
                            except (RuntimeError, WebSocketDisconnect):
                                client_accepts_updates = False
                                client_disconnected.set()
                                observe(browser_connection='closed', error='client_send_failed')
                        continue
                    await speaker_tracker.wait_until_processed(
                        end_sec,
                        settings.live.diarization_wait_seconds,
                    )
                    fallback_speaker_id = speaker_tracker.dominant_speaker(
                        start_sec, end_sec
                    )
                    groups = speaker_tracker.group_words(
                        list(message['words']),
                        fallback_speaker_id=fallback_speaker_id,
                    )
                    if not groups:
                        groups = [
                            LiveTranscriptGroup(
                                speaker_id=fallback_speaker_id,
                                start_sec=start_sec,
                                end_sec=end_sec,
                                text=text,
                                overlap=speaker_tracker.has_overlap(
                                    start_sec, end_sec
                                ),
                            )
                        ]
                    for group_index, group in enumerate(groups):
                        speaker_id = meeting_speaker_id(group.speaker_id)
                        if speaker_id:
                            participant_id = (
                                container.meetings.get_or_create_participant(
                                    meeting_id, speaker_id
                                )
                            )
                            participant = container.meetings.participant(
                                meeting_id, participant_id
                            )
                            speaker_label = (
                                participant['label']
                                if participant
                                else speaker_id
                            )
                        else:
                            speaker_label = 'Konuşmacı belirlenemedi'
                        event = {
                            'segment_id': (
                                source_segment_id
                                if group_index == 0
                                else f'{source_segment_id}_{group_index + 1:02d}'
                            ),
                            'revision': revision,
                            'status': 'final',
                            'start_sec': group.start_sec + session_offset_sec,
                            'end_sec': group.end_sec + session_offset_sec,
                            'diarization_speaker_id': speaker_id,
                            'speaker_label': speaker_label,
                            'overlap': group.overlap,
                            'text': group.text,
                        }
                        with live_path.open('a', encoding='utf-8') as handle:
                            handle.write(
                                json.dumps(event, ensure_ascii=False) + '\n'
                            )
                        if client_accepts_updates:
                            try:
                                await send_browser(event)
                                observe(last_forwarded_at=utc_now())
                            except (RuntimeError, WebSocketDisconnect):
                                # Kullanıcı toplantıyı bitirince tarayıcı
                                # WebSocket'i kapatır. Kalan Whisper sonuçlarını
                                # dosyaya yazmayı sürdür, kapalı istemciye tekrar
                                # göndererek finalizasyonu yarıda kesme.
                                client_accepts_updates = False
                                client_disconnected.set()
                                observe(browser_connection='closed', error='client_send_failed')

            transcript_receiver = asyncio.create_task(receive_transcripts())
            response_task = asyncio.create_task(forward_transcripts())
            async def pump_audio():
                nonlocal audio_elapsed_sec, dropped_frames, last_ack
                while True:
                    pcm_chunk = await receive_audio(websocket)
                    if len(pcm_chunk) > 1024 * 1024:
                        raise ValueError('Audio WebSocket frame exceeds 1 MiB')
                    if len(pcm_chunk) % 2:
                        raise ValueError('Audio WebSocket frame is not valid PCM16')
                    writer.push(pcm_chunk)
                    audio_elapsed_sec += len(pcm_chunk) / (
                        settings.capture.sample_rate * 2
                    )
                    if monitor:
                        monitor.audio(session_id, audio_elapsed_sec)
                    if time.monotonic() - last_ack >= 1:
                        try:
                            await send_browser({'type': 'audio_status', 'received_sec': audio_elapsed_sec})
                        except (RuntimeError, WebSocketDisconnect, OSError) as exc:
                            client_disconnected.set()
                            raise WebSocketDisconnect(code=1006) from exc
                        last_ack = time.monotonic()
                    try:
                        diarization_audio.put_nowait(pcm_chunk)
                    except asyncio.QueueFull:
                        # Count dropped frames; a healthy diarization stream must not hide them.
                        dropped_frames += 1
                        observe(diarization_dropped_frames=dropped_frames, error='diarization_queue_full')
                    observe(diarization_queue=diarization_audio.qsize())
                    await whisper_ws.send(pcm_chunk)
            try:
                await run_input_until_closed(pump_audio, [transcript_receiver, response_task], client_disconnected)
            except WebSocketDisconnect as exc:
                log.info('Browser disconnected close_code=%s', exc.code)
                observe(browser_connection='closed', client_close_code=exc.code)
            except Exception:
                observe(browser_connection='closed', error='live_transport_failed')
                log.exception('Live transport failed')
            finally:
                observe(state='draining', browser_connection='closed')
                try:
                    await whisper_ws.send(json.dumps({'eof': 1}))
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(
                        asyncio.gather(transcript_receiver, response_task),
                        timeout=settings.offline.whisper_ws_receive_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    observe(whisper_state='timeout', error='whisper_finalization_timeout')
                    log.warning(
                        'Live transcript finalization timed out: meeting_id=%s',
                        meeting_id,
                    )
                    transcript_receiver.cancel()
                    response_task.cancel()
                except Exception:
                    observe(whisper_state='error', error='whisper_forwarding_failed')
                    log.exception(
                        'Live transcript forwarding failed: meeting_id=%s',
                        meeting_id,
                    )
                    transcript_receiver.cancel()
                    response_task.cancel()
                await asyncio.gather(transcript_receiver, response_task, return_exceptions=True)
                try:
                    await websocket.close(code=1011 if not client_disconnected.is_set() else 1000)
                except (RuntimeError, WebSocketDisconnect, OSError):
                    pass
    finally:
        try:
            diarization_audio.put_nowait(None)
        except asyncio.QueueFull:
            diarization_task.cancel()
        try:
            await asyncio.wait_for(
                diarization_task,
                timeout=settings.live.diarization_wait_seconds + 2,
            )
        except Exception:
            diarization_task.cancel()
        writer.finalize()
        current = container.meetings.get_meeting(meeting_id)
        if current and current['status'] == MeetingStatus.RECORDING:
            container.meetings.update_status(meeting_id, MeetingStatus.RECORDED)


def _generate_summary(container: AppContainer, meeting_id: int):
    """Transkripti okuyup LLM'den özet ister, sonucu kaydeder ve durumu
    günceller. Hata durumunda exception fırlatır; çağıran taraf bunu HTTP
    hatasına mı çevireceğine yoksa sessizce loglayıp SUMMARY_FAILED'a mı
    geçeceğine kendi karar verir (manuel çağrı vs. otomatik arka plan)."""
    log.info('Özet üretimi başladı: meeting_id=%s', meeting_id)
    container.meetings.update_status(meeting_id, MeetingStatus.SUMMARIZING)
    transcript_path = container.paths.final_transcript_path(meeting_id)
    transcript = transcript_path.read_text(encoding="utf-8")
    client = SummaryClient(container.settings.llm)
    result = client.summarize(transcript)
    container.summaries.upsert(meeting_id, result)
    write_summary(container.paths.summary_path(meeting_id), result)
    container.meetings.update_status(meeting_id, MeetingStatus.SUMMARIZED)
    log.info('Özet üretimi tamamlandı: meeting_id=%s', meeting_id)
    return result


def _run_offline(container: AppContainer, meeting_id: int) -> None:
    with meeting_log_context(meeting_id, f'offline-{meeting_id}'):
        _run_offline_job(container, meeting_id)


def _run_offline_job(container: AppContainer, meeting_id: int) -> None:
    log.info('Döküm (transkript) üretimi başladı: meeting_id=%s', meeting_id)
    try:
        OfflinePipeline(container.settings, container.meetings, container.segments).run(meeting_id)
    except Exception:
        log.exception('Offline refinement failed: meeting_id=%s', meeting_id)
        container.meetings.update_status(meeting_id, MeetingStatus.REFINE_FAILED)
        return
    log.info('Döküm (transkript) üretimi tamamlandı: meeting_id=%s', meeting_id)
    # Transkript hazır olur olmaz özet üretimi de otomatik zincirleniyor;
    # kullanıcı "Özet Al" butonuna basmadan çok önce hazırlanmaya başlar.
    try:
        _generate_summary(container, meeting_id)
    except Exception:
        log.exception('Automatic summary generation failed: meeting_id=%s', meeting_id)
        container.meetings.update_status(meeting_id, MeetingStatus.SUMMARY_FAILED)


app = create_app()
