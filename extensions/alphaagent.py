"""AlphaAgent integration wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extensions.base import run_command


class AlphaAgentExtension:
    """Wrap alpha mining and backtesting CLI flows."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).expanduser()

    def alpha_mine(self, data_config: dict[str, Any]) -> dict[str, Any]:
        cfg = self.repo_path / "tmp-data-config.json"
        cfg.write_text(json.dumps(data_config), encoding="utf-8")
        result = run_command(f"alphaagent mine --config {cfg}", cwd=self.repo_path)
        return {"success": result["ok"], "factors": result["stdout"], "raw": result}

    def alpha_backtest(self, factors: list[dict[str, Any]], portfolio: dict[str, Any]) -> dict[str, Any]:
        factors_path = self.repo_path / "tmp-factors.json"
        portfolio_path = self.repo_path / "tmp-portfolio.json"
        factors_path.write_text(json.dumps(factors), encoding="utf-8")
        portfolio_path.write_text(json.dumps(portfolio), encoding="utf-8")
        result = run_command(
            f"alphaagent backtest --factors {factors_path} --portfolio {portfolio_path}",
            cwd=self.repo_path,
        )
        return {"success": result["ok"], "backtest": result["stdout"], "raw": result}

    def update(self) -> dict[str, Any]:
        steps = [
            run_command("git fetch origin --prune", cwd=self.repo_path),
            run_command("git pull --ff-only origin main", cwd=self.repo_path),
            run_command("uv sync", cwd=self.repo_path),
        ]
        return {"success": all(step["ok"] for step in steps), "steps": steps}