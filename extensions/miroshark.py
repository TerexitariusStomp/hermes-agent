"""MiroShark integration wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from extensions.base import run_command


class MiroSharkExtension:
    """Wrap MiroShark graph/simulation workflows."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def simulate(self, uploaded_docs: list[str], days: int) -> dict[str, Any]:
        docs = " ".join(uploaded_docs)
        steps = [
            run_command("docker compose up -d neo4j", cwd=self.repo_path),
            run_command(f"./scripts/build_graph.sh {docs}", cwd=self.repo_path),
            run_command("./scripts/generate_personas.sh", cwd=self.repo_path),
            run_command(f"./scripts/simulate.sh --days {days}", cwd=self.repo_path),
            run_command("./scripts/report.sh", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "report_path": str(self.repo_path / "reports"), "steps": steps}

    def update(self) -> dict[str, Any]:
        steps = [
            run_command("git fetch origin --prune", cwd=self.repo_path),
            run_command("git pull --ff-only origin main", cwd=self.repo_path),
            run_command("docker compose pull", cwd=self.repo_path),
            run_command("docker compose build --no-cache", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}