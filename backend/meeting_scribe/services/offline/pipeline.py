from __future__ import annotations

from pathlib import Path
from threading import Lock

from meeting_scribe.config import Settings
from meeting_scribe.domain.models import MeetingStatus
from meeting_scribe.paths import MeetingPaths
from meeting_scribe.services.capture.manifest import combine_chunks
from meeting_scribe.services.offline.adapters import (
    Diarizer,
    FasterWhisperTranscriber,
    OpenAIWhisperTranscriber,
    PlaceholderDiarizer,
    PlaceholderTranscriber,
    PyannoteDiarizer,
    Transcriber,
    VoskWebSocketTranscriber,
)
from meeting_scribe.services.offline.alignment import align_words_to_segments
from meeting_scribe.services.offline.speaker_continuity import (
    load_live_speaker_labels,
    load_live_speaker_turns,
    match_offline_speakers_to_live_labels,
)
from meeting_scribe.storage.repositories import MeetingRepository, SegmentRepository


_GPU_JOB_LOCK = Lock()

# Whisper/pyannote modelleri her biri birkaç GB GPU belleği kaplıyor.
# Her toplantı bitişinde OfflinePipeline yeniden kurulduğundan, modelleri
# build_* ile taze yüklemek yerine process ömrü boyunca burada önbelleğe
# alıp yeniden kullanıyoruz — aksi halde her toplantıda whisper-bridge ve
# canlı diarization zaten GPU'dayken üstüne bir kopya daha binip OOM'a
# (gördüğümüz "CUDA failed with error out of memory" çökmesine) sebep oluyordu.
_MODEL_CACHE_LOCK = Lock()
_cached_transcriber: Transcriber | None = None
_cached_diarizer: Diarizer | None = None


def get_cached_transcriber(settings: Settings) -> Transcriber:
    global _cached_transcriber
    if _cached_transcriber is None:
        with _MODEL_CACHE_LOCK:
            if _cached_transcriber is None:
                _cached_transcriber = build_transcriber(settings)
    return _cached_transcriber


def get_cached_diarizer(settings: Settings) -> Diarizer:
    global _cached_diarizer
    if _cached_diarizer is None:
        with _MODEL_CACHE_LOCK:
            if _cached_diarizer is None:
                _cached_diarizer = build_diarizer(settings)
    return _cached_diarizer


class OfflinePipeline:
    def __init__(
        self,
        settings: Settings,
        meeting_repo: MeetingRepository,
        segment_repo: SegmentRepository,
        transcriber: Transcriber | None = None,
        diarizer: Diarizer | None = None,
    ) -> None:
        self.settings = settings
        self.paths = MeetingPaths(settings)
        self.meeting_repo = meeting_repo
        self.segment_repo = segment_repo
        self.transcriber = transcriber or get_cached_transcriber(settings)
        self.diarizer = diarizer or get_cached_diarizer(settings)

    def run(self, meeting_id: int) -> Path:
        from meeting_scribe.log_context import meeting_log_context
        with meeting_log_context(meeting_id, f'offline-{meeting_id}'):
            return self._run_meeting(meeting_id)

    def _run_meeting(self, meeting_id: int) -> Path:
        self.meeting_repo.update_status(meeting_id, MeetingStatus.REFINING)
        processing_dir = self.paths.runtime_dir(meeting_id) / 'processing'
        processing_dir.mkdir(parents=True, exist_ok=True)
        combined_audio = processing_dir / 'combined.wav'
        try:
            combine_chunks(
                self.paths.audio_dir(meeting_id),
                self.paths.manifest_path(meeting_id),
                combined_audio,
            )
            meeting = self.meeting_repo.get_meeting(meeting_id)
            language = None
            if meeting and meeting.get('language') != 'mixed':
                language = meeting.get('language')
            with _GPU_JOB_LOCK:
                words = self.transcriber.transcribe(combined_audio, language=language)
                turns = self.diarizer.diarize(combined_audio)
            segments = align_words_to_segments(meeting_id, words, turns)
            live_labels = load_live_speaker_labels(
                self.paths.live_transcript_path(meeting_id)
            )
            if not live_labels:
                live_labels = {
                    participant['diarization_speaker_id']: participant['label']
                    for participant in self.meeting_repo.participants(meeting_id)
                }
            preferred_labels = match_offline_speakers_to_live_labels(
                turns,
                load_live_speaker_turns(
                    self.paths.live_diarization_path(meeting_id)
                ),
                live_labels,
            )
            segments = self.segment_repo.replace_final_segments_with_labels(
                meeting_id,
                segments,
                preferred_labels=preferred_labels,
                reserved_labels=set(live_labels.values()),
            )

            final_path = self.paths.final_transcript_path(meeting_id)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_final_transcript(meeting_id, final_path, segments)
            self.meeting_repo.update_status(meeting_id, MeetingStatus.REFINED)
            return final_path
        except Exception:
            self.meeting_repo.update_status(meeting_id, MeetingStatus.REFINE_FAILED)
            raise
        finally:
            combined_audio.unlink(missing_ok=True)
            try:
                processing_dir.rmdir()
            except OSError:
                pass

    def _write_final_transcript(self, meeting_id: int, path: Path, segments) -> None:
        participants = {p["id"]: p["label"] for p in self.meeting_repo.participants(meeting_id)}
        lines = ["# Final Transcript", ""]
        for segment in segments:
            stamp = _format_timestamp(segment.start_sec)
            speaker = participants.get(segment.participant_id, "Bilinmeyen konuşmacı")
            if segment.overlap:
                speaker = f"{speaker} [üst üste konuşma]"
            lines.append(f"{stamp} - {speaker} - {segment.text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"



def build_transcriber(settings: Settings) -> Transcriber:
    backend = settings.offline.whisper_backend
    if backend == 'remote_vosk_ws':
        return VoskWebSocketTranscriber(
            url=settings.offline.whisper_ws_url,
            receive_timeout_seconds=settings.offline.whisper_ws_receive_timeout_seconds,
            chunk_bytes=settings.offline.whisper_ws_chunk_bytes,
        )
    if backend == "remote_openai":
        return OpenAIWhisperTranscriber(
            base_url=settings.offline.whisper_api_base_url,
            model=settings.offline.whisper_model,
            timeout_seconds=settings.offline.whisper_api_timeout_seconds,
        )
    if backend == "local_faster_whisper":
        return FasterWhisperTranscriber(
            model_name=settings.offline.whisper_model,
            compute_type=settings.offline.whisper_compute_type,
        )
    if backend == 'placeholder':
        return PlaceholderTranscriber()
    raise ValueError(f'Unsupported Whisper backend: {backend}')


def build_diarizer(settings: Settings) -> Diarizer:
    import os

    if settings.offline.diarizer_backend == "pyannote":
        return PyannoteDiarizer(
            model_name=settings.offline.pyannote_model,
            hf_token=os.getenv(settings.offline.hf_token_env),
            segmentation_batch_size=settings.offline.pyannote_segmentation_batch_size,
            embedding_batch_size=settings.offline.pyannote_embedding_batch_size,
        )
    if settings.offline.diarizer_backend == 'placeholder':
        return PlaceholderDiarizer()
    raise ValueError(
        f'Unsupported diarizer backend: {settings.offline.diarizer_backend}'
    )
