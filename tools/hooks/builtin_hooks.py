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
from .builtin_continuous_observation import register_continuous_observation
from .builtin_compact_suggester import register_compact_suggester
from .builtin_quality_gates import register_quality_gates
from .builtin_token_optimizer import register_token_optimizer
from .builtin_session_hooks import register_session_hooks


def register_builtin_hooks(
    hook_manager: HookManager,
    base_priority: int = 0,
) -> HookManager:
    """Register all built-in hooks into the given HookManager.

    Priority ordering (first to last within each category):
        +15: quality gates (block before validation)
        +10: tool validation (inspects results early)
        +5:  continuous observation (learn from everything)
        0:   metrics, notifier, compact suggester
        -5:  syntax check, memory auto-store (run last)
    """

    # Quality gates -- run first so they can block bad commands
    register_quality_gates(hook_manager, priority=base_priority + 15)

    # Validation runs before most others to flag issues
    register_tool_validation(hook_manager, priority=base_priority + 10)

    # Continuous observation captures everything regardless
    register_continuous_observation(hook_manager, priority=base_priority + 5)

    # Core diagnostics
    register_tool_metrics(hook_manager, priority=base_priority)
    register_error_notifier(hook_manager, priority=base_priority)
    register_compact_suggester(hook_manager, priority=base_priority)
    register_token_optimizer(hook_manager, priority=base_priority)

    # Session lifecycle
    register_session_hooks(hook_manager, priority=base_priority)

    # Memory auto-store runs last after all validation
    register_memory_auto_store(hook_manager, priority=base_priority - 5)

    return hook_manager
