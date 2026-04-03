"""Core types for the Hermes extension point lifecycle hook system.

Modeled after Ruflo's ExtensionPoint pattern but adapted for Python and
the Hermes agent architecture.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

import logging
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Extension Point Names
# ──────────────────────────────────────────────────────────────────────

class HookPoint(str, Enum):
    """Standard extension point names where hooks can attach.

    Naming convention: component.lifecycle_phase
    """

    # Agent lifecycle
    AGENT_BEFORE_START = "agent.beforeStart"
    AGENT_AFTER_STOP = "agent.afterStop"

    # Model inference
    MODEL_BEFORE_CALL = "model.beforeCall"
    MODEL_AFTER_RESPONSE = "model.afterResponse"

    # Tool execution
    TOOL_BEFORE_EXECUTE = "tool.beforeExecute"
    TOOL_AFTER_EXECUTE = "tool.afterExecute"
    TOOL_ON_ERROR = "tool.onError"

    # Message lifecycle
    MESSAGE_BEFORE_SEND = "message.beforeSend"
    MESSAGE_AFTER_RECEIVE = "message.afterReceive"

    # Skill execution
    SKILL_BEFORE_EXECUTE = "skill.beforeExecute"
    SKILL_AFTER_EXECUTE = "skill.afterExecute"

    # Memory operations
    MEMORY_BEFORE_STORE = "memory.beforeStore"
    MEMORY_AFTER_STORE = "memory.afterStore"
    MEMORY_BEFORE_QUERY = "memory.beforeQuery"

    # Cron / scheduled jobs
    CRON_BEFORE_RUN = "cron.beforeRun"
    CRON_AFTER_RUN = "cron.afterRun"
    CRON_ON_ERROR = "cron.onError"

    # Context compression
    CONTEXT_BEFORE_COMPRESS = "context.beforeCompress"
    CONTEXT_AFTER_COMPRESS = "context.afterCompress"

    # Self-improvement
    SELF_IMPROVE_BEFORE_CYCLE = "selfImprove.beforeCycle"
    SELF_IMPROVE_AFTER_CYCLE = "selfImprove.afterCycle"


# ──────────────────────────────────────────────────────────────────────
# Hook Context
# ──────────────────────────────────────────────────────────────────────

@dataclass
class HookContext:
    """Runtime context passed to every hook handler.

    Provides the data a hook needs to make decisions.  Different hook
    points attach different fields, so most values are optional.
    """

    # Always present
    hook_point: str = ""
    timestamp: float = field(default_factory=time.time)
    agent_id: str = ""

    # Tool context
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None
    tool_duration: Optional[float] = None
    tool_error: Optional[Exception] = None

    # Message context
    message_role: Optional[str] = None
    message_content: Optional[str] = None
    message_token_count: Optional[int] = None

    # Skill context
    skill_name: Optional[str] = None
    skill_action: Optional[str] = None

    # Memory context
    memory_content: Optional[str] = None
    memory_target: Optional[str] = None
    memory_query: Optional[str] = None
    memory_results: Optional[List[Any]] = None

    # Cron context
    cron_job_id: Optional[str] = None
    cron_job_name: Optional[str] = None
    cron_prompt: Optional[str] = None

    # Model context
    model_name: Optional[str] = None
    model_request_messages: Optional[List[Dict[str, Any]]] = None
    model_response: Optional[Any] = None

    # Self-improve context
    improvement_type: Optional[str] = None
    improvement_result: Optional[str] = None

    # Agent / conversation context
    conversation_id: Optional[str] = None
    turn_number: Optional[int] = None
    iteration_count: Optional[int] = None

    # Arbitrary extras for custom hooks
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize context for logging or hook state persistence."""
        d: Dict[str, Any] = {
            "hook_point": self.hook_point,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
        }
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_args:
            d["tool_args_keys"] = list(self.tool_args.keys())
        if self.skill_name:
            d["skill_name"] = self.skill_name
        if self.cron_job_name:
            d["cron_job_name"] = self.cron_job_name
        if self.model_name:
            d["model_name"] = self.model_name
        if self.turn_number is not None:
            d["turn_number"] = self.turn_number
        return d


# ──────────────────────────────────────────────────────────────────────
# Handlers and Results
# ──────────────────────────────────────────────────────────────────────

HookHandler = Callable[[HookContext], Awaitable[Optional["HookResult"]]]


@dataclass
class HookResult:
    """Optional result a hook can return to influence agent behavior."""

    # If True, the agent should abort the current operation
    abort: bool = False
    # Human-readable reason (logged, not shown to user)
    reason: str = ""
    # Optional mutation: the hook can modify tool args or content
    mutated_args: Optional[Dict[str, Any]] = None
    # Severity for logging
    severity: str = "info"  # info | warn | error

    @property
    def is_significant(self) -> bool:
        return self.abort or bool(self.mutated_args) or self.severity in ("error", "warn")


@dataclass
class HookRegistration:
    """A single registered hook with metadata."""

    hook_point: str
    handler: HookHandler
    name: str  # Human-readable name
    description: str = ""
    priority: int = 0  # Higher runs first
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────

@dataclass
class HookMetrics:
    """Aggregate metrics for the hook system."""

    total_invocations: int = 0
    total_errors: int = 0
    total_aborts: int = 0
    total_duration_ms: float = 0.0
    per_hook: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def record(self, hook_name: str, duration_ms: float, error: bool = False,
               aborted: bool = False):
        self.total_invocations += 1
        self.total_duration_ms += duration_ms
        if error:
            self.total_errors += 1
        if aborted:
            self.total_aborts += 1

        if hook_name not in self.per_hook:
            self.per_hook[hook_name] = {
                "invocations": 0, "errors": 0,
                "aborts": 0, "total_ms": 0.0,
            }
        h = self.per_hook[hook_name]
        h["invocations"] += 1
        h["total_ms"] += duration_ms
        if error:
            h["errors"] += 1
        if aborted:
            h["aborts"] += 1

    @property
    def avg_latency_ms(self) -> float:
        if self.total_invocations == 0:
            return 0.0
        return self.total_duration_ms / self.total_invocations

    def summary(self) -> str:
        return (
            f"Hooks: {self.total_invocations} invocations, "
            f"{self.total_errors} errors, {self.total_aborts} aborts, "
            f"{self.avg_latency_ms:.2f}ms avg"
        )
