"""Factory for registering all built-in hooks.

Call `register_builtin_hooks(hook_manager)` during agent setup to wire up
all the default lifecycle hooks.
"""

from __future__ import annotations

from .hook_manager import HookManager
from .builtin_memory_auto_store import register_memory_auto_store
from .builtin_tool_validation import register_tool_validation
from .builtin_metrics_logger import register_tool_metrics
from .builtin_error_notifier import register_error_notifier


def register_builtin_hooks(
    hook_manager: HookManager,
    base_priority: int = 0,
) -> HookManager:
    """Register all built-in hooks into the given HookManager.

    Priority ordering:
        base_priority + 10  -- validation (fires first, can flag issues)
        base_priority       -- metrics, notifier (fire after validation)
        base_priority       -- memory auto-store (fires last, stores results)
    """

    # Validation runs first -- it inspects results before downstream hooks
    register_tool_validation(hook_manager, priority=base_priority + 10)

    # Metrics and error notifier
    register_tool_metrics(hook_manager, priority=base_priority)
    register_error_notifier(hook_manager, priority=base_priority)

    # Memory auto-store runs last after all validation
    register_memory_auto_store(hook_manager, priority=base_priority)

    return hook_manager
