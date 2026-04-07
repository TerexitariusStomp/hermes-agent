"""Atlas-GIC integration wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from extensions.base import run_command


class AtlasGICExtension:
    """Run Darwinian prompt-evolution trading generations."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def run_generation(self, num_days: int, initial_agents: list[dict[str, Any]]) -> dict[str, Any]:
        agents_file = self.repo_path / "tmp-initial-agents.json"
        agents_file.write_text(__import__("json").dumps(initial_agents), encoding="utf-8")
        steps = [
            run_command(f"python atlas.py spawn --agents {agents_file}", cwd=self.repo_path),
            run_command(f"python atlas.py run --days {num_days}", cwd=self.repo_path),
            run_command("python atlas.py evaluate", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}

    def update(self) -> dict[str, Any]:
        steps = [
            run_command("git fetch origin --prune", cwd=self.repo_path),
            run_command("git pull --ff-only origin main", cwd=self.repo_path),
            run_command("uv sync", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}