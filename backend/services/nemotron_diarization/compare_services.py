from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LIVE_DIARIZATION_DIR = HERE.parent / "live_diarization"
sys.path.insert(0, str(LIVE_DIARIZATION_DIR))

from probe import run_probe  # noqa: E402


def summarize(result: dict) -> dict:
    turns = result.get("first_turns", [])
    return {
        "ok": result.get("ok"),
        "message_count": result.get("message_count"),
        "turn_count": result.get("turn_count"),
        "speaker_ids": result.get("speaker_ids"),
        "processed_until_sec": result.get("processed_until_sec"),
        "first_turns": turns,
    }


async def compare(args) -> dict:
    diart = await run_probe(
        args.wav_path,
        args.diart_url,
        args.chunk_ms,
        args.receive_timeout,
    )
    nemotron = await run_probe(
        args.wav_path,
        args.nemotron_url,
        args.chunk_ms,
        args.receive_timeout,
    )
    return {
        "wav": str(args.wav_path.resolve()),
        "diart": summarize(diart),
        "nemotron": summarize(nemotron),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay the same WAV against Diart and Nemotron"
    )
    parser.add_argument("wav_path", type=Path)
    parser.add_argument("--diart-url", default="ws://127.0.0.1:2710")
    parser.add_argument("--nemotron-url", default="ws://127.0.0.1:2712")
    parser.add_argument("--chunk-ms", type=int, default=250)
    parser.add_argument("--receive-timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not args.wav_path.is_file():
        parser.error(f"WAV file not found: {args.wav_path}")
    result = asyncio.run(compare(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["diart"]["ok"] or not result["nemotron"]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
