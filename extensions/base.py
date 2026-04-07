"""Shared helpers for ecosystem extension wrappers."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any


def run_command(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Run a shell command and return structured output."""
    proc = subprocess.run(
        shlex.split(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": command,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def ensure_dir(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p