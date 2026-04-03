"""Meta-Harness integration — auto-capture execution traces during agent runs.

This module provides drop-in hooks that capture:
- Every tool call (args, result, duration, success/failure)
- Session-level stats (tokens, iterations, tool success rate)
- Failure patterns with causal classification

These are written to ~/.hermes/meta_harness/experiments/{date}/{session}.jsonl
so the Pareto Optimizer and Subconscious can reason from real execution data.

Usage: Call register_trace_hooks(agent_instance) once during AIAgent init.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes-meta-harness")


def register_trace_hooks(agent) -> None:
    """Wrap the agent's tool execution and completion with trace logging.

    This is called from AIAgent.__init__ after _setup_hooks().
    It wraps the existing callback system without changing tool execution paths.
    """
    session_id = getattr(agent, "session_id", None) or getattr(agent, "_agent_id", "unknown")
    model_name = getattr(agent, "model", "unknown")

    # Track per-session tool calls
    _session_tool_calls: List[Dict[str, Any]] = []
    _session_start = time.time()

    def _log_tool_call(tool_name, args, result, duration_ms, is_error):
        """Log a single tool execution to the trace filesystem."""
        try:
            trace = {
                "trace_id": f"{session_id}-{tool_name}-{len(_session_tool_calls)}",
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "phase": "tool_call",
                "tool": tool_name,
                "args": _safe_truncate(json.dumps(args), 1000),
                "result_success": not is_error,
                "duration_ms": round(duration_ms, 1),
                "result_length": len(result) if result else 0,
                "result_snippet": result[:300] if result else "",
                "model": model_name,
            }

            # Classify failure if needed
            if is_error:
                trace["failure_type"] = _classify_error(tool_name, result)
            else:
                trace["failure_type"] = None

            _session_tool_calls.append(trace)
            _append_trace(session_id, trace)

        except Exception:
            logger.debug("Trace logging failed for %s", tool_name, exc_info=True)

    # Wrap existing callbacks if they exist
    orig_start = agent.tool_start_callback
    orig_complete = agent.tool_complete_callback
    _tool_start_times: Dict[str, float] = {}

    def wrapped_start(tool_id, name, args):
        _tool_start_times[tool_id] = time.time()
        if orig_start:
            try:
                return orig_start(tool_id, name, args)
            except Exception:
                pass

    def wrapped_complete(tool_id, name, args, result):
        start = _tool_start_times.pop(tool_id, None)
        duration_ms = (time.time() - start) * 1000 if start else 0
        is_error = (result or "").startswith("Error ") or (result or "").startswith("Failed")
        _log_tool_call(name, args, result, duration_ms, is_error)
        if orig_complete:
            try:
                return orig_complete(tool_id, name, args, result)
            except Exception:
                pass

    agent.tool_start_callback = wrapped_start
    agent.tool_complete_callback = wrapped_complete

    # Hook into session end for summary logging
    orig_fire_session = getattr(agent, "_fire_session_end_hook", None)

    def wrapped_session_end(completed=False, interrupted=False, final_response=""):
        # Log session summary
        _log_session_summary(
            session_id=session_id,
            model=model_name,
            tool_calls=list(_session_tool_calls),
            duration_s=time.time() - _session_start,
        )
        _session_tool_calls.clear()

        # Call original hook
        if orig_fire_session:
            orig_fire_session(completed, interrupted, final_response)

    agent._fire_session_end_hook = wrapped_session_end

    logger.info("Meta-harness trace hooks registered for session %s", session_id)


def _append_trace(session_id: str, trace: Dict[str, Any]) -> None:
    """Append a trace record to the daily experiment file."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trace_dir = Path.home() / ".hermes" / "meta_harness" / "experiments" / today
        trace_dir.mkdir(parents=True, exist_ok=True)

        trace_file = trace_dir / f"{session_id}.jsonl"
        with open(trace_file, "a") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")

    except Exception:
        logger.debug("Failed to write trace record", exc_info=True)


def _log_session_summary(
    session_id: str,
    model: str = "unknown",
    tool_calls: List[Dict[str, Any]] = None,
    duration_s: float = 0,
):
    """Write session-level summary to trace filesystem."""
    if not tool_calls:
        tool_calls = []

    total_calls = len(tool_calls)
    error_count = sum(1 for t in tool_calls if not t.get("total_success"))
    success_rate = round(((total_calls - error_count) / total_calls * 100) if total_calls else 0, 1)

    summary = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": "agent_loop",
        "model": model,
        "tool_calls_count": total_calls,
        "tool_errors_count": error_count,
        "tool_success_rate": success_rate,
        "duration_seconds": round(duration_s, 1),
    }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trace_dir = Path.home() / ".hermes" / "meta_harness" / "experiments" / today
    trace_dir.mkdir(parents=True, exist_ok=True)

    trace_file = trace_dir / f"{session_id}.jsonl"
    with open(trace_file, "a") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")


def _safe_truncate(text: str, max_len: int) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f" ... [{len(text)} chars]"


def _classify_error(tool_name: str, result: str) -> str:
    """Classify a tool failure into an actionable category."""
    text = (result or "").lower()

    patterns = {
        "path_not_found": [r"no such file", r"file not found", r"does not exist"],
        "permission_denied": [r"permission denied", r"access denied", r"forbidden"],
        "syntax_error": [r"SyntaxError", r"invalid syntax"],
        "timeout_exceeded": [r"timeout", r"timed out"],
        "output_too_large": [r"too large", r"exceeded", r"truncated"],
        "json_parse_error": [r"JSONDecodeError", r"invalid json"],
        "pattern_not_matched": [r"not found", r"pattern not found", r"no match", r"0 matches"],
        "command_failed": [r"exit_code", r"command failed"],
        "network_error": [r"connection", r"network", r"401", r"403", r"429"],
    }

    import re
    for category, patterns_list in patterns.items():
        for pat in patterns_list:
            if pat.lower() in text:
                return category

    return "unknown"
