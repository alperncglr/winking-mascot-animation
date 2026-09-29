from __future__ import annotations

import json
import os
import wave
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioChunk:
    file_name: str
    start_sec: float
    duration_sec: float
    sample_count: int
    sample_rate: int


class Manifest:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, chunk: AudioChunk) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = {item.file_name: item for item in self.read_all()}
        if chunk.file_name in existing:
            if existing[chunk.file_name] == chunk:
                return
            raise ValueError(f'Conflicting manifest entry: {chunk.file_name}')
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> list[AudioChunk]:
        if not self.path.exists():
            return []
        chunks: list[AudioChunk] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    chunk = AudioChunk(**json.loads(line))
                    previous = next(
                        (item for item in chunks if item.file_name == chunk.file_name),
                        None,
                    )
                    if previous is None:
                        chunks.append(chunk)
                    elif previous != chunk:
                        raise ValueError(
                            f'Conflicting manifest entry: {chunk.file_name}'
                        )
        return chunks


def wav_duration(path: Path) -> tuple[float, int, int]:
    with wave.open(str(path), "rb") as wav:
        sample_count = wav.getnframes()
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
    return sample_count / sample_rate, sample_count, channels


def combine_chunks(audio_dir: Path, manifest_path: Path, output_path: Path) -> Path:
    chunks = Manifest(manifest_path).read_all()
    if not chunks:
        raise FileNotFoundError(f"No chunks in manifest: {manifest_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    first_path = audio_dir / chunks[0].file_name
    with wave.open(str(first_path), "rb") as first:
        params = first.getparams()

    with wave.open(str(output_path), "wb") as out:
        out.setparams(params)
        for chunk in chunks:
            if Path(chunk.file_name).name != chunk.file_name:
                raise ValueError(f'Unsafe chunk name: {chunk.file_name}')
            chunk_path = audio_dir / chunk.file_name
            with wave.open(str(chunk_path), "rb") as wav:
                if wav.getframerate() != params.framerate or wav.getnchannels() != params.nchannels:
                    raise ValueError(f"Chunk format mismatch: {chunk_path}")
                if wav.getsampwidth() != params.sampwidth or wav.getcomptype() != params.comptype:
                    raise ValueError(f'Chunk encoding mismatch: {chunk_path}')
                out.writeframes(wav.readframes(wav.getnframes()))
    return output_path
