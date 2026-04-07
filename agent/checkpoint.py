"""Checkpoint-based Crash Recovery for Hermes Agent Sessions.

Adapted from aden-hive/hive core/runtime/checkpoint.py.

Persists session state at tool call boundaries so the agent can resume
after crashes, restarts, or connection losses.
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Any


class CheckpointManager:
    """Manages session checkpoints for crash recovery.

    Checkpoints are saved at tool call boundaries and include:
    - Conversation history
    - Current state (tool calls, visit counts)
    - Session metadata
    """

    def __init__(self, checkpoint_dir: str | Path | None = None, max_checkpoints: int = 20):
        self.checkpoint_dir = Path(checkpoint_dir).expanduser() if checkpoint_dir else (
            Path.home() / ".hermes" / "checkpoints"
        )
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.max_checkpoints = max_checkpoints
        self._current_run_id = None

    def save_checkpoint(
        self,
        session_id: str,
        conversation: list[dict],
        iteration: int,
        tool_calls: list[dict],
        metadata: dict[str, Any] = None,
    ) -> str:
        """Save a checkpoint at the current state.

        Args:
            session_id: Unique session identifier
            conversation: Current conversation history
            iteration: Current iteration number in the agent loop
            tool_calls: List of tool calls made so far
            metadata: Additional session metadata

        Returns:
            Checkpoint file path
        """
        # Generate run ID if needed
        if self._current_run_id is None:
            self._current_run_id = f"{session_id}_{int(time.time())}"

        # Trim conversation to manageable size for checkpoint
        # Keep first 2 messages (system + initial user) + last 30 messages
        if len(conversation) > 32:
            trimmed_messages = conversation[:2] + conversation[-30:]
            trim_note = f"trimmed from {len(conversation)} to {len(trimmed_messages)} messages"
        else:
            trimmed_messages = conversation
            trim_note = None

        checkpoint = {
            'run_id': self._current_run_id,
            'session_id': session_id,
            'timestamp': time.time(),
            'iteration': iteration,
            'conversation': trimmed_messages,
            'tool_calls': tool_calls[-50:],  # Keep last 50 tool calls
            'metadata': metadata or {},
            'trim_note': trim_note,
        }

        # Save to file
        filename = f"checkpoint_{session_id}_{int(time.time())}.json"
        filepath = self.checkpoint_dir / filename
        filepath.write_text(json.dumps(checkpoint, indent=2, default=str))

        # Cleanup old checkpoints
        self._cleanup_old_checkpoints(session_id)

        return str(filepath)

    def load_latest_checkpoint(self, session_id: str) -> dict | None:
        """Load the most recent checkpoint for a session.

        Args:
            session_id: Session to load checkpoint for

        Returns:
            Checkpoint dict or None if no checkpoint exists
        """
        checkpoints = list(self.checkpoint_dir.glob(f"checkpoint_{session_id}_*.json"))
        if not checkpoints:
            return None

        # Get most recent
        checkpoints.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        latest = checkpoints[0]

        try:
            data = json.loads(latest.read_text())
            return data
        except (json.JSONDecodeError, IOError):
            return None

    def list_checkpoints(self, session_id: str = None) -> list[dict]:
        """List all available checkpoints.

        Args:
            session_id: Filter by session (optional)

        Returns:
            List of checkpoint info dicts
        """
        pattern = f"checkpoint_{session_id}_*.json" if session_id else "checkpoint_*.json"
        files = list(self.checkpoint_dir.glob(pattern))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        results = []
        for f in files[:20]:
            try:
                data = json.loads(f.read_text())
                results.append({
                    'filename': f.name,
                    'filepath': str(f),
                    'session_id': data.get('session_id', 'unknown'),
                    'run_id': data.get('run_id', 'unknown'),
                    'timestamp': data.get('timestamp', 0),
                    'iteration': data.get('iteration', 0),
                    'conversation_length': len(data.get('conversation', [])),
                })
            except (json.JSONDecodeError, IOError):
                continue

        return results

    def _cleanup_old_checkpoints(self, session_id: str) -> None:
        """Remove old checkpoints for this session beyond the limit."""
        checkpoints = list(self.checkpoint_dir.glob(f"checkpoint_{session_id}_*.json"))
        if len(checkpoints) <= self.max_checkpoints:
            return

        checkpoints.sort(key=lambda f: f.stat().st_mtime)
        to_remove = len(checkpoints) - self.max_checkpoints

        for f in checkpoints[:to_remove]:
            try:
                f.unlink()
            except OSError:
                pass

    def clear_all(self, session_id: str = None) -> int:
        """Clear all checkpoints, optionally for a specific session."""
        if session_id:
            pattern = f"checkpoint_{session_id}_*.json"
        else:
            pattern = "checkpoint_*.json"

        count = 0
        for f in self.checkpoint_dir.glob(pattern):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
        return count
