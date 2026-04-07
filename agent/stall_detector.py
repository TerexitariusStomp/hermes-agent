"""Stall/Doom Loop Detection — Detect agent loops and inject escape strategies.

Adapted from aden-hive/hive core/runtime/diagnostics.py and core/framework/event_loop.py.

Detects when the agent is stuck in a loop based on:
- Same tool called 5+ times with same args (fingerprint match)
- Same tool sequence repeated
- No progress across multiple iterations

Escape strategies:
- Level 1: Meta-prompt about different approach
- Level 2: Force different tool
- Level 3: Report failure to user
"""

import re
import json
import hashlib
from typing import Any


class StallDetector:
    """Detect stall/doom loops in agent execution.

    Monitors tool call patterns and content to detect when the agent
    is making no progress.
    """

    def __init__(
        self,
        fingerprint_window: int = 5,
        sequence_window: int = 8,
        max_iterations: int = 50,
    ):
        self.fingerprint_window = fingerprint_window
        self.sequence_window = sequence_window
        self.max_iterations = max_iterations
        self._tool_calls = []  # List of (tool_name, args_hash)
        self._response_hashes = []  # Hashes of recent assistant responses
        self._iteration_count = 0
        self._escape_stage = 0  # 0=none, 1=warned, 2=forced, 3=fail

    def record_tool_call(self, tool_name: str, args: dict) -> None:
        """Record a tool call for loop detection."""
        args_str = json.dumps(args, sort_keys=True, default=str)
        args_hash = hashlib.md5(args_str.encode()).hexdigest()[:10]
        self._tool_calls.append((tool_name, args_hash))
        self._iteration_count += 1

    def add_response(self, content: str) -> None:
        """Record the agent's response content."""
        # Use first 100 chars hash as fingerprint
        content_hash = hashlib.md5(content[:100].encode()).hexdigest()[:10]
        self._response_hashes.append(content_hash)

    def check_stall(self) -> dict[str, Any]:
        """Check if we're in a stall/doom loop.

        Returns dict with:
        - stalled: bool
        - stall_type: str or None
        - escape_stage: int (0-3)
        - suggestion: str
        """
        if len(self._tool_calls) < 3:
            return {'stalled': False, 'stall_type': None, 'escape_stage': 0, 'suggestion': ''}

        # Check 1: Exact tool+args repetition (3+ times)
        recent = self._tool_calls[-self.fingerprint_window:]
        if len(recent) >= 3:
            # Check if last 3 calls are identical
            if recent[-1] == recent[-2] == recent[-3]:
                tool_name = recent[-1][0]
                return {
                    'stalled': True,
                    'stall_type': 'exact_repetition',
                    'escape_stage': self._escape_stage,
                    'suggestion': f"Agent called {tool_name} with identical args {len(recent)} times. Try a different approach.",
                }

            # Check if same tool called 5+ times regardless of args
            tool_counts = {}
            for tn, _ in recent:
                tool_counts[tn] = tool_counts.get(tn, 0) + 1
            max_tool = max(tool_counts, key=tool_counts.get)
            if tool_counts.get(max_tool, 0) >= 5:
                return {
                    'stalled': True,
                    'stall_type': 'single_tool_loop',
                    'escape_stage': self._escape_stage,
                    'suggestion': f"Agent called {max_tool} {tool_counts[max_tool]} times in last {self.fingerprint_window} iterations. Consider a different strategy.",
                }

        # Check 2: Repeated tool sequence
        if len(self._tool_calls) >= self.sequence_window:
            seq1 = self._tool_calls[-(self.sequence_window//2):]
            seq2 = self._tool_calls[-self.sequence_window:-(self.sequence_window//2)]
            if seq1 == seq2:
                return {
                    'stalled': True,
                    'stall_type': 'sequence_repetition',
                    'escape_stage': self._escape_stage,
                    'suggestion': "Agent is repeating the same tool sequence. Try breaking the pattern.",
                }

        # Check 3: Response content recycling
        if len(self._response_hashes) >= 5:
            recent_responses = self._response_hashes[-5:]
            unique = set(recent_responses)
            if len(unique) <= 2:  # Most responses are identical
                return {
                    'stalled': True,
                    'stall_type': 'content_recycling',
                    'escape_stage': self._escape_stage,
                    'suggestion': "Agent is producing nearly identical responses. Change strategy.",
                }

        # Check 4: Excessive retries without state change
        if self._iteration_count > self.max_iterations:
            return {
                'stalled': True,
                'stall_type': 'max_iterations',
                'escape_stage': self._escape_stage,
                'suggestion': f"Exceeded {self.max_iterations} max iterations.",
            }

        return {'stalled': False, 'stall_type': None, 'escape_stage': 0, 'suggestion': ''}

    def get_escape_prompt(self) -> str:
        """Get the appropriate escape prompt based on current stage."""
        self._escape_stage += 1

        if self._escape_stage == 1:
            return (
                "SYSTEM: You appear to be repeating the same approach without making progress. "
                "Try a fundamentally different strategy. Consider:\n"
                "1. Using a different tool than you've been using\n"
                "2. Reading different files or searching differently\n"
                "3. Breaking the problem into smaller parts\n"
                "4. Taking a step back and re-reading the original request"
            )
        elif self._escape_stage == 2:
            return (
                "SYSTEM: Your previous approach isn't working. You must now change tactics entirely.\n"
                "Do NOT call the same tool again. Instead:\n"
                "1. Summarize what you've tried so far\n"
                "2. Explain why it hasn't worked\n"
                "3. Propose a completely different approach\n"
                "4. Ask the user for guidance if needed"
            )
        else:
            return (
                "SYSTEM: This task appears to be intractable with the current approach. "
                "Please summarize: (1) what was attempted, (2) what failed, (3) what would be needed to succeed. "
                "Then STOP and report your findings to the user."
            )

    def get_stats(self) -> dict[str, Any]:
        """Get detection statistics."""
        return {
            'iteration_count': self._iteration_count,
            'unique_tool_calls': len(set(self._tool_calls)),
            'escape_stage': self._escape_stage,
            'recent_tools': [tc[0] for tc in self._tool_calls[-10:]],
        }
