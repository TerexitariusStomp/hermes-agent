"""Built-in hook: Session lifecycle integration.

Wires session persistence into the hook system:
- Auto-saves session state on agent.beforeStart and agent.afterStop
- Fires context.beforeCompress hooks before compaction
- Provides hooks for manual save/restore via memory store tool

Triggered on: agent.beforeStart, agent.afterStop, context.beforeCompress
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .hook_types import HookContext, HookResult
from .hook_manager import HookManager as HM
from tools import session_persist

logger = logging.getLogger(__name__)


def register_session_hooks(hook_manager: HM, priority: int = 0) -> None:
    hook_manager.register(
        hook_point="agent.afterStop",
        handler=_on_stop_auto_save,
        name="session-autosave",
        description="Auto-save session state when agent stops",
        priority=priority,
    )

    hook_manager.register(
        hook_point="context.beforeCompress",
        handler=_before_compress_checkpoint,
        name="compress-checkpoint",
        description="Save session state before context compression",
        priority=priority,
    )


def _on_stop_auto_save(ctx: HookContext) -> None:
    """Fire on agent.afterStop. Save session with summary."""
    summary = ctx.extras.get("conversation_summary", "")
    if not summary and ctx.message_content:
        summary = ctx.message_content[:500]

    try:
        session_persist.save_session(
            session_id=ctx.conversation_id or ctx.agent_id,
            summary=summary,
            active_tools=ctx.extras.get("active_tools"),
            context_files=ctx.extras.get("context_files"),
            current_task=ctx.extras.get("current_task", ""),
            user_message=ctx.extras.get("last_user_message", ""),
            model_name=ctx.model_name or "",
            alias=ctx.extras.get("session_alias", ""),
        )
    except Exception:
        logger.debug("Auto-save failed", exc_info=True)

    return None


def _before_compress_checkpoint(ctx: HookContext) -> Optional[HookResult]:
    """Fire on context.beforeCompress. Save state before we lose it."""
    try:
        session_persist.save_session(
            session_id=ctx.conversation_id or ctx.agent_id,
            summary=ctx.extras.get("conversation_summary", ""),
            current_task=ctx.extras.get("current_task", ""),
        )
        logger.info("Session checkpoint saved before compression")
    except Exception:
        logger.debug("Pre-compress checkpoint save failed", exc_info=True)

    return None
