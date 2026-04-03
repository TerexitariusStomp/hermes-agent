"""Session persistence for Hermes Agent.

Adapted from ECC's /save-session and /resume-session pattern.
Provides save/restore of agent state (conversation summary, active tools,
task context) so agents can pause and resume across restarts.

Storage: ~/.hermes/sessions/ as JSON.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SESSIONS_DIR = Path.home() / ".hermes" / "sessions"
_SCHEMA_VERSION = "hermes.session.v1"


def ensure_session_dir() -> Path:
    """Create and return the sessions directory."""
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSIONS_DIR


def save_session(
    session_id: str,
    summary: str = "",
    active_tools: Optional[List[str]] = None,
    context_files: Optional[List[str]] = None,
    current_task: str = "",
    user_message: str = "",
    model_name: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    alias: str = "",
) -> Path:
    """Save current session state.

    Args:
        session_id: Unique session identifier (usually same as conversation_id)
        summary: Conversation summary for resume context
        active_tools: List of tool names currently in use
        context_files: List of file paths loaded in context
        current_task: Current task description
        user_message: Last user message
        model_name: Model currently in use
        metadata: Arbitrary extra session state
        alias: Human-readable session label

    Returns:
        Path to saved session file
    """
    ensure_session_dir()

    snapshot = {
        "schemaVersion": _SCHEMA_VERSION,
        "session": {
            "id": session_id,
            "alias": alias or session_id,
            "savedAt": datetime.now(timezone.utc).isoformat(),
            "model": model_name,
            "state": "saved",
        },
        "context": {
            "summary": summary,
            "currentTask": current_task,
            "lastUserMessage": user_message[:500] if user_message else "",
            "activeTools": active_tools or [],
            "contextFiles": context_files or [],
        },
        "metadata": metadata or {},
    }

    session_file = _SESSIONS_DIR / f"{session_id}.json"
    with open(session_file, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    logger.info("Session saved: %s (%s)", session_id, session_file)
    return session_file


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Load a saved session by ID.

    Returns:
        Session snapshot dict, or None if not found.
    """
    session_file = _SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        logger.debug("Session not found: %s", session_id)
        return None

    try:
        with open(session_file) as f:
            snapshot = json.load(f)
        if snapshot.get("schemaVersion") != _SCHEMA_VERSION:
            logger.warning("Session schema mismatch: %s", snapshot.get("schemaVersion"))
        logger.info("Session loaded: %s", session_id)
        return snapshot
    except Exception:
        logger.error("Failed to load session %s", session_id, exc_info=True)
        return None


def list_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    """List all saved sessions, most recent first."""
    if not _SESSIONS_DIR.exists():
        return []

    sessions = []
    for f in _SESSIONS_DIR.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            sessions.append({
                "id": data.get("session", {}).get("id", f.stem),
                "alias": data.get("session", {}).get("alias", ""),
                "savedAt": data.get("session", {}).get("savedAt", ""),
                "model": data.get("session", {}).get("model", ""),
                "summary_preview": (data.get("context", {}).get("summary", "") or "")[:120],
                "task_preview": (data.get("context", {}).get("currentTask", "") or "")[:120],
                "file_size": f.stat().st_size,
            })
        except Exception:
            continue

    sessions.sort(key=lambda s: s["savedAt"], reverse=True)
    return sessions[:limit]


def delete_session(session_id: str) -> bool:
    """Delete a saved session. Returns True if deleted."""
    session_file = _SESSIONS_DIR / f"{session_id}.json"
    if session_file.exists():
        session_file.unlink()
        logger.info("Session deleted: %s", session_id)
        return True
    return False


def build_resume_prompt(snapshot: Dict[str, Any]) -> str:
    """Build a context injection prompt from a loaded session snapshot.

    This prompt, when injected into a new conversation, gives the agent
    enough context to resume where it left off.
    """
    ctx = snapshot.get("context", {})
    session = snapshot.get("session", {})

    prompt = "## RESUME FROM PREVIOUS SESSION\n\n"
    prompt += f"Session: {session.get('alias', session.get('id', 'unknown'))}\n"
    prompt += f"Model: {session.get('model', 'unknown')}\n"
    prompt += f"Saved: {session.get('savedAt', 'unknown')}\n\n"

    summary = ctx.get("summary", "").strip()
    if summary:
        prompt += f"Conversation summary:\n{summary}\n\n"

    task = ctx.get("currentTask", "").strip()
    if task:
        prompt += f"Current task: {task}\n\n"

    tools = ctx.get("activeTools", [])
    if tools:
        prompt += f"Active tools: {', '.join(tools)}\n\n"

    files = ctx.get("contextFiles", [])
    if files:
        prompt += f"Context files: {', '.join(files)}\n\n"

    last_msg = ctx.get("lastUserMessage", "").strip()
    if last_msg:
        prompt += f"Last user message: {last_msg}\n\n"

    prompt += "Resume from here. Use the context above to continue.\n"
    return prompt
