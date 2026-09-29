import asyncio
import json
import logging
import os
import contextvars
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets
from faster_whisper import WhisperModel

from stabilizer import LiveHypothesisStabilizer


logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('whisper-bridge')
trace_context = contextvars.ContextVar('whisper_trace', default=None)


class TraceFilter(logging.Filter):
    def filter(self, record):
        trace = trace_context.get() or {}
        record.meeting_id = trace.get('meeting_id', '-')
        record.session_id = trace.get('session_id', '-')
        record.request_id = trace.get('request_id', '-')
        return True


for handler in logging.getLogger().handlers:
    handler.addFilter(TraceFilter())
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s meeting_id=%(meeting_id)s session_id=%(session_id)s '
        'request_id=%(request_id)s %(message)s'))


def safe_identifier(value):
    return re.sub(r'[^a-zA-Z0-9_-]', '', str(value or ''))[:64] or '-'


async def measured_inference(websocket, pcm, sample_rate, language, task,
                             word_timestamps, initial_prompt, end_sec):
    trace = trace_context.get()
    trace['request_id'] = trace.get('request_id', 0) + 1
    submitted = time.perf_counter()
    worker = f"{os.getenv('HOSTNAME', 'whisper')}:{os.getpid()}:cuda={os.getenv('CUDA_VISIBLE_DEVICES', 'default')}"

    async def report(phase, **values):
        if trace.get('telemetry'):
            await websocket.send(json.dumps(dict(
                type='telemetry', phase=phase, meeting_id=trace['meeting_id'],
                session_id=trace['session_id'], request_id=trace['request_id'],
                worker=worker, **values)))

    await report('queued')
    try:
        async with inference_slots:
            await report('running', queue_ms=round((time.perf_counter() - submitted) * 1000, 2))
            def run():
                started = time.perf_counter()
                result = transcribe_pcm(pcm, sample_rate, language, task,
                                        word_timestamps, initial_prompt)
                return result, (started - submitted) * 1000, (time.perf_counter() - started) * 1000
            ctx = contextvars.copy_context()
            payload, queue_ms, inference_ms = await asyncio.get_running_loop().run_in_executor(executor, ctx.run, run)
        await report('completed', queue_ms=round(queue_ms, 2),
                     inference_ms=round(inference_ms, 2), processed_until_sec=end_sec,
                     detected_language=payload.get('language'))
        log.info('Inference completed queue_ms=%.2f inference_ms=%.2f processed_until_sec=%s language=%s',
                 queue_ms, inference_ms, end_sec, payload.get('language'))
        return payload
    except Exception:
        await report('error')
        raise

MODEL_NAME = os.environ.get('WHISPER_MODEL', 'large-v3')
DEVICE = os.environ.get('WHISPER_DEVICE', 'cuda')
COMPUTE_TYPE = os.environ.get('WHISPER_COMPUTE', 'float16')
DEFAULT_LANGUAGE = os.environ.get('WHISPER_LANGUAGE', '') or None
PORT = int(os.environ.get('BRIDGE_PORT', '2700'))
CHUNK_SECONDS = float(os.environ.get('CHUNK_SECONDS', '3.0'))

log.info(
    'faster-whisper yukleniyor: model=%s device=%s compute=%s',
    MODEL_NAME,
    DEVICE,
    COMPUTE_TYPE,
)
model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
executor = ThreadPoolExecutor(max_workers=int(os.environ.get('WHISPER_WORKERS', '4')))
inference_slots = asyncio.Semaphore(
    max(1, int(os.environ.get('WHISPER_MAX_CONCURRENT', '1')))
)
log.info('Model yuklendi, baglanti bekleniyor.')


def transcribe_pcm(
    pcm_bytes,
    sample_rate,
    language=None,
    task='transcribe',
    word_timestamps=False,
    initial_prompt=None,
):
    if len(pcm_bytes) < sample_rate * 2 * 0.3:
        return {'text': '', 'result': []}

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0 or float(np.max(np.abs(audio))) < 0.001:
        return {'text': '', 'result': []}

    if sample_rate != 16000:
        target_len = int(len(audio) * 16000 / sample_rate)
        if target_len <= 0:
            return {'text': '', 'result': []}
        audio = np.interp(
            np.linspace(0, len(audio), target_len, endpoint=False),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)

    try:
        segments_iter, info = model.transcribe(
            audio,
            language=language or DEFAULT_LANGUAGE,
            task=task,
            beam_size=1,
            vad_filter=True,
            word_timestamps=word_timestamps,
            initial_prompt=initial_prompt,
        )
        segments = list(segments_iter)
    except ValueError as exc:
        if 'max() arg is an empty sequence' in str(exc):
            log.info('Sessiz/VAD bos audio: transcript yok')
            return {'text': '', 'result': []}
        raise

    text = ' '.join(
        segment.text.strip()
        for segment in segments
        if segment.text and segment.text.strip()
    ).strip()
    words = []
    if word_timestamps:
        for segment in segments:
            for word in segment.words or []:
                token = (word.word or '').strip()
                if not token:
                    continue
                words.append(
                    {
                        'word': token,
                        'start': float(word.start),
                        'end': float(word.end),
                        'conf': float(word.probability),
                    }
                )
    return {
        'text': text,
        'result': words,
        'language': getattr(info, 'language', None),
        'language_probability': float(
            getattr(info, 'language_probability', 0.0) or 0.0
        ),
    }


async def transcribe_buffer(
    websocket,
    buffer,
    sample_rate,
    language,
    word_timestamps,
    task='transcribe',
    live_metadata=False,
    sequence=None,
    start_sec=None,
    end_sec=None,
    done=False,
    context_start_sec=None,
    emit_start_sec=None,
    initial_prompt=None,
    word_offset_sec=0.0,
):
    payload = await measured_inference(websocket, bytes(buffer), sample_rate, language,
                                       task, word_timestamps, initial_prompt, end_sec)
    if live_metadata:
        absolute_offset = float(
            context_start_sec if context_start_sec is not None else start_sec or 0.0
        )
        absolute_words = []
        for item in payload.get('result') or []:
            absolute_word = {
                **item,
                'start': absolute_offset + float(item.get('start', 0.0)),
                'end': absolute_offset
                + float(item.get('end', item.get('start', 0.0))),
            }
            midpoint = (
                float(absolute_word['start']) + float(absolute_word['end'])
            ) / 2
            if emit_start_sec is not None and midpoint < emit_start_sec - 0.05:
                continue
            absolute_words.append(absolute_word)
        emitted_text = (
            ' '.join(str(item.get('word') or '') for item in absolute_words).strip()
            if emit_start_sec is not None
            else payload.get('text') or ''
        )
        wire_payload = {
            'type': 'transcript',
            'sequence': sequence,
            'start_sec': start_sec,
            'end_sec': end_sec,
            'text': emitted_text,
            'result': absolute_words,
            'done': bool(done),
        }
        returned_payload = {
            **payload,
            'text': emitted_text,
            'result': absolute_words,
        }
    elif word_timestamps:
        wire_payload = {
            'text': payload.get('text') or '',
            'result': [
                {**word,
                 'start': float(word['start']) + word_offset_sec,
                 'end': float(word['end']) + word_offset_sec}
                for word in payload.get('result') or []
            ],
        }
        if done:
            wire_payload['done'] = True
    else:
        wire_payload = {'text': payload.get('text') or ''}
    if not live_metadata:
        returned_payload = payload
    if wire_payload.get('text') or word_timestamps or live_metadata or done:
        await websocket.send(json.dumps(wire_payload, ensure_ascii=False))
    return returned_payload


async def handle(websocket, path=None):
    token = trace_context.set(dict(meeting_id='-', session_id=uuid.uuid4().hex,
                                   request_id=0, telemetry=False))
    try:
        await handle_session(websocket, path)
    finally:
        log.info('Session closed')
        trace_context.reset(token)


async def handle_session(websocket, path=None):
    log.info('Baglanti: %s', websocket.remote_address)
    sample_rate = 16000
    language = DEFAULT_LANGUAGE
    task = 'transcribe'
    offline = False
    word_timestamps = False
    live_metadata = False
    chunk_seconds = CHUNK_SECONDS
    context_seconds = chunk_seconds
    buffer = bytearray()
    bytes_per_chunk = int(sample_rate * 2 * chunk_seconds)
    offline_chunk_bytes = sample_rate * 2 * 15
    bytes_per_context = bytes_per_chunk
    context_overlap_bytes = 0
    rolling_context = bytearray()
    prompt_words = []
    processed_samples = 0
    sequence = 0
    eof_received = False
    live_audio_samples = 0
    live_bytes_since_decode = 0
    live_silence_seconds = 0.0
    live_speech_seen = False
    silence_rms_threshold = 300
    silence_flush_seconds = 1.2
    stabilizer = LiveHypothesisStabilizer()

    async def emit_live_window(
        window,
        context_start_sec,
        emit_start_sec,
        end_sec,
        done=False,
    ):
        nonlocal sequence, prompt_words
        sequence += 1
        payload = await transcribe_buffer(
            websocket,
            window,
            sample_rate,
            language,
            word_timestamps=True,
            task=task,
            live_metadata=True,
            sequence=sequence,
            start_sec=emit_start_sec,
            end_sec=end_sec,
            done=done,
            context_start_sec=context_start_sec,
            emit_start_sec=emit_start_sec,
            initial_prompt=' '.join(prompt_words) or None,
        )
        detected_language = payload.get('language')
        language_probability = float(
            payload.get('language_probability', 0.0) or 0.0
        )
        if detected_language and payload.get('text'):
            log.info(
                'Canli dil algilandi: language=%s probability=%.2f',
                detected_language,
                language_probability,
            )
        emitted_words = [
            str(item.get('word') or '').strip()
            for item in payload.get('result') or []
            if str(item.get('word') or '').strip()
        ]
        if emitted_words:
            prompt_words = (prompt_words + emitted_words)[-30:]
        if payload.get('text'):
            log.info('Parca: text_chars=%s', len(payload['text']))
        return payload

    async def emit_stabilized_live(force_final=False, done=False):
        nonlocal sequence, prompt_words
        audio_end_sec = live_audio_samples / sample_rate
        if buffer:
            context_start_sec = max(
                0.0,
                (live_audio_samples - len(buffer) // 2) / sample_rate,
            )
            payload = await measured_inference(
                websocket, bytes(buffer), sample_rate, language, task, True,
                ' '.join(prompt_words) or None, audio_end_sec)
            absolute_words = [
                {
                    **item,
                    'start': context_start_sec + float(item.get('start', 0.0)),
                    'end': context_start_sec
                    + float(item.get('end', item.get('start', 0.0))),
                }
                for item in payload.get('result') or []
            ]
        else:
            payload = {'text': '', 'result': []}
            absolute_words = []

        events = stabilizer.update(
            absolute_words,
            audio_end_sec=audio_end_sec,
            force_final=force_final,
        )
        for event in events:
            sequence += 1
            await websocket.send(
                json.dumps(
                    event.as_payload(sequence=sequence, done=False),
                    ensure_ascii=False,
                )
            )
            if event.status == 'final':
                committed = [
                    str(item.get('word') or '').strip()
                    for item in event.words
                    if str(item.get('word') or '').strip()
                ]
                prompt_words = (prompt_words + committed)[-30:]
            if event.text:
                log.info('Canli %s: segment_id=%s text_chars=%s',
                         event.status, event.segment_id, len(event.text))

        if done:
            sequence += 1
            await websocket.send(
                json.dumps(
                    {
                        'type': 'transcript',
                        'sequence': sequence,
                        'segment_id': '',
                        'revision': 0,
                        'status': 'final',
                        'start_sec': audio_end_sec,
                        'end_sec': audio_end_sec,
                        'text': '',
                        'result': [],
                        'done': True,
                    },
                    ensure_ascii=False,
                )
            )
        return payload

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    obj = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if 'config' in obj:
                    config = obj['config'] or {}
                    sample_rate = int(config.get('sample_rate') or sample_rate)
                    language = config.get('language') or DEFAULT_LANGUAGE
                    requested_task = str(config.get('task') or 'transcribe')
                    task = requested_task if requested_task in {'transcribe', 'translate'} else 'transcribe'
                    word_timestamps = bool(config.get('word_timestamps', False))
                    live_metadata_requested = bool(
                        config.get('live_metadata', False)
                    )
                    offline = bool(
                        config.get('offline', False)
                        or (word_timestamps and not live_metadata_requested)
                    )
                    live_metadata = live_metadata_requested and not offline
                    trace_context.get().update(
                        meeting_id=safe_identifier(config.get('meeting_id')),
                        session_id=safe_identifier(config.get('session_id') or trace_context.get()['session_id']),
                        telemetry=bool(config.get('telemetry')) and live_metadata,
                    )
                    requested_chunk_seconds = float(
                        config.get('chunk_seconds', chunk_seconds)
                    )
                    if 1.0 <= requested_chunk_seconds <= 30.0:
                        chunk_seconds = requested_chunk_seconds
                    else:
                        log.warning(
                            'Gecersiz chunk_seconds=%s; varsayilan=%s kullaniliyor',
                            requested_chunk_seconds,
                            chunk_seconds,
                        )
                    requested_context_seconds = float(
                        config.get('context_seconds', chunk_seconds)
                    )
                    if (
                        live_metadata
                        and chunk_seconds <= requested_context_seconds <= 30.0
                    ):
                        context_seconds = requested_context_seconds
                    else:
                        context_seconds = chunk_seconds
                    bytes_per_chunk = int(sample_rate * 2 * chunk_seconds)
                    # Offline recordings must never be decoded as one unbounded GPU job.
                    # Keep this below Whisper's 30-second window and retain absolute word times.
                    offline_chunk_bytes = sample_rate * 2 * 15
                    bytes_per_context = int(sample_rate * 2 * context_seconds)
                    context_overlap_bytes = max(
                        0,
                        bytes_per_context - bytes_per_chunk,
                    )
                    silence_rms_threshold = int(
                        config.get('silence_rms_threshold', 300)
                    )
                    silence_flush_seconds = float(
                        config.get('silence_flush_seconds', 1.2)
                    )
                    stabilizer = LiveHypothesisStabilizer(
                        holdback_seconds=float(
                            config.get('stability_holdback_seconds', 0.6)
                        )
                    )
                    log.info(
                        'Config: sample_rate=%s chunk_seconds=%s context_seconds=%s offline=%s word_timestamps=%s live_metadata=%s language=%s task=%s',
                        sample_rate,
                        chunk_seconds,
                        context_seconds,
                        offline,
                        word_timestamps,
                        live_metadata,
                        language,
                        task,
                    )
                    continue

                if obj.get('eof') == 1:
                    if offline:
                        start_sec = processed_samples / sample_rate
                        sequence += 1
                        payload = await transcribe_buffer(
                            websocket, buffer, sample_rate, language,
                            word_timestamps=word_timestamps, task=task,
                            done=True, word_offset_sec=start_sec,
                        )
                        processed_samples += len(buffer) // 2
                        log.info('Offline EOF: chunks=%s processed_sec=%.2f',
                                 sequence, processed_samples / sample_rate)
                        buffer.clear()
                        eof_received = True
                        continue
                    if live_metadata:
                        payload = await emit_stabilized_live(
                            force_final=True,
                            done=True,
                        )
                        log.info(
                            'EOF: text_chars=%s words=%s',
                            len(payload.get('text') or ''),
                            len(payload.get('result') or []),
                        )
                        buffer.clear()
                        eof_received = True
                        continue
                    if live_metadata:
                        if buffer:
                            emit_start_sec = processed_samples / sample_rate
                            if processed_samples == 0:
                                window = bytes(buffer)
                                context_start_sec = 0.0
                            else:
                                window = bytes(rolling_context) + bytes(buffer)
                                context_start_sec = max(
                                    0.0,
                                    (
                                        processed_samples
                                        - len(rolling_context) // 2
                                    )
                                    / sample_rate,
                                )
                            end_sec = (
                                processed_samples + len(buffer) // 2
                            ) / sample_rate
                            payload = await emit_live_window(
                                window,
                                context_start_sec,
                                emit_start_sec,
                                end_sec,
                                done=True,
                            )
                            processed_samples += len(buffer) // 2
                        else:
                            sequence += 1
                            position_sec = processed_samples / sample_rate
                            await websocket.send(
                                json.dumps(
                                    {
                                        'type': 'transcript',
                                        'sequence': sequence,
                                        'start_sec': position_sec,
                                        'end_sec': position_sec,
                                        'text': '',
                                        'result': [],
                                        'done': True,
                                    },
                                    ensure_ascii=False,
                                )
                            )
                            payload = {'text': '', 'result': []}
                    else:
                        start_sec = processed_samples / sample_rate
                        end_sec = start_sec + len(buffer) / 2 / sample_rate
                        sequence += 1
                        payload = await transcribe_buffer(
                            websocket,
                            buffer,
                            sample_rate,
                            language,
                            word_timestamps,
                            task=task,
                            live_metadata=live_metadata,
                            sequence=sequence,
                            start_sec=start_sec,
                            end_sec=end_sec,
                            done=offline or live_metadata,
                        )
                        processed_samples += len(buffer) // 2
                    log.info(
                        'EOF: text_chars=%s words=%s',
                        len(payload['text']),
                        len(payload['result']),
                    )
                    buffer.clear()
                    eof_received = True
                    continue

                if obj.get('reset') == 1:
                    buffer.clear()
                    rolling_context.clear()
                    prompt_words.clear()
                    processed_samples = 0
                    sequence = 0
                    live_audio_samples = 0
                    live_bytes_since_decode = 0
                    live_silence_seconds = 0.0
                    live_speech_seen = False
                    stabilizer = LiveHypothesisStabilizer()
                    eof_received = False
                    log.info('Reset: buffer temizlendi')
                continue

            if live_metadata:
                buffer.extend(message)
                live_audio_samples += len(message) // 2
                live_bytes_since_decode += len(message)
                samples = np.frombuffer(message, dtype=np.int16)
                rms = (
                    float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
                    if samples.size
                    else 0.0
                )
                frame_seconds = (len(message) // 2) / sample_rate
                if rms >= silence_rms_threshold:
                    live_speech_seen = True
                    live_silence_seconds = 0.0
                elif live_speech_seen:
                    live_silence_seconds += frame_seconds

                if len(buffer) > bytes_per_context:
                    del buffer[:-bytes_per_context]
                endpoint = (
                    live_speech_seen
                    and live_silence_seconds >= silence_flush_seconds
                )
                if live_bytes_since_decode >= bytes_per_chunk or endpoint:
                    await emit_stabilized_live(force_final=endpoint)
                    live_bytes_since_decode = 0
                if endpoint:
                    buffer.clear()
                    live_speech_seen = False
                    live_silence_seconds = 0.0
                continue

            buffer.extend(message)
            if offline:
                while len(buffer) >= offline_chunk_bytes:
                    chunk = bytes(buffer[:offline_chunk_bytes])
                    del buffer[:offline_chunk_bytes]
                    start_sec = processed_samples / sample_rate
                    processed_samples += len(chunk) // 2
                    sequence += 1
                    await transcribe_buffer(
                        websocket, chunk, sample_rate, language,
                        word_timestamps=word_timestamps, task=task,
                        word_offset_sec=start_sec,
                    )
                continue
            if live_metadata and context_seconds > chunk_seconds:
                if processed_samples == 0 and len(buffer) >= bytes_per_context:
                    window = bytes(buffer[:bytes_per_context])
                    del buffer[:bytes_per_context]
                    processed_samples += len(window) // 2
                    rolling_context = bytearray(
                        window[-context_overlap_bytes:]
                        if context_overlap_bytes
                        else b''
                    )
                    await emit_live_window(
                        window,
                        context_start_sec=0.0,
                        emit_start_sec=0.0,
                        end_sec=processed_samples / sample_rate,
                    )

                while processed_samples > 0 and len(buffer) >= bytes_per_chunk:
                    chunk = bytes(buffer[:bytes_per_chunk])
                    del buffer[:bytes_per_chunk]
                    history = bytes(rolling_context)
                    window = history + chunk
                    context_start_sec = max(
                        0.0,
                        (processed_samples - len(history) // 2) / sample_rate,
                    )
                    emit_start_sec = processed_samples / sample_rate
                    processed_samples += len(chunk) // 2
                    rolling_context = bytearray(
                        window[-context_overlap_bytes:]
                        if context_overlap_bytes
                        else b''
                    )
                    await emit_live_window(
                        window,
                        context_start_sec=context_start_sec,
                        emit_start_sec=emit_start_sec,
                        end_sec=processed_samples / sample_rate,
                    )
            else:
                while live_metadata and len(buffer) >= bytes_per_chunk:
                    chunk = bytes(buffer[:bytes_per_chunk])
                    del buffer[:bytes_per_chunk]
                    start_sec = processed_samples / sample_rate
                    processed_samples += len(chunk) // 2
                    await emit_live_window(
                        chunk,
                        context_start_sec=start_sec,
                        emit_start_sec=start_sec,
                        end_sec=processed_samples / sample_rate,
                    )
            if not offline and not live_metadata and len(buffer) >= bytes_per_chunk:
                payload = await transcribe_buffer(
                    websocket,
                    buffer,
                    sample_rate,
                    language,
                    word_timestamps=word_timestamps,
                    task=task,
                )
                if payload['text']:
                    log.info('Parca: text_chars=%s', len(payload['text']))
                buffer.clear()

    except websockets.ConnectionClosed:
        log.info('Baglanti kapandi')
    finally:
        if buffer and not offline and not eof_received:
            try:
                if live_metadata:
                    emit_start_sec = processed_samples / sample_rate
                    history = bytes(rolling_context) if processed_samples else b''
                    window = history + bytes(buffer)
                    context_start_sec = max(
                        0.0,
                        (processed_samples - len(history) // 2) / sample_rate,
                    )
                    end_sec = (
                        processed_samples + len(buffer) // 2
                    ) / sample_rate
                    await emit_live_window(
                        window,
                        context_start_sec=context_start_sec,
                        emit_start_sec=emit_start_sec,
                        end_sec=end_sec,
                        done=True,
                    )
                else:
                    start_sec = processed_samples / sample_rate
                    end_sec = start_sec + len(buffer) / 2 / sample_rate
                    sequence += 1
                    await transcribe_buffer(
                        websocket,
                        buffer,
                        sample_rate,
                        language,
                        word_timestamps=word_timestamps,
                        task=task,
                        sequence=sequence,
                        start_sec=start_sec,
                        end_sec=end_sec,
                    )
            except Exception:
                pass


async def main():
    log.info('Whisper-bridge dinliyor: ws://0.0.0.0:%s', PORT)
    async with websockets.serve(
        handle,
        '0.0.0.0',
        PORT,
        max_size=None,
        ping_interval=None,
    ):
        await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
