"""OpenViking integration wrapper."""

from __future__ import annotations

import json
from pathlib import Path

from extensions.base import run_command


class OpenVikingExtension:
    """OpenViking index/search and update hooks."""

    def openviking_index(self, context_files: list[str]) -> dict:
        payload = json.dumps(context_files)
        return run_command(f"openviking index --files {payload!r}")

    def openviking_search(self, query: str) -> dict:
        return run_command(f"openviking search --query {query!r}")

    def update(self) -> dict:
        step = run_command("python -m pip install openviking --upgrade --force-reinstall")
        return {"success": step["ok"], "steps": [step]}