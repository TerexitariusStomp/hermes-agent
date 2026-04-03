"""Integration adapters for wiring hooks into Hermes' existing callback system.

Hermes already has callback attributes on AIAgent:
  - tool_progress_callback(name, preview, args)
  - tool_start_callback(tool_id, name, args)
  - tool_complete_callback(tool_id, name, args, result)

This module produces wrapper functions that bridge between those callbacks
and the HookManager.fire() calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from .hook_manager import HookManager
from .hook_types import HookContext, HookPoint

logger = logging.getLogger(__name__)


def make_tool_start_hook(hook_manager: HookManager, agent_id: str = "") -> Callable:
    """Create a tool_start_callback that fires tool.beforeExecute hooks.

    Returns a sync callable compatible with Hermes' callback signature:
        callback(tool_id: str, name: str, args: dict)
    """

    def _on_tool_start(tool_id: str, name: str, args: Dict[str, Any]) -> None:
        ctx = HookContext(
            agent_id=agent_id,
            tool_name=name,
            tool_args=args,
        )
        try:
            # Fire hooks synchronously (they should be fast for beforeExecute)
            results = hook_manager.fire_sync(HookPoint.TOOL_BEFORE_EXECUTE, ctx)
            for r in results:
                if r.abort:
                    logger.warning(
                        "Hook '%s' aborted tool '%s': %s",
                        r.reason[:100] if r.reason else "unknown", name,
                    )
        except Exception:
            logger.error("Error firing before-execute hooks for '%s'", name, exc_info=True)

    return _on_tool_start


def make_tool_complete_hook(
    hook_manager: HookManager,
    agent_id: str = "",
) -> Callable:
    """Create a tool_complete_callback that fires tool.afterExecute / tool.onError hooks.

    Returns a sync callable compatible with Hermes' callback signature:
        callback(tool_id: str, name: str, args: dict, result: str)
    """

    def _on_tool_complete(
        tool_id: str,
        name: str,
        args: Dict[str, Any],
        result: str,
    ) -> None:
        duration: Optional[float] = None  # Not available via callback, agent tracks it
        is_error = result.startswith("Error ") or result.startswith("Failed")

        ctx = HookContext(
            agent_id=agent_id,
            tool_name=name,
            tool_args=args,
            tool_result=result,
            tool_duration=duration,
            tool_error=Exception(result) if is_error else None,
        )

        fire_point = HookPoint.TOOL_ON_ERROR if is_error else HookPoint.TOOL_AFTER_EXECUTE

        try:
            # Fire async hooks for afterExecute (they may do I/O)
            results = hook_manager.fire_sync(fire_point, ctx)
            for r in results:
                if r.is_significant:
                    logger.debug(
                        "Hook result at '%s': %s", fire_point, r.reason,
                    )
        except Exception:
            logger.error(
                "Error firing post-execute hooks for '%s'", name, exc_info=True,
            )

    return _on_tool_complete


def make_on_tool_error_hook(
    hook_manager: HookManager,
    agent_id: str = "",
) -> Callable:
    """Create a hook that fires when tool execution raises an exception.

    Usage in AIAgent._execute_tool_calls:
        except Exception as e:
            error_hook(tool_name, args, e)

    Returns:
        callback(name, args, error)
    """

    def _on_error(name: str, args: Dict[str, Any], error: Exception) -> None:
        ctx = HookContext(
            agent_id=agent_id,
            tool_name=name,
            tool_args=args,
            tool_error=error,
        )

        try:
            hook_manager.fire_sync(HookPoint.TOOL_ON_ERROR, ctx)
        except Exception:
            logger.error(
                "Error firing on-error hooks for '%s'", name, exc_info=True,
            )

    return _on_error
