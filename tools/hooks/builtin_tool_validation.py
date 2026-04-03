"""Built-in hook: Tool Result Validation.

Validates tool execution results against expected patterns:
- Detects empty results from tools that should produce output
- Warns about oversized results that may hit context limits
- Validates that destructive terminal commands produced expected exit codes
- Flags tools that return errors with specific patterns

Triggered on: tool.afterExecute
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from .hook_types import HookContext, HookResult
from .hook_manager import HookManager

logger = logging.getLogger(__name__)


# Patterns indicating a tool returned misleading success
_FALSE_POSITIVE_PATTERNS = [
    re.compile(r"^\s*$"),  # empty result
    re.compile(r"^No results? found", re.IGNORECASE),
    re.compile(r"^0 matches?", re.IGNORECASE),
    re.compile(r"empty (result|output|list|response)", re.IGNORECASE),
]

# Maximum acceptable result sizes before flagging warning
_RESULT_SIZE_THRESHOLDS = {
    "terminal": 50_000,       # 50KB for terminal output
    "read_file": 30_000,      # 30KB for file reads
    "search_files": 20_000,   # 20KB for search results
    "web_extract": 40_000,    # 40KB for web extraction
    "browser_snapshot": 50_000,
    "default": 25_000,        # 25KB default
}

# Destructive commands that should verify success
_DESTRUCTIVE_COMMANDS = re.compile(
    r"^\s*(rm\s|git\s+(reset|clean)\s|mv\s|shred\s|truncate\s)",
)

# Success indicators for file modification commands
_SUCCESS_INDICATORS = re.compile(
    r"(updated|modified|created|written|deleted|replaced|success|ok)",
    re.IGNORECASE,
)


def register_tool_validation(hook_manager: HookManager, priority: int = 0) -> None:
    """Register the tool result validation hook."""

    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_validate_tool_result,
        name="tool-result-validator",
        description="Validate tool results for correctness and size",
        priority=priority + 10,  # Run before memory auto-store
        metadata={"validation": True},
    )

    hook_manager.register(
        hook_point="cron.afterRun",
        handler=_validate_cron_result,
        name="cron-result-validator",
        description="Validate cron job execution results",
        priority=priority,
    )


def _validate_tool_result(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.afterExecute. Validate the result."""
    if not ctx.tool_name or ctx.tool_error:
        # Error cases are handled by the error-notifier hook
        return None

    tool_name = ctx.tool_name
    result = ctx.tool_result or ""
    duration = ctx.tool_duration or 0

    issues = []

    # Check for false positive success patterns
    if not ctx.tool_error:
        for pattern in _FALSE_POSITIVE_PATTERNS:
            if pattern.search(result):
                issues.append(f"Result may be empty/no-data: {result[:100]}")
                break

    # Check result size
    threshold = _RESULT_SIZE_THRESHOLDS.get(tool_name, _RESULT_SIZE_THRESHOLDS["default"])
    if len(result) > threshold:
        issues.append(
            f"Result oversized ({len(result):,} chars > {threshold:,} threshold). "
            f"May cause context window issues."
        )

    # Validate destructive commands succeeded
    if tool_name == "terminal" and ctx.tool_args:
        cmd = ctx.tool_args.get("command", "")
        if _DESTRUCTIVE_COMMANDS.match(cmd):
            # Check for success indicators
            if not _SUCCESS_INDICATORS.search(result):
                # Check exit code if available
                if "exit_code" not in result and "error" in result.lower():
                    issues.append(
                        f"Destructive command '{cmd[:60]}' may not have succeeded - "
                        f"no success indicators found"
                    )

    # Check unusually slow execution
    slow_thresholds = {"terminal": 60.0, "web_search": 30.0, "default": 45.0}
    slow_threshold = slow_thresholds.get(tool_name, slow_thresholds["default"])
    if duration > slow_threshold:
        issues.append(
            f"Slow execution: {duration:.1f}s (threshold: {slow_threshold:.0f}s)"
        )

    if issues:
        reason = "; ".join(issues)
        return HookResult(
            abort=False,
            reason=reason,
            severity="warn",
        )

    return None


def _validate_cron_result(ctx: HookContext) -> Optional[HookResult]:
    """Fire on cron.afterRun for job validation."""
    if ctx.tool_error:
        return HookResult(
            abort=False,
            reason=f"Cron job '{ctx.cron_job_name or ctx.cron_job_id}' failed",
            severity="error",
        )

    return None
