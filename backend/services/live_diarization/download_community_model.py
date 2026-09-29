from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

REPO_ID = 'pyannote/speaker-diarization-community-1'
REQUIRED_FILES = (
    Path('config.yaml'),
    Path('segmentation/pytorch_model.bin'),
    Path('embedding/pytorch_model.bin'),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict:
    files = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or '.cache' in path.parts:
            continue
        files.append(
            {
                'path': path.relative_to(root).as_posix(),
                'size': path.stat().st_size,
                'sha256': sha256_file(path),
            }
        )
    return {'repo_id': REPO_ID, 'files': files}


def verify_manifest(root: Path) -> dict:
    manifest_path = root / 'manifest.sha256.json'
    if not manifest_path.is_file():
        raise RuntimeError(f'Manifest not found: {manifest_path}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('repo_id') != REPO_ID:
        raise RuntimeError(f'Unexpected repo_id in manifest: {manifest.get(repo_id)!r}')

    checked_bytes = 0
    for item in manifest.get('files', []):
        relative = Path(item['path'])
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f'Manifest file is missing: {relative.as_posix()}')
        size = path.stat().st_size
        if size != item['size']:
            raise RuntimeError(
                f'Size mismatch for {relative.as_posix()}: expected {item[size]}, got {size}'
            )
        digest = sha256_file(path)
        if digest != item['sha256']:
            raise RuntimeError(f'SHA-256 mismatch for {relative.as_posix()}')
        checked_bytes += size

    missing = [str(relative) for relative in REQUIRED_FILES if not (root / relative).is_file()]
    if missing:
        raise RuntimeError(f'Model is missing required files: {missing}')
    return {
        'ok': True,
        'verified': True,
        'destination': str(root),
        'file_count': len(manifest.get('files', [])),
        'total_bytes': checked_bytes,
        'manifest': str(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Download the gated pyannote community-1 repo for air-gapped use'
    )
    parser.add_argument(
        '--destination',
        type=Path,
        default=Path('models/pyannote-speaker-diarization-community-1'),
    )
    parser.add_argument(
        '--verify-only',
        action='store_true',
        help='Verify an already-downloaded model against manifest.sha256.json',
    )
    args = parser.parse_args()
    destination = args.destination.resolve()
    if args.verify_only:
        print(json.dumps(verify_manifest(destination)))
        return

    from huggingface_hub import get_token, snapshot_download

    token = os.getenv('HF_TOKEN') or get_token()
    if not token:
        parser.error('No Hugging Face token found. Run: hf auth login')

    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        local_dir=destination,
        token=token,
    )

    missing = [
        str(relative)
        for relative in REQUIRED_FILES
        if not (destination / relative).is_file()
    ]
    if missing:
        raise RuntimeError(f'Download is missing required files: {missing}')

    manifest = build_manifest(destination)
    manifest_path = destination / 'manifest.sha256.json'
    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding='utf-8',
    )
    print(json.dumps(verify_manifest(destination)))


if __name__ == '__main__':
    main()
