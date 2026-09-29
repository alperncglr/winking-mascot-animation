#!/usr/bin/env python3
"""Create and compare portable SHA-256 manifests for air-gapped transfers."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from pathlib import Path


DEFAULT_EXCLUDES = (
    ".git",
    ".pytest_cache",
    ".venv",
    ".venv-*",
    "__pycache__",
    "*.pyc",
    ".env",
)


def is_excluded(relative_path: str, patterns: list[str]) -> bool:
    parts = relative_path.split("/")
    return any(
        fnmatch.fnmatch(relative_path, pattern)
        or any(fnmatch.fnmatch(part, pattern) for part in parts)
        for pattern in patterns
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_files(root: Path, includes: list[str], excludes: list[str], output: Path):
    seen: set[str] = set()
    output_resolved = output.resolve()
    for include in includes:
        target = (root / include).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Included path does not exist: {target}")
        candidates = [target] if target.is_file() else target.rglob("*")
        for candidate in candidates:
            if not candidate.is_file() or candidate.resolve() == output_resolved:
                continue
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(f"Included path is outside root: {candidate}") from exc
            if relative in seen or is_excluded(relative, excludes):
                continue
            seen.add(relative)
            yield relative, candidate


def create_manifest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Root is not a directory: {root}")

    includes = args.include or ["."]
    excludes = list(DEFAULT_EXCLUDES) + list(args.exclude or [])
    entries = []
    for relative, path in sorted(
        iter_files(root, includes, excludes, output), key=lambda item: item[0]
    ):
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "format": "meeting-scribe-airgap-manifest-v1",
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(output),
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def load_entries(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "meeting-scribe-airgap-manifest-v1":
        raise ValueError(f"Unsupported manifest format: {path}")
    return {entry["path"]: entry for entry in data["files"]}


def compare_manifests(args: argparse.Namespace) -> int:
    left_path = Path(args.left).resolve()
    right_path = Path(args.right).resolve()
    left = load_entries(left_path)
    right = load_entries(right_path)
    left_names = set(left)
    right_names = set(right)
    only_left = sorted(left_names - right_names)
    only_right = sorted(right_names - left_names)
    changed = sorted(
        name
        for name in left_names & right_names
        if left[name]["size"] != right[name]["size"]
        or left[name]["sha256"] != right[name]["sha256"]
    )
    result = {
        "ok": not only_left and not only_right and not changed,
        "left": str(left_path),
        "right": str(right_path),
        "only_left": only_left,
        "only_right": only_right,
        "changed": changed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a SHA-256 manifest")
    create.add_argument("--root", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--include", action="append", default=[])
    create.add_argument("--exclude", action="append", default=[])
    create.set_defaults(handler=create_manifest)

    compare = subparsers.add_parser("compare", help="Compare two manifests")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.set_defaults(handler=compare_manifests)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
