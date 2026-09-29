"""Test helpers."""
from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    root = Path('.test_tmp')
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        yield Path(tmp)
