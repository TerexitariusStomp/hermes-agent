"""Tool Result Spillover — Hive-inspired file spillover for large tool results.

Adapted from aden-hive/hive core/runtime/spillover.py.

When tool results exceed a size threshold, write them to disk and replace
the conversation content with a file pointer. The agent calls load_data()
when it needs specific content.

Benefits:
- Prevents context exhaustion from large tool outputs
- Error results are never truncated (always full)
- load_data() results are never re-spilled (prevents loops)
"""

import os
import hashlib
import json
import time
from pathlib import Path
from typing import Any


class ToolSpillover:
    """Manages file spillover for large tool results.

    When a tool result exceeds max_inline_chars, it's written to disk
    and replaced with a pointer in the conversation.
    """

    def __init__(
        self,
        max_inline_chars: int = 30000,
        spillover_dir: str | Path | None = None,
        max_spillover_files: int = 200,
    ):
        self.max_inline_chars = max_inline_chars
        self.spillover_dir = Path(spillover_dir).expanduser() if spillover_dir else (
            Path.home() / ".hermes" / "spillover"
        )
        self.spillover_dir.mkdir(parents=True, exist_ok=True)
        self.max_spillover_files = max_spillover_files
        self._spill_count = 0
        self._total_bytes_spilled = 0

    def should_spill(
        self,
        result: str | dict | list,
        tool_name: str,
        exclude_tools: set[str] | None = None,
    ) -> bool:
        """Check if a tool result should be spilled to disk."""
        if exclude_tools and tool_name in exclude_tools:
            return False

        content = self._extract_content(result)
        if content is None:
            return False

        return len(content) > self.max_inline_chars

    def maybe_spill(
        self,
        result: str | dict | list,
        tool_name: str,
        exclude_tools: set[str] | None = None,
    ) -> str | dict:
        """Process a tool result, spilling to disk if needed.

        Args:
            result: The raw tool result (string or dict with 'content'/'output' keys)
            tool_name: Name of the tool that produced this result
            exclude_tools: Tool names that should never be spilled

        Returns:
            Original result or spillover pointer dict
        """
        if exclude_tools and tool_name in exclude_tools:
            return result

        content = self._extract_content(result)
        if content is None:
            return result

        # Never spill error results
        if self._is_error_result(content):
            return result

        if len(content) <= self.max_inline_chars:
            return result

        pointer = self._write_to_disk(content, tool_name)
        return self._create_pointer(pointer, content)

    def create_spillover_stats(self) -> dict[str, Any]:
        """Return current spillover statistics."""
        spillover_files = list(self.spillover_dir.glob("*.txt"))
        total_size = sum(f.stat().st_size for f in spillover_files)

        return {
            "spillover_dir": str(self.spillover_dir),
            "max_inline_chars": self.max_inline_chars,
            "current_spillover_files": len(spillover_files),
            "total_spillover_size_bytes": total_size,
            "total_spills_this_session": self._spill_count,
            "total_bytes_spilled": self._total_bytes_spilled,
        }

    def cleanup_old_files(self) -> int:
        """Remove oldest spillover files if over max limit."""
        files = list(self.spillover_dir.glob("*.txt"))
        if len(files) <= self.max_spillover_files:
            return 0

        # Sort by modification time (oldest first)
        files.sort(key=lambda f: f.stat().st_mtime)
        to_remove = len(files) - self.max_spillover_files

        for f in files[:to_remove]:
            f.unlink()

        return to_remove

    def _extract_content(self, result: Any) -> str | None:
        """Extract text content from a tool result."""
        if isinstance(result, str):
            return result
        elif isinstance(result, dict):
            for key in ['content', 'output', 'result', 'data', 'text']:
                if key in result and isinstance(result[key], str):
                    return result[key]
        return None

    def _is_error_result(self, content: str) -> bool:
        """Check if content contains error indicators (never truncate errors)."""
        error_indicators = [
            'ERROR:', 'Traceback', 'Exception:', 'Error:', 'FAILED',
        ]
        # Check JSON-wrapped errors
        try:
            parsed = json.loads(content)
            if any(k in parsed for k in ['error', 'stderr', 'exit_code']):
                return True
        except (json.JSONDecodeError, TypeError):
            pass
        return any(indicator in content[:500] for indicator in ['ERROR:', 'Traceback', 'Exception:', 'FAILED'])

    def _write_to_disk(self, content: str, tool_name: str) -> dict:
        """Write content to a spillover file and return pointer info."""
        # Generate a unique filename
        content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
        timestamp = int(time.time())
        filename = f"{tool_name}_{timestamp}_{content_hash}.txt"
        filepath = self.spillover_dir / filename

        filepath.write_text(content, encoding='utf-8')

        self._spill_count += 1
        self._total_bytes_spilled += len(content)

        # Cleanup old files if needed
        self.cleanup_old_files()

        return {
            'filepath': str(filepath),
            'filename': filename,
            'size_bytes': len(content),
            'size_chars': len(content),
            'tool_name': tool_name,
            'hash': content_hash,
        }

    def _create_pointer(self, pointer_info: dict, original_content: str) -> str:
        """Create a pointer message to replace the full content."""
        return (
            f"[LARGE OUTPUT SPILLED TO DISK — {pointer_info['size_chars']:,} chars]\n"
            f"Tool: {pointer_info['tool_name']}\n"
            f"File: {pointer_info['filepath']}\n"
            f"Preview: {original_content[:500]}...\n"
            f"Use load_data('{pointer_info['filepath']}') to read the full content."
        )

    def load_data(self, filepath: str) -> str:
        """Load data from a spillover file.

        This is the reverse operation — when the agent explicitly requests
        a spilled file, return its full content. This file should NOT be
        spilled again on the way back into context.
        """
        try:
            path = Path(filepath).expanduser()
            if not path.exists():
                return f"[ERROR] Spillover file not found: {filepath}"
            # Verify it's in the spillover directory
            if not str(path).startswith(str(self.spillover_dir)):
                return f"[ERROR] Access denied — file must be in spillover directory"
            return path.read_text(encoding='utf-8')
        except Exception as e:
            return f"[ERROR] Could not read spillover file: {e}"


# Convenience function for direct use
def create_spillover(max_chars=30000, spillover_dir=None):
    return ToolSpillover(max_inline_chars=max_chars, spillover_dir=spillover_dir)
