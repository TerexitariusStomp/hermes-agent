"""Built-in hook: Continuous Observation Logger.

Adapted from ECC's continuous-learning-v2 observe.sh hook pattern.
Logs every tool execution to a rolling JSONL observation file for
later pattern extraction and skill evolution analysis.

Each observation captures:
- tool_name, args summary, result quality (error/success/timeout)
- duration, iteration context, turn number

Triggered on: tool.afterExecute, tool.onError
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .hook_types import HookContext, HookManager
from .hook_manager import HookManager as HM

logger = logging.getLogger(__name__)

_OBSERVATION_DIR = Path.home() / ".hermes" / "logs" / "observations"
_MAX_OBSERVATIONS_PER_FILE = 10_000
_ROLLOVER_COUNT = 5  # Keep last N files



def register_continuous_observation(hook_manager: HM, priority: int = 0) -> None:
    """Register the continuous observation hooks."""
    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_observe_successful,
        name="continuous-observation",
        description="Log tool execution observations for pattern learning",
        priority=priority,
        metadata={"observation": True, "log_path": str(_OBSERVATION_DIR)},
    )


def _observe_successful(ctx: HookContext) -> None:
    """Fire on tool.afterExecute. Record structured observation."""
    if not ctx.tool_name:
        return None

    _log_observation(ctx)
    return None


def _log_observation(ctx: HookContext) -> None:
    """Append observation to rolling JSONL file."""
    try:
        _OBSERVATION_DIR.mkdir(parents=True, exist_ok=True)

        # Determine current observation file
        files = sorted(_OBSERVATION_DIR.glob("observations-*.jsonl"))
        current_file = files[-1] if files else None

        if current_file and current_file.stat().st_size > 50 * 1024 * 1024:
            # >50MB, rotate
            if len(files) >= _ROLLOVER_COUNT:
                files[0].unlink()
            current_file = None

        if current_file is None:
            # Find next numbered file
            existing_nums = [int(f.stem.split("-", 1)[-1]) for f in files if f.stem.startswith("observations-")]
            next_num = max(existing_nums, default=-1) + 1
            current_file = _OBSERVATION_DIR / f"observations-{next_num:04d}.jsonl"

        # Build observation record
        args_summary = {}
        if ctx.tool_args:
            for k, v in ctx.tool_args.items():
                if k not in ("content", "text", "embedding"):
                    args_summary[k] = str(v)[:200]

        is_error = (ctx.tool_result or "").startswith("Error ") or ctx.tool_error is not None
        is_empty = not (ctx.tool_result or "").strip()
        result_quality = "error" if is_error else ("empty" if is_empty else "success")

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": ctx.agent_id,
            "tool": ctx.tool_name,
            "args_keys": list(ctx.tool_args.keys()) if ctx.tool_args else [],
            "args_summary": args_summary,
            "quality": result_quality,
            "duration_s": round(ctx.tool_duration or 0, 3),
            "result_bytes": len(ctx.tool_result or ""),
            "turn": ctx.turn_number,
            "iteration": ctx.iteration_count,
        }

        with open(current_file, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    except Exception:
        logger.debug("Failed to log observation for %s", ctx.tool_name or "unknown", exc_info=True)


def get_observation_count() -> int:
    """Return total observation count across all files."""
    if not _OBSERVATION_DIR.exists():
        return 0
    total = 0
    for f in _OBSERVATION_DIR.glob("observations-*.jsonl"):
        total += sum(1 for _ in open(f))
    return total
