"""Auto-research integration wrapper."""

from __future__ import annotations

from pathlib import Path

from extensions.base import run_command


class AutoResearchExtension:
    """Run the self-evolving train/evaluate loop in a containerized flow."""

    def autores_run(self, repo_path: str | Path, minutes: int = 5) -> dict:
        repo = Path(repo_path).expanduser()
        steps = [
            run_command("git status --porcelain", cwd=repo),
            run_command("docker compose up -d", cwd=repo),
            run_command(f"timeout {max(minutes, 1)}m python train.py", cwd=repo),
            run_command("python evaluate.py", cwd=repo),
        ]
        # If evaluation fails, revert train.py changes.
        accepted = steps[-1]["ok"]
        if not accepted:
            steps.append(run_command("git checkout -- train.py", cwd=repo))
        return {"success": accepted, "accepted": accepted, "steps": steps}

    def update(self, repo_path: str | Path) -> dict:
        repo = Path(repo_path).expanduser()
        steps = [
            run_command("git fetch origin --prune", cwd=repo),
            run_command("git pull --ff-only origin main", cwd=repo),
            run_command("uv sync", cwd=repo),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}