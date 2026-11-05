"""Ephemeral Sandbox: provides temporary isolated directories for execution."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any


class EphemeralSandbox:
    """Disposable scratch directory for untrusted scripts, test runs, and temp artifacts."""

    def __init__(self, prefix: str = "airframe_sandbox_", seed_dir: str | Path | None = None) -> None:
        self.prefix = prefix
        self.seed_dir = Path(seed_dir) if seed_dir else None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self._temp_dir = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.path = Path(self._temp_dir.name).resolve()

        if self.seed_dir and self.seed_dir.is_dir():
            shutil.copytree(self.seed_dir, self.path, dirs_exist_ok=True)

        return self.path

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._temp_dir:
            self._temp_dir.cleanup()
            self._temp_dir = None
            self.path = None

    async def __aenter__(self) -> Path:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
