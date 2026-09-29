from __future__ import annotations

import asyncio
import json
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from meeting_scribe.log_context import meeting_context, session_context


@dataclass(frozen=True)
class Word:
    text: str
    start_sec: float
    end_sec: float
    confidence: float | None = None


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_id: str
    start_sec: float
    end_sec: float
    overlap: bool = False


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: str | None) -> list[Word]:
        ...


class Diarizer(Protocol):
    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        ...


class PlaceholderTranscriber:
    def transcribe(self, audio_path: Path, language: str | None) -> list[Word]:
        return [
            Word(
                text=f"[transcript pending for {audio_path.name}]",
                start_sec=0.0,
                end_sec=1.0,
                confidence=None,
            )
        ]


class PlaceholderDiarizer:
    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        return [SpeakerTurn(speaker_id="speaker_0", start_sec=0.0, end_sec=3600.0)]


class OpenAIWhisperTranscriber:
    def __init__(self, base_url: str, model: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def transcribe(self, audio_path: Path, language: str | None) -> list[Word]:
        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if language:
            data["language"] = language
        with audio_path.open("rb") as audio:
            files = {"file": (audio_path.name, audio, "audio/wav")}
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/audio/transcriptions", data=data, files=files)
                response.raise_for_status()
        return _words_from_openai_payload(response.json())


class VoskWebSocketTranscriber:
    def __init__(
        self,
        url: str,
        receive_timeout_seconds: int = 300,
        chunk_bytes: int = 32000,
    ) -> None:
        self.url = url
        self.receive_timeout_seconds = receive_timeout_seconds
        self.chunk_bytes = chunk_bytes

    def transcribe(self, audio_path: Path, language: str | None) -> list[Word]:
        pcm, sample_rate, duration_sec = _load_pcm16_mono(audio_path)
        messages = asyncio.run(
            self._exchange(pcm, sample_rate, language, duration_sec)
        )
        return _words_from_vosk_messages(messages)

    async def _exchange(
        self,
        pcm: bytes,
        sample_rate: int,
        language: str | None,
        duration_sec: float,
    ) -> list[dict[str, Any]]:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError('Install websockets to use remote_vosk_ws') from exc

        config: dict[str, Any] = {
            'meeting_id': meeting_context.get(),
            'session_id': session_context.get(),
            'sample_rate': sample_rate,
            'retry_low_confidence': True,
            'offline': True,
            'word_timestamps': True,
            'task': 'transcribe',
        }
        if language:
            config['language'] = language

        messages: list[dict[str, Any]] = []
        response_timeout_seconds = max(
            float(self.receive_timeout_seconds),
            float(duration_sec) + 60.0,
        )
        async with websockets.connect(
            self.url,
            open_timeout=self.receive_timeout_seconds,
            proxy=None,
            max_size=None,
        ) as ws:
            async def receive_results() -> None:
                while True:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=response_timeout_seconds
                    )
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8')
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        messages.append(payload)
                        if payload.get('done'):
                            return

            # The bridge now sends bounded offline results while more audio is
            # still being uploaded. Read concurrently to avoid socket backpressure
            # deadlocking both peers on long recordings.
            receiver = asyncio.create_task(receive_results())
            try:
                await ws.send(json.dumps({'config': config}))
                for offset in range(0, len(pcm), self.chunk_bytes):
                    await ws.send(pcm[offset : offset + self.chunk_bytes])
                await ws.send(json.dumps({'eof': 1}))
                await receiver
            finally:
                if not receiver.done():
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)

        if not messages:
            raise RuntimeError('Whisper WebSocket returned no transcription messages')
        return messages


class FasterWhisperTranscriber:
    def __init__(self, model_name: str, compute_type: str = "float16") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install offline extras to use faster-whisper") from exc
        self.model = WhisperModel(model_name, device="cuda", compute_type=compute_type)

    def transcribe(self, audio_path: Path, language: str | None) -> list[Word]:
        # vad_filter=True runs faster-whisper's built-in Silero VAD pass before decoding,
        # dropping silent/non-speech regions — this is the real fix for Whisper hallucinating
        # on silence/noise, not a hand-rolled VAD (faster-whisper already bundles Silero VAD).
        segments, _info = self.model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
        )
        words: list[Word] = []
        for segment in segments:
            for word in segment.words or []:
                words.append(
                    Word(
                        text=word.word.strip(),
                        start_sec=float(word.start),
                        end_sec=float(word.end),
                        confidence=getattr(word, "probability", None),
                    )
                )
        return words


class PyannoteDiarizer:
    """Real offline diarization via pyannote.audio's community-1 speaker-diarization pipeline.

    Requires `pip install pyannote.audio` and a Hugging Face access token that has
    accepted the model's usage terms (see hf_token_env in config.yaml).

    Audio is loaded manually with the stdlib `wave` module and passed to the pipeline
    as an in-memory {"waveform", "sample_rate"} dict rather than a file path — pyannote
    would otherwise decode the path via torchcodec, which needs FFmpeg's shared
    libraries installed at the OS level. Our WAV chunks are always plain 16-bit PCM, so
    there is no codec to decode; this sidesteps the FFmpeg dependency entirely."""

    def __init__(
        self,
        model_name: str,
        hf_token: str | None,
        segmentation_batch_size: int = 8,
        embedding_batch_size: int = 8,
    ) -> None:
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError(
                "Install pyannote.audio to use real diarization: pip install pyannote.audio"
            ) from exc
        self.pipeline = Pipeline.from_pretrained(model_name, token=hf_token)
        if self.pipeline is None:
            raise RuntimeError(
                'Could not load pyannote pipeline. Accept the model terms and set HF_TOKEN.'
            )
        # community-1 model config defaults both batches to 32. That setting
        # can request more than 10 GiB of temporary VRAM on an A4000 while the
        # live diarizer and Whisper bridge are resident. Smaller batches keep
        # the exact same model/results and trade only throughput for headroom.
        self.pipeline.segmentation_batch_size = max(1, segmentation_batch_size)
        self.pipeline.embedding_batch_size = max(1, embedding_batch_size)
        try:
            import torch

            if torch.cuda.is_available():
                self.pipeline.to(torch.device('cuda'))
        except (ImportError, RuntimeError):
            pass

    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        waveform, sample_rate = _load_wav_as_tensor(audio_path)
        diarization = self.pipeline({"waveform": waveform, "sample_rate": sample_rate})
        regular = getattr(diarization, 'speaker_diarization', diarization)
        exclusive = getattr(diarization, 'exclusive_speaker_diarization', regular)
        overlap_timeline = regular.get_overlap()
        turns: list[SpeakerTurn] = []
        for turn, _, speaker in exclusive.itertracks(yield_label=True):
            has_overlap = len(overlap_timeline.crop(turn)) > 0
            turns.append(
                SpeakerTurn(
                    speaker_id=str(speaker),
                    start_sec=float(turn.start),
                    end_sec=float(turn.end),
                    overlap=has_overlap,
                )
            )
        return turns


def _load_wav_as_tensor(path: Path):
    import torch

    with wave.open(str(path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())
    if sample_width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got sample width {sample_width} bytes: {path}")
    samples = torch.frombuffer(bytearray(frames), dtype=torch.int16).float() / 32768.0
    if channels > 1:
        samples = samples.view(-1, channels).mean(dim=1)
    waveform = samples.unsqueeze(0)  # (channel=1, time)
    return waveform, sample_rate


def _load_pcm16_mono(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), 'rb') as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        frames = wav_file.readframes(frame_count)
    if sample_width != 2:
        raise ValueError(
            f'Expected 16-bit PCM WAV, got sample width {sample_width} bytes: {path}'
        )
    if channels < 1:
        raise ValueError(f'Invalid channel count {channels}: {path}')

    samples = array('h')
    samples.frombytes(frames)
    if sys.byteorder == 'big':
        samples.byteswap()
    if channels > 1:
        mono = array(
            'h',
            (
                int(sum(samples[index : index + channels]) / channels)
                for index in range(0, len(samples), channels)
            ),
        )
    else:
        mono = samples
    duration_sec = len(mono) / sample_rate if sample_rate else 0.0
    if sys.byteorder == 'big':
        mono.byteswap()
    return mono.tobytes(), sample_rate, duration_sec


def _words_from_vosk_messages(messages: list[dict[str, Any]]) -> list[Word]:
    timed: list[Word] = []
    for payload in messages:
        for item in payload.get('result') or []:
            text = str(item.get('word', '')).strip()
            if not text:
                continue
            timed.append(
                Word(
                    text=text,
                    start_sec=float(item.get('start', 0.0)),
                    end_sec=float(item.get('end', item.get('start', 0.0))),
                    confidence=item.get('conf'),
                )
            )
    if timed:
        return timed

    tokens: list[str] = []
    for payload in messages:
        text = str(payload.get('text') or '').strip()
        if text:
            tokens.extend(text.split())
    if not tokens:
        return []
    raise RuntimeError(
        'WebSocket Whisper returned text without word timestamps. '
        'Use local_faster_whisper for the offline speaker-attributed transcript.'
    )


def _words_from_openai_payload(payload: dict[str, Any]) -> list[Word]:
    words_payload = payload.get("words") or []
    if words_payload:
        return [
            Word(
                text=str(item.get("word", "")).strip(),
                start_sec=float(item.get("start", 0.0)),
                end_sec=float(item.get("end", item.get("start", 0.0))),
                confidence=item.get("probability"),
            )
            for item in words_payload
            if str(item.get("word", "")).strip()
        ]

    segments = payload.get("segments") or []
    if segments:
        return [
            Word(
                text=str(item.get("text", "")).strip(),
                start_sec=float(item.get("start", 0.0)),
                end_sec=float(item.get("end", item.get("start", 0.0))),
                confidence=item.get("avg_logprob"),
            )
            for item in segments
            if str(item.get("text", "")).strip()
        ]

    text = str(payload.get("text", "")).strip()
    if text:
        return [Word(text=text, start_sec=0.0, end_sec=1.0, confidence=None)]
    return [Word(text="[empty whisper response]", start_sec=0.0, end_sec=1.0, confidence=None)]
