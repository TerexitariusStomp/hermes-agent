"""Built-in hook: Memory Auto-Storage.

Automatically stores tool results and conversation patterns in memory
after successful tool execution. This eliminates the need for explicit
memory() calls after every operation.

Triggered on: tool.afterExecute, skill.afterExecute, cron.afterRun
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .hook_types import HookContext, HookResult
from .hook_manager import HookManager

logger = logging.getLogger(__name__)


# Tool result patterns that are worth memorizing automatically
_MEMORY_WORTHY_TOOLS = {
    "terminal",     # command outputs, system state
    "read_file",    # file contents read
    "search_files", # search results
    "web_search",   # web research
    "web_extract",  # extracted page content
    "skill_view",   # skill content loaded
    "session_search",  # past session context
    "recall",       # vector memory recall
    "vision_analyze",  # image analysis
    "browser_snapshot", # page content
}

# Tools whose results should NOT be auto-stored (PII, noise, or redundant)
_MEMORY_EXCLUDED_TOOLS = {
    "terminal",  # can be huge, handled separately via command filter
    "read_file",  # file content stored via path reference only
}

# Command patterns worth memorizing from terminal output
_TERMINAL_MEMORY_COMMANDS = [
    "systemctl status", "journalctl", "dmesg", "df ", "free ",
    "nvidia-smi", "uname", "cat /etc/os-release", "ip addr",
    "lspci", "lsusb", "lsblk", "uptime", "cat /proc/",
    "git log", "git status", "git diff",
    "pip list", "uv pip", "apt list --installed",
]


def register_memory_auto_store(hook_manager: HookManager, priority: int = 0) -> None:
    """Register the memory auto-store hook."""
    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_on_tool_complete,
        name="memory-auto-store",
        description="Automatically store worthy tool results in memory",
        priority=priority,
        metadata={"auto_store": True, "max_result_chars": 2000},
    )

    hook_manager.register(
        hook_point="skill.afterExecute",
        handler=_on_skill_complete,
        name="memory-skill-store",
        description="Store skill execution results",
        priority=priority,
    )


def _on_tool_complete(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.afterExecute. Decide if result should be auto-stored."""
    if not ctx.tool_name or ctx.tool_error:
        return None

    tool_name = ctx.tool_name
    result = ctx.tool_result or ""

    # Skip if tool is excluded from auto-store
    if tool_name in _MEMORY_EXCLUDED_TOOLS:
        return _check_terminal_memory(ctx)

    # Only store results from worthy tools
    if tool_name not in _MEMORY_WORTHY_TOOLS:
        return None

    # Skip empty or error results
    if not result.strip() or result.startswith("Error ") or result.startswith("Failed"):
        return None

    # Truncate result for memory
    max_chars = 2000
    if len(result) > max_chars:
        result_preview = result[:max_chars] + "... (truncated)"
    else:
        result_preview = result

    # Format memory entry
    args_preview = ""
    if ctx.tool_args:
        args_preview = json.dumps({k: v for k, v in ctx.tool_args.items()
                                   if k not in ("content", "text", "url")},
                                  ensure_ascii=False)[:200]

    content = f"[TOOL:{tool_name}] {args_preview} -> {result_preview}"

    # Store via memory tool if available
    _emit_memory_event(content, f"auto:{tool_name}", ctx)

    logger.debug("Auto-stored tool result: %s (%d chars)", tool_name, len(result_preview))
    return None


def _check_terminal_memory(ctx: HookContext) -> Optional[HookResult]:
    """Check if terminal command output is worth memorizing."""
    if ctx.tool_name != "terminal" or not ctx.tool_args or ctx.tool_error:
        return None

    cmd = ctx.tool_args.get("command", "")
    result = ctx.tool_result or ""

    # Only store system diagnostic commands
    should_store = any(cmd.startswith(pat) for pat in _TERMINAL_MEMORY_COMMANDS)
    if not should_store:
        return None

    if not result.strip() or result.startswith("Error"):
        return None

    max_chars = 1500
    if len(result) > max_chars:
        result = result[:max_chars] + "... (truncated)"

    content = f"[TERMINAL] {cmd.strip()[:100]} -> {result}"
    _emit_memory_event(content, "auto:terminal", ctx)

    return None


def _on_skill_complete(ctx: HookContext) -> Optional[HookResult]:
    """Fire on skill.afterExecute."""
    if not ctx.skill_name or ctx.skill_error:
        return None

    skill_name = ctx.skill_name
    action = ctx.skill_action or "unknown"

    content = f"[SKILL] {skill_name} performed action '{action}'"
    _emit_memory_event(content, f"auto:skill:{skill_name}", ctx)

    return None


def _emit_memory_event(content: str, source: str, ctx: HookContext) -> None:
    """Emit a memory store event.

    Currently logs and sets context extras. In a future iteration this
    will call the memory tool directly. The integration with the actual
    memory() tool requires the agent reference which is passed via extras.
    """
    ctx.extras["memory_auto_event"] = {
        "content": content,
        "source": source,
        "timestamp": ctx.timestamp,
    }
    logger.debug("Memory auto-store event: %s", source)
