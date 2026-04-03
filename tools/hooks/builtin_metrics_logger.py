"""Built-in hook: Tool Performance Metrics Logger.

Tracks tool execution metrics (latency, error rate, result size) per tool,
per hook point, and in aggregate. Logs periodic summaries to the Hermes
log stream. Replaces scattered print() calls with structured metrics.

Triggered on: tool.afterExecute, agent.afterStop, cron.afterRun
"""

from __future__ import annotations

import collections
import logging
import time
from typing import Dict, List, Optional

from .hook_types import HookContext, HookResult
from .hook_manager import HookManager

logger = logging.getLogger(__name__)


class ToolMetricAccumulator:
    """Thread-local accumulating metrics for tool performance."""

    def __init__(self):
        # per-tool: list of (duration, is_error, result_bytes)
        self._tool_stats: Dict[str, Dict] = collections.defaultdict(
            lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
        )
        self._session_start = time.time()

    def record(self, tool_name: str, duration_ms: float, is_error: bool,
               result_bytes: int):
        s = self._tool_stats[tool_name]
        s["count"] += 1
        s["total_ms"] += duration_ms
        s["max_ms"] = max(s["max_ms"], duration_ms)
        if is_error:
            s["errors"] += 1

    def summary_lines(self) -> List[str]:
        lines = [
            f"=== Tool Performance Summary (session: {time.time() - self._session_start:.0f}s) ==="
        ]
        # Sort by total time descending
        sorted_tools = sorted(
            self._tool_stats.items(),
            key=lambda x: x[1]["total_ms"],
            reverse=True,
        )
        for tool, s in sorted_tools:
            avg_ms = s["total_ms"] / s["count"] if s["count"] > 0 else 0
            error_pct = (s["errors"] / s["count"] * 100) if s["count"] > 0 else 0
            lines.append(
                f"  {tool:25s} calls={s['count']:>4d}  "
                f"total={s['total_ms']:>8.0f}ms  avg={avg_ms:>8.1f}ms  "
                f"max={s['max_ms']:>8.0f}ms  errors={s['errors']}({error_pct:.0f}%)"
            )

        total_calls = sum(s["count"] for s in self._tool_stats.values())
        total_errors = sum(s["errors"] for s in self._tool_stats.values())
        total_ms = sum(s["total_ms"] for s in self._tool_stats.values())
        lines.append(
            f"  {'TOTAL':25s} calls={total_calls:>4d}  "
            f"total={total_ms:>8.0f}ms  errors={total_errors}"
        )
        return lines


# Module-level singleton -- created lazily to avoid import-time side effects
_accumulator: Optional[ToolMetricAccumulator] = None


def _get_accumulator() -> ToolMetricAccumulator:
    global _accumulator
    if _accumulator is None:
        _accumulator = ToolMetricAccumulator()
    return _accumulator


def register_tool_metrics(hook_manager: HookManager, priority: int = 0) -> None:
    """Register the tool performance metrics hook."""

    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_record_tool_metrics,
        name="tool-metrics-logger",
        description="Accumulate tool execution performance metrics",
        priority=priority,  # Low priority, best-effort
        metadata={"metrics": True},
    )

    hook_manager.register(
        hook_point="agent.afterStop",
        handler=_log_metrics_summary,
        name="metrics-session-summary",
        description="Log accumulated tool metrics summary at agent shutdown",
        priority=priority,
    )


def _record_tool_metrics(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.afterExecute. Record metrics for this tool call."""
    if not ctx.tool_name:
        return None

    duration_ms = (ctx.tool_duration or 0) * 1000
    is_error = ctx.tool_error is not None
    result_bytes = len(ctx.tool_result) if ctx.tool_result else 0

    acc = _get_accumulator()
    acc.record(ctx.tool_name, duration_ms, is_error, result_bytes)

    # Log slow tools immediately (over 10s)
    if duration_ms > 10_000:
        logger.warning(
            "Slow tool: %s took %.1fs", ctx.tool_name, duration_ms / 1000
        )

    return None


def _log_metrics_summary(ctx: HookContext) -> Optional[HookResult]:
    """Fire on agent.afterStop. Print the accumulated metrics summary."""
    acc = _get_accumulator()
    for line in acc.summary_lines():
        logger.info(line)

    return None
