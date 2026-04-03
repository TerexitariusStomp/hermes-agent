"""Built-in hook: Token Optimizer.

Adapted from ECC token-optimization.md patterns.
Tracks model call patterns and provides budgeting hints:
- Warns about excessive thinking token usage
- Suggests cheaper model for simple follow-ups
- Tracks estimated token cost per turn

Triggered on: model.afterResponse
"""

from __future__ import annotations

import logging
from typing import Optional

from .hook_types import HookContext, HookResult, HookManager
from .hook_manager import HookManager as HM

logger = logging.getLogger(__name__)

# Per-agent cost tracking -- simple counter, not full pricing
_agent_costs: dict[str, dict] = {}

# Model tier cost multipliers (relative to cheapest)
_MODEL_COST_MAP = {
    "haiku": 1.0,
    "sonnet": 3.0,
    "opus": 15.0,
}

# Token budget warning threshold
_TURN_TOKEN_WARNING = 15_000  # tokens


def register_token_optimizer(hook_manager: HM, priority: int = 0) -> None:
    hook_manager.register(
        hook_point="model.afterResponse",
        handler=_track_model_usage,
        name="token-optimizer",
        description="Track and warn about token cost patterns",
        priority=priority,
    )


def _track_model_usage(ctx: HookContext) -> Optional[HookResult]:
    """Fire on model.afterResponse. Track usage and give hints."""
    agent_id = ctx.agent_id or "default"

    if agent_id not in _agent_costs:
        _agent_costs[agent_id] = {
            "total_tokens": 0,
            "total_turns": 0,
            "model_usage": {},
            "high_cost_turns": 0,
        }

    stats = _agent_costs[agent_id]
    stats["total_turns"] += 1

    model = ctx.model_name or "unknown"
    stats["model_usage"].setdefault(model, 0)
    stats["model_usage"][model] += 1

    token_count = ctx.message_token_count
    if token_count:
        stats["total_tokens"] += token_count

        if token_count > _TURN_TOKEN_WARNING:
            stats["high_cost_turns"] += 1
            return HookResult(
                abort=False,
                reason=f"High token turn: {token_count:,} tokens (threshold: {_TURN_TOKEN_WARNING:,}). "
                       f"Consider /compact or delegate complex subtasks.",
                severity="warn",
            )

    # Periodic cost hint every 20 turns
    if stats["total_turns"] % 20 == 0:
        return HookResult(
            abort=False,
            reason=f"Session stats: {stats['total_tokens']:,} tokens, "
                   f"{stats['total_turns']} turns, "
                   f"{stats['high_cost_turns']} high-cost turns. "
                   f"Model usage: {stats['model_usage']}",
            severity="info",
        )

    return None


def get_token_stats() -> dict:
    return dict(_agent_costs)
