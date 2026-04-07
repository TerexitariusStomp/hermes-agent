"""AEON integration wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from extensions.base import ensure_dir, run_command


class AEONExtension:
    """Wrap AEON skill, schedule, log, and update operations."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def create_or_update_skill_yaml(self, skill_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        skills_dir = ensure_dir(self.repo_path / "skills")
        target = skills_dir / f"{skill_name}.yaml"
        target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return {"success": True, "path": str(target)}

    def trigger_scheduled_run(self, workflow: str, ref: str = "main") -> dict[str, Any]:
        return run_command(
            f"gh workflow run {workflow} --ref {ref}",
            cwd=self.repo_path,
        )

    def fetch_logs(self, run_id: str | None = None, workflow: str | None = None) -> dict[str, Any]:
        if run_id:
            return run_command(f"gh run view {run_id} --log", cwd=self.repo_path)
        if workflow:
            return run_command(f"gh run list --workflow {workflow}", cwd=self.repo_path)
        return run_command("gh run list", cwd=self.repo_path)

    def update(self) -> dict[str, Any]:
        """Merge from AEON upstream and trigger schedule sync."""
        steps = [
            run_command("git remote get-url upstream", cwd=self.repo_path),
            run_command("git remote add upstream https://github.com/aaronjmars/aeon.git", cwd=self.repo_path),
            run_command("git fetch upstream", cwd=self.repo_path),
            run_command("git merge upstream/main", cwd=self.repo_path),
            run_command("gh workflow list", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps[2:]), "steps": steps}