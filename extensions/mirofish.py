"""MiroFish integration wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from extensions.base import run_command


class MiroFishExtension:
    """Wrap MiroFish prediction-simulation workflows."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def simulate(self, seed_info: dict[str, Any], horizon: int) -> dict[str, Any]:
        seed_path = self.repo_path / "tmp-seed.json"
        seed_path.write_text(__import__("json").dumps(seed_info), encoding="utf-8")
        steps = [
            run_command("uv sync", cwd=self.repo_path),
            run_command("npm install", cwd=self.repo_path),
            run_command(f"uv run python scripts/build_graph.py --seed {seed_path}", cwd=self.repo_path),
            run_command(f"uv run python scripts/simulate.py --horizon {horizon}", cwd=self.repo_path),
            run_command("uv run python scripts/report.py", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "report_path": str(self.repo_path / "reports"), "steps": steps}

    def update(self) -> dict[str, Any]:
        steps = [
            run_command("git fetch origin --prune", cwd=self.repo_path),
            run_command("git pull --ff-only origin main", cwd=self.repo_path),
            run_command("uv sync", cwd=self.repo_path),
            run_command("npm install", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}