"""Multi-Tier Context Compaction — Hive-inspired context management.

Adapted from aden-hive/hive compaction system.

4-tier approach that scales from free to expensive:
- Tier 0: Microcompact (FREE) - remove old tool results by count
- Tier 1: Token Budget Prune (FREE) - prune when context > threshold
- Tier 2: Content Spillover (FREE) - move content to disk, use pointers
- Tier 3: LLM Summary (COSTS API call) - summarize old turns

Usage:
    exec(open(os.path.expanduser("~/.hermes/skills/hivemind-integration/scripts/compaction.py")).read())
    messages = microcompact(messages, keep_last=12)
    messages = prune_by_token_budget(messages, max_tokens=100000)
"""

import json
import re
from typing import Any


def estimate_token_count(text: str) -> int:
    """Rough token count estimate (4 chars per token average)."""
    return len(text) // 4


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        total += estimate_token_count(msg.get('content', ''))
        total += estimate_token_count(json.dumps(msg.get('tool_calls', [])))
        total += 4  # per-message overhead
    return total


def microcompact(
    messages: list[dict],
    keep_last: int = 10,
    compactable_roles: set[str] = None,
) -> list[dict]:
    """Tier 0: Remove old compactable tool results by count.

    Keeps the most recent N tool result messages and removes older ones.
    This is FREE — no LLM call needed.

    Args:
        messages: Full message list
        keep_last: Number of tool results to keep per tool type
        compactable_roles: Roles/types that can be compacted

    Returns:
        Compacted message list
    """
    if compactable_roles is None:
        compactable_roles = {'tool'}

    # Find all tool messages
    tool_indices = [i for i, m in enumerate(messages) if m.get('role') in compactable_roles]

    # Keep the last N, mark the rest for removal
    to_remove = tool_indices[:-keep_last] if len(tool_indices) > keep_last else []

    # Also remove orphaned assistant messages with tool_calls whose tool results are removed
    # (Keep the assistant message structure but remove the content)
    result = []
    remove_indices = set(to_remove)

    for i, msg in enumerate(messages):
        if i in remove_indices:
            continue
        result.append(msg)

    return result


def prune_by_token_budget(
    messages: list[dict],
    max_tokens: int = 80000,
    keep_first: int = 2,
    keep_last: int = 5,
) -> list[dict]:
    """Tier 1: Prune old messages when context exceeds token budget.

    Keeps the first N messages (system prompt) and last N messages
    (most recent context), removing the middle.

    Args:
        messages: Full message list
        max_tokens: Maximum token budget before pruning
        keep_first: Number of initial messages to always keep
        keep_last: Number of final messages to always keep

    Returns:
        Pruned message list with a summary placeholder
    """
    total_tokens = estimate_message_tokens(messages)

    if total_tokens <= max_tokens:
        return messages

    if len(messages) <= keep_first + keep_last + 1:
        return messages  # Not enough to prune

    # Keep first N and last M
    first = messages[:keep_first]
    last = messages[-keep_last:]

    # Calculate how many we removed
    removed_count = len(messages) - keep_first - keep_last
    removed_tokens = total_tokens - estimate_message_tokens(first) - estimate_message_tokens(last)

    # Insert summary placeholder
    summary_msg = {
        'role': 'system',
        'content': (
            f"[CONTEXT COMPACTED: {removed_count} messages removed, "
            f"~{removed_tokens} tokens saved. "
            f"Earlier tool results and responses have been pruned to stay within context budget. "
            f"The conversation continues from the last {keep_last} messages.]"
        )
    }

    return first + [summary_msg] + last


def spill_contents(
    messages: list[dict],
    max_field_chars: int = 4000,
    exclude_tools: set[str] = None,
) -> tuple[list[dict], list[str]]:
    """Tier 2: Move large content fields to disk, replace with pointers.

    For messages with very long content fields, write the content to disk
    and replace with a short pointer.

    Args:
        messages: Message list to process
        max_field_chars: Maximum chars per content field before spilling
        exclude_tools: Tool names to exclude from spilling

    Returns:
        Tuple of (new_messages, list_of_spilled_files)
    """
    import os
    import hashlib
    import time
    from pathlib import Path

    spillover_dir = Path.home() / ".hermes" / "spillover"
    spillover_dir.mkdir(parents=True, exist_ok=True)
    if exclude_tools is None:
        exclude_tools = set()

    spilled = []
    new_messages = []

    for msg in messages:
        content = msg.get('content', '')
        if not content or len(content) <= max_field_chars:
            new_messages.append(msg)
            continue

        # Check if this is a tool result from an excluded tool
        tool_name = msg.get('tool_name', '')
        if tool_name in exclude_tools:
            new_messages.append(msg)
            continue

        # Spill the content
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        filename = f"compact_{int(time.time())}_{content_hash}.txt"
        filepath = spillover_dir / filename

        filepath.write_text(content, encoding='utf-8')
        spilled.append(str(filepath))

        new_msg = dict(msg)
        new_msg['content'] = (
            f"[content spilled to disk: {len(content)} chars]"
            f"\nFile: {filepath}"
            f"\nPreview: {content[:400]}..."
            f"\nCall load_data('{filepath}') to read full content."
        )
        new_messages.append(new_msg)

    return new_messages, spilled


def run_compaction_pipeline(
    messages: list[dict],
    max_tokens: int = 100000,
    tier0_keep: int = 10,
    tier1_max_tokens: int = None,
    tier2_max_chars: int = 4000,
    exclude_tools: set[str] = None,
) -> dict[str, Any]:
    """Run the full 3-tier compaction pipeline (Tier 3 = LLM, done separately).

    Returns dict with new messages and metadata about what was done.
    """
    if tier1_max_tokens is None:
        tier1_max_tokens = max_tokens

    original_count = len(messages)
    original_tokens = estimate_message_tokens(messages)

    # Tier 0: Microcompact
    messages = microcompact(messages, keep_last=tier0_keep)

    # Tier 2: Spill large contents (before tier 1 pruning)
    messages, spilled_files = spill_contents(
        messages,
        max_field_chars=tier2_max_chars,
        exclude_tools=exclude_tools,
    )

    # Tier 1: Token budget prune
    messages = prune_by_token_budget(
        messages,
        max_tokens=tier1_max_tokens,
    )

    final_tokens = estimate_message_tokens(messages)
    tokens_saved = original_tokens - final_tokens

    return {
        'messages': messages,
        'original_message_count': original_count,
        'final_message_count': len(messages),
        'original_tokens': original_tokens,
        'final_tokens': final_tokens,
        'tokens_saved': tokens_saved,
        'spilled_files': spilled_files,
    }
