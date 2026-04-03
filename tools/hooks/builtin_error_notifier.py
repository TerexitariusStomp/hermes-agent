"""Built-in hook: Tool Error Notifier.

Detects tool errors and flags them with structured context for downstream
consumers (Telegram alerting, self-diagnosis, auto-recovery attempts).

Triggered on: tool.onError, cron.onError
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional

from .hook_types import HookContext, HookResult
from .hook_manager import HookManager

logger = logging.getLogger(__name__)


def register_error_notifier(hook_manager: HookManager, priority: int = 0) -> None:
    """Register the tool error notifier hook."""

    hook_manager.register(
        hook_point="tool.onError",
        handler=_on_tool_error,
        name="tool-error-notifier",
        description="Log and flag tool errors for downstream handling",
        priority=priority,
        metadata={"error_handling": True},
    )

    hook_manager.register(
        hook_point="cron.onError",
        handler=_on_cron_error,
        name="cron-error-notifier",
        description="Log and flag cron job errors",
        priority=priority,
    )


def _on_tool_error(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.onError. Log the error with full context."""
    if not ctx.tool_name or not ctx.tool_error:
        return None

    err = ctx.tool_error
    error_msg = str(err)
    error_type = type(err).__name__

    args_summary = ""
    if ctx.tool_args:
        args_summary = ", ".join(
            f"{k}={v!r}" for k, v in list(ctx.tool_args.items())[:5]
        )

    logger.error(
        "TOOL ERROR [%s]: %s(%s) -> %s: %s",
        ctx.tool_name,
        ctx.tool_name,
        args_summary,
        error_type,
        error_msg[:500],
    )

    # Log full traceback if available
    if isinstance(err, Exception) and hasattr(err, "__traceback__") and err.__traceback__:
        tb_lines = traceback.format_exception(type(err), err, err.__traceback__)
        logger.debug("".join(tb_lines)[:2000])

    # Set context extras for downstream consumers
    ctx.extras["tool_error_detail"] = {
        "tool": ctx.tool_name,
        "error_type": error_type,
        "error_msg": error_msg,
        "args_keys": list(ctx.tool_args.keys()) if ctx.tool_args else [],
        "duration": ctx.tool_duration,
    }

    return HookResult(
        abort=False,
        reason=f"Tool '{ctx.tool_name}' failed: {error_type}: {error_msg[:200]}",
        severity="error",
    )


def _on_cron_error(ctx: HookContext) -> Optional[HookResult]:
    """Fire on cron.onError."""
    job_name = ctx.cron_job_name or ctx.cron_job_id or "unknown"

    if not ctx.tool_error:
        return None

    err = ctx.tool_error
    logger.error(
        "CRON ERROR [%s]: %s: %s",
        job_name,
        type(err).__name__,
        str(err)[:500],
    )

    return HookResult(
        abort=False,
        reason=f"Cron job '{job_name}' failed: {type(err).__name__}",
        severity="error",
    )
