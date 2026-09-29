from __future__ import annotations

import argparse
import json
from pathlib import Path

from meeting_scribe.config import load_settings
from meeting_scribe.services.offline.pipeline import build_diarizer


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Run the Meeting Scribe final/offline diarizer on a WAV file.'
    )
    parser.add_argument('audio_path', type=Path)
    parser.add_argument('--config', type=Path, default=Path('config.yaml'))
    parser.add_argument('--first-turns', type=int, default=10)
    args = parser.parse_args()

    audio_path = args.audio_path.resolve()
    if not audio_path.is_file():
        parser.error(f'audio file not found: {audio_path}')

    settings = load_settings(args.config)
    diarizer = build_diarizer(settings)
    turns = diarizer.diarize(audio_path)
    payload = {
        'ok': True,
        'audio_path': str(audio_path),
        'model': settings.offline.pyannote_model,
        'turn_count': len(turns),
        'speaker_ids': sorted({turn.speaker_id for turn in turns}),
        'first_turns': [
            {
                'speaker_id': turn.speaker_id,
                'start_sec': turn.start_sec,
                'end_sec': turn.end_sec,
                'overlap': turn.overlap,
            }
            for turn in turns[: max(args.first_turns, 0)]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == '__main__':
    main()
