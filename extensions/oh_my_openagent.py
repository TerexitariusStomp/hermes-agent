"""Oh-My-OpenAgent integration wrapper."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from extensions.base import run_command


class OhMyOpenAgentExtension:
    """Delegate tasks to OmO orchestration runtime and hooks."""

    EVENT_TO_HOOK = {
        "planning": "before_plan",
        "code_generation": "before_codegen",
        "testing": "before_test",
        "debugging": "before_debug",
        "deploy": "before_deploy",
    }

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def execute(self, task_description: str, models: list[str] | None = None) -> dict[str, Any]:
        models = models or []
        model_flags = " ".join(f"--model {m}" for m in models)
        env = os.environ.copy()
        if models:
            env["OMO_MODELS"] = ",".join(models)
        result = run_command(f"bunx oh-my-openagent run {model_flags} --task {task_description!r}", cwd=self.repo_path)
        result["selected_models"] = models
        result["hook_map"] = self.EVENT_TO_HOOK
        return result

    def install_or_update(self) -> dict[str, Any]:
        steps = [
            run_command("bunx oh-my-openagent install", cwd=self.repo_path),
            run_command("bunx oh-my-openagent install --force", cwd=self.repo_path),
        ]
        return {"success": any(step["ok"] for step in steps), "steps": steps}

    def update(self) -> dict[str, Any]:
        return self.install_or_update()