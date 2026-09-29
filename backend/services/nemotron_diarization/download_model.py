from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download


MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_REVISION = "a435e9867d79e789e90053f9b6d6834053af564a"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the pinned Nemotron model")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/Nemotron-3-Diarization"),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=output,
        allow_patterns=(
            "config.json",
            "processor_config.json",
            "preprocessor_config.json",
            "model.safetensors",
            "README.md",
            "LICENSE*",
        ),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "model_id": MODEL_ID,
                "revision": MODEL_REVISION,
                "path": str(Path(result).resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
