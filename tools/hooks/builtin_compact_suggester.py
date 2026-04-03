"""Built-in hook: Strategic Compact Suggester.

Adapted from ECC's suggest-compact hook pattern.
Tracks tool call count per conversation turn and suggests
context compaction at logical intervals.

Strategy (from ECC token-optimization.md):
- Suggest compact after ~50 tool calls
- Suggest compact after exploration phase (before implementation)
- Suggest compact at turn milestones

Triggered on: tool.afterExecute
"""

from __future__ import annotations

import logging
from typing import Optional

from .hook_types import HookContext, HookResult, HookManager
from .hook_manager import HookManager as HM

logger = logging.getLogger(__name__)

# Track tool calls per agent -- module-level since HookManager can't store
# state that survives across agent instances
_tool_call_counters: dict[str, int] = {}

# Compaction threshold: suggest compact every N tool calls
COMPACT_THRESHOLD = 50


def register_compact_suggester(hook_manager: HM, priority: int = 0) -> None:
    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_count_and_suggest,
        name="compact-suggester",
        description="Track tool calls and suggest compaction at thresholds",
        priority=priority,
    )


def _count_and_suggest(ctx: HookContext) -> Optional[HookResult]:
    agent_id = ctx.agent_id or "default"
    _tool_call_counters[agent_id] = _tool_call_counters.get(agent_id, 0) + 1
    count = _tool_call_counters[agent_id]

    # Suggest at each threshold boundary
    if count % COMPACT_THRESHOLD == 0:
        logger.info(
            "[COMPACT-HINT] %d tool calls executed. Consider running /compact "
            "at this logical breakpoint to maintain response quality.",
            count,
        )
        ctx.extras["compact_hint"] = {
            "tool_call_count": count,
            "next_threshold": count + COMPACT_THRESHOLD,
        }
        return HookResult(
            abort=False,
            reason=f"Hint: {count} tool calls executed -- consider compacting context",
            severity="info",
        )

    return None
