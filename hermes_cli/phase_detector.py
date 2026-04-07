#!/usr/bin/env python3
"""
Automatic Phase Boundary Detection for Hermes Agent.

Based on "Agent Workflow Memory" (Chen & Callan, 2026), which finds that
auto-segmenting agent sessions into semantically coherent phases improves
memory retrieval accuracy by up to 20% compared to treating the entire
session as a flat sequence.

Detection Strategies
====================
1. Keyword/Intent Shift   -- Heuristic patterns that signal a phase change
   (user starts a qualitatively different request, agent switches tools)
2. Tool Cluster Shift      -- Statistical change in tool usage patterns
3. Conversation Reset      -- User sends a topic-changing message
4. Session Boundary        -- Natural session start/end

Usage
=====
  from hermes_cli.phase_detector import PhaseDetector

  detector = PhaseDetector()

  # On each new user message:
  change = detector.should_split_phase(user_message, recent_context)
  if change:
      hwm.end_phase(summary=change["suggested_summary"])
      hwm.start_phase(change["suggested_name"])

  # Or batch-detect on session end:
  phases = detector.detect_phases(session_messages)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Phase boundary signals ─────────────────────────────────────────────

# Patterns that indicate a NEW phase/user intent
PHASE_START_PATTERNS = [
    # Explicit task transitions
    r'(?i)^(now|next|moving on|switching|lets? (?:move|switch|go|try)|let me ask|another question)',
    r'(?i)^(can you|could you|would you|please) (?:\w+ )*(?:look at|check|fix|review|analyze|debug|help me with)',
    r'(?i)^(i need|i want|i have to|we need|we should|lets?) (?:\w+ )*(?:fix|create|build|deploy|setup|install|configure|remove|delete|update)',

    # Topic shift signals
    r'(?i)^(also|by the way|oh|btw|separately|on a different note|meanwhile)',
    r'(?i)^(back to|returning to|regarding|about the)',
    # Task transition signals (covers "now let's", "now let us", "next we should")
    r'(?i)^(?:now|next|then|so)\s+(?:lets?|let us|we should|we need|we (?:must|will)|I (?:want|need|will))',
    r'(?i)^(thanks,|thank you|great,|perfect|ok,|alright).{0,50}(?:now|next|also|can you)',

    # Error recovery (new phase: fixing a problem)
    r"(?i)^(that (didn|does)n't work|failed|broke|error|wrong|not working)",
    r"(?i)^(that does not work|failed|broke|error|wrong|not working)",
    r"(?i)^(got an error|seeing this error|this failed|it broke|hmm)",

    # Completion + new task
    r'(?i)(?:done|finished|completed|working|fixed|solved).{0,20}(now|next|let|can)',
]

# Patterns that indicate CONTINUATION of current phase
PHASE_CONTINUE_PATTERNS = [
    r'(?i)^(yes|yeah)\b',
    r'(?i)^no\b(?:\s+(?:,|not yet|but|actually|wait))?',
    r'(?i)^(still|wait|hold on|one more|also )',
    r'(?i)^(can you also|and then|and also|next step|after that)',
    r'(?i)^(show me|tell me|explain|why|how|what about|what if)',
    r'(?i)^(try|use|check|run|test|verify|re-run|retry)',
]

# Tool cluster categories for tool-shift detection
TOOL_CATEGORIES = {
    "filesystem": ["read_file", "write_file", "patch", "search_files", "terminal"],
    "browser": ["browser_navigate", "browser_click", "browser_type",
                "browser_snapshot", "browser_vision"],
    "delegation": ["delegate_task", "execute_code"],
    "memory": ["recall", "memory", "session_search", "honcho_"],
    "skills": ["skill_view", "skills_list", "skill_manage"],
    "communication": ["text_to_speech", "clarify", "send_message"],
    "scheduling": ["cronjob"],
    "web_search": [],  # would contain web search tools if available
}

@dataclass
class PhaseBoundary:
    """Represents a detected phase boundary."""
    position: int  # Message index where the new phase starts
    confidence: float  # 0-1 confidence score
    reason: str  # Human-readable reason
    suggested_name: str = ""
    suggested_summary: str = ""


@dataclass
class DetectedPhase:
    """A detected phase in a session."""
    start: int
    end: int
    name: str
    summary: str
    dominant_tool_category: str = ""
    message_count: int = 0
    tool_call_count: int = 0


class PhaseDetector:
    """Detects phase boundaries in agent session transcripts.

    No external dependencies -- pure text analysis.
    """

    def __init__(self):
        self._start_patterns = [
            re.compile(p) for p in PHASE_START_PATTERNS
        ]
        self._continue_patterns = [
            re.compile(p) for p in PHASE_CONTINUE_PATTERNS
        ]

    def should_split_phase(
        self,
        user_message: str,
        recent_messages: Optional[List[Dict[str, str]]] = None,
        current_phase_duration_msgs: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Check if the current user message signals a phase boundary.

        Args:
            user_message: The new user message
            recent_messages: Last 3-5 assistant messages (for tool shift check)
            current_phase_duration_msgs: How many messages in current phase

        Returns:
            Dict with 'suggested_name', 'suggested_summary', 'confidence'
            if a phase split is warranted, None otherwise.
        """
        if current_phase_duration_msgs < 3:
            return None  # Phase too young to split

        signals = []

        # 1. Check for phase-start patterns in the user message
        for pat in self._start_patterns:
            m = pat.search(user_message)
            if m:
                signals.append(("intent_shift", 0.7, m.group(0)[:40]))
                break

        # Don't split if there are strong continuation signals
        for pat in self._continue_patterns:
            m = pat.search(user_message)
            if m:
                # Override intent shift if continuation signal is strong
                signals = [(s[0], s[1] * 0.5, s[2]) for s in signals]
                break

        # 2. Tool-shift detection (if recent messages provided)
        if recent_messages and len(recent_messages) >= 3:
            prev_tools = self._extract_tool_categories(recent_messages[:-1])
            new_tools = self._extract_tool_categories(recent_messages[-1:])
            if prev_tools and new_tools and prev_tools != new_tools:
                signals.append(("tool_shift", 0.5,
                              f"Tools: {prev_tools} -> {new_tools}"))

        # 3. Long phase pressure (split if phase is getting very long)
        if current_phase_duration_msgs > 20:
            signals.append(("length_pressure", 0.3,
                          f"Phase has {current_phase_duration_msgs} messages"))

        if not signals:
            return None

        # Take the highest-confidence signal
        best = max(signals, key=lambda s: s[1])

        if best[1] < 0.5:
            return None

        # Generate a phase name from the user message
        name = self._extract_phase_name(user_message)
        summary = self._generate_summary(user_message, best)

        return {
            "suggested_name": name,
            "suggested_summary": summary,
            "confidence": round(best[1], 2),
            "reason": best[0],
        }

    def detect_phases(self, messages: List[Dict[str, Any]]) -> List[DetectedPhase]:
        """Batch-detect phases in a session transcript.

        Args:
            messages: List of {role, content, tool_calls} dicts

        Returns:
            List of DetectedPhase objects
        """
        if not messages:
            return []

        boundaries = [PhaseBoundary(position=0, confidence=1.0,
                                    reason="session_start", suggested_name="Session Start")]

        # Walk through user messages and detect phase starts
        phase_len = 0
        recent_assistant = []

        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant":
                recent_assistant.append(msg)
                if len(recent_assistant) > 5:
                    recent_assistant = recent_assistant[-5:]
                continue

            if msg.get("role") != "user":
                continue

            user_content = msg.get("content", "") or ""
            split = self.should_split_phase(
                user_message=user_content,
                recent_messages=recent_assistant,
                current_phase_duration_msgs=phase_len,
            )

            if split:
                boundaries.append(PhaseBoundary(
                    position=i,
                    confidence=split["confidence"],
                    reason=split["reason"],
                    suggested_name=split.get("suggested_name", ""),
                    suggested_summary=split.get("suggested_summary", ""),
                ))
                phase_len = 0
            else:
                phase_len += 1

        # Convert boundaries to phases
        phases = []
        for j, boundary in enumerate(boundaries):
            start = boundary.position
            end = boundaries[j + 1].position if j + 1 < len(boundaries) else len(messages)

            # Analyze phase content
            phase_messages = messages[start:end]
            tool_cats = self._extract_tool_categories(phase_messages)

            # Generate summary from first user message in phase
            first_user = next(
                (m["content"] for m in phase_messages
                 if m.get("role") == "user" and m.get("content")),
                ""
            )

            phases.append(DetectedPhase(
                start=start,
                end=end,
                name=boundary.suggested_name or self._extract_phase_name(first_user),
                summary=boundary.suggested_summary or first_user[:120],
                dominant_tool_category=", ".join(tool_cats) if tool_cats else "",
                message_count=len(phase_messages),
                tool_call_count=sum(
                    1 for m in phase_messages if m.get("tool_calls")),
            ))

        return phases

    def _extract_tool_categories(
        self, messages: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract dominant tool categories from a set of messages."""
        category_counts: Dict[str, int] = {}

        for msg in messages:
            tool_calls = msg.get("tool_calls", [])
            if isinstance(tool_calls, str):
                try:
                    tool_calls = json.loads(tool_calls)
                except (json.JSONDecodeError, TypeError):
                    continue

            if isinstance(tool_calls, list) and tool_calls:
                for tc in tool_calls:
                    func_name = ""
                    if isinstance(tc, dict):
                        func_name = tc.get("function", {}).get("name", "") or ""
                    for cat, tools in TOOL_CATEGORIES.items():
                        if not tools or any(t in func_name for t in tools):
                            category_counts[cat] = category_counts.get(cat, 0) + 1
                        else:
                            # Check if the tool name partially matches
                            for t in tools:
                                if t in func_name or func_name in t:
                                    category_counts[cat] = category_counts.get(cat, 0) + 1
                                    break

        # Return top categories by count
        sorted_cats = sorted(category_counts.items(),
                            key=lambda x: x[1], reverse=True)
        return [cat for cat, count in sorted_cats[:3] if count > 0]

    @staticmethod
    def _extract_phase_name(message: str) -> str:
        """Extract a short phase name from a user message."""
        if not message:
            return "Unknown phase"

        # Take the first sentence, truncated to 60 chars
        sentences = re.split(r'[.!?]+', message, maxsplit=1)
        first = sentences[0].strip()

        # Remove leading filler words
        first = re.sub(
            r'^(?:hey|hi|hello|ok|so|well|um|uh|actually|just),?\s*',
            '', first, flags=re.IGNORECASE).strip()

        if len(first) > 60:
            first = first[:57] + "..."

        return first or "Task phase"

    @staticmethod
    def _generate_summary(
        message: str, signal: tuple
    ) -> str:
        """Generate a phase-end summary hint."""
        reason = signal[2] if len(signal) > 2 else ""
        return f"Transition: {reason}" if reason else message[:100]
