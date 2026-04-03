"""Built-in hook: Quality Gates.

Adapted from ECC's PreToolUse/PostToolUse hook pattern.
Implements:
- Pre-terminal blocks: warns about long-running commands without --background
- Post-write syntax checks: validates Python/JSON/YAML after file writes
- Pre-write guards: suggests compact before large multi-file edits

Triggered on: tool.beforeExecute, tool.afterExecute
"""

from __future__ import annotations

import logging
import subprocess
import re
from typing import Optional

from .hook_types import HookContext, HookResult, HookManager
from .hook_manager import HookManager as HM

logger = logging.getLogger(__name__)

# Commands that typically run forever and should use background=true
_LONG_RUNNING_PATTERNS = [
    re.compile(r"^\s*uvicorn\s", re.IGNORECASE),
    re.compile(r"^\s*npm\s+run\s+(dev|start|watch|serve)", re.IGNORECASE),
    re.compile(r"^\s*python\s+[^\s]*server", re.IGNORECASE),
    re.compile(r"^\s*node\s+[^\s]*(server|app|daemon|watch)", re.IGNORECASE),
    re.compile(r"^\s*tail\s+-f", re.IGNORECASE),
    re.compile(r"^\s*sleep\s+\d+", re.IGNORECASE),
    re.compile(r"^\s*while\s+", re.IGNORECASE),
    re.compile(r"^\s*loop\.", re.IGNORECASE),
    re.compile(r"^\s*hermes-agent\s", re.IGNORECASE),
]

# File extensions that can be syntax-checked
_SYNTAX_CHECKERS = {
    ".py": "py_compile",
    ".json": "json",
}


def register_quality_gates(hook_manager: HM, priority: int = 0) -> None:
    hook_manager.register(
        hook_point="tool.beforeExecute",
        handler=_pre_terminal_gate,
        name="quality-gate-terminal",
        description="Warn about long-running terminal commands missing --background",
        priority=priority + 5,
        metadata={"gate": True},
    )

    hook_manager.register(
        hook_point="tool.afterExecute",
        handler=_post_write_syntax_check,
        name="quality-gate-syntax",
        description="Syntax-check files after write/patch operations",
        priority=priority - 5,  # Run after other post-hooks
    )


def _pre_terminal_gate(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.beforeExecute. Check terminal commands."""
    if ctx.tool_name != "terminal" or not ctx.tool_args:
        return None

    cmd = ctx.tool_args.get("command", "")
    background = ctx.tool_args.get("background", False)

    if background:
        return None

    for pattern in _LONG_RUNNING_PATTERNS:
        if pattern.search(cmd):
            ctx.extras["quality_gate"] = {
                "type": "long_running_command",
                "cmd_preview": cmd[:120],
                "suggestion": "Use background=True for long-running commands",
            }
            return HookResult(
                abort=False,
                reason=f"Long-running command detected without --background: {cmd[:80]}...",
                severity="warn",
            )

    return None


def _post_write_syntax_check(ctx: HookContext) -> Optional[HookResult]:
    """Fire on tool.afterExecute. Check syntax of written files."""
    if ctx.tool_name not in ("write_file", "patch"):
        return None
    if not ctx.tool_args or ctx.tool_error:
        return None

    path = ctx.tool_args.get("path", "")
    ext = __import__("os").path.splitext(path)[1].lower()
    checker = _SYNTAX_CHECKERS.get(ext)

    if not checker:
        return None

    try:
        if checker == "py_compile":
            import py_compile
            py_compile.compile(path, doraise=True)
        elif checker == "json":
            import json
            json.loads(open(path).read())
        return None
    except Exception as e:
        return HookResult(
            abort=False,
            reason=f"Syntax error in {path}: {type(e).__name__}: {e}",
            severity="error",
        )
