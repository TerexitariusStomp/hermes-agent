#!/usr/bin/env python3
"""
Context window compaction for Hermes agent.

Prevents context overflow by intelligently summarizing and trimming conversation history.
Based on patterns from nano-claude-code compaction.py.

Strategies (in priority order):
1. Summarize old tool results that are no longer actively referenced
2. Trim intermediate reasoning steps while preserving conclusions
3. Replace early conversation turns with a summary checkpoint

Usage:
    from context_compaction import maybe_compact, estimate_tokens, get_context_stats
    
    stats = get_context_stats(messages)
    if stats.pct_used > 85:
        compacted = maybe_compact(messages, config)
        if compacted:
            print(f"Compacted: removed {compacted['tokens_removed']} tokens")
"""
from __future__ import annotations

import re
import json
from typing import Optional

# Approximate tokens per character (3.5 chars ≈ 1 token, matching nano-claude-code)
CHARS_PER_TOKEN = 3.5

# Context window thresholds
THRESHOLD_WARN = 0.80      # 80% - start monitoring
THRESHOLD_COMPACT = 0.85   # 85% - begin compaction
THRESHOLD_EMERGENCY = 0.95 # 95% - emergency truncation


def estimate_tokens(text: str) -> int:
    """Rough token estimation: 3.5 chars ≈ 1 token (matching nano-claude-code)."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens for a single message in the conversation."""
    content = message.get("content", "")
    if isinstance(content, str):
        return estimate_tokens(content)
    elif isinstance(content, list):
        # Multi-part message (text + tool_use/tool_result blocks)
        total = 0
        for block in content:
            if isinstance(block, dict):
                total += estimate_tokens(json.dumps(block, ensure_ascii=False))
            else:
                total += estimate_tokens(str(block))
        return total
    return estimate_tokens(str(content))


def estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_message_tokens(m) for m in messages)


def get_context_window_limit(config: dict) -> int:
    """Get the context window limit from config or defaults.
    
    Matches nano-claude-code's approach: look up by provider.
    """
    if "context_limit" in config:
        return config["context_limit"]
    
    model = config.get("model", "").lower()
    
    # Match nano-claude limits (from providers.py PROVIDERS dict)
    if any(x in model for x in ["sonnet", "claude", "opus", "haiku"]):
        return 200000
    elif any(x in model for x in ["gpt-4o", "gpt-4", "o1", "o3"]):
        return 128000
    elif "gemini" in model:
        return 1000000
    elif any(x in model for x in ["llama", "phi", "mistral", "mixtral", "gemma"]):
        return 128000
    elif any(x in model for x in ["qwen", "qwq"]):
        return 1000000  # qwen-max/plus have 1M context
    elif any(x in model for x in ["glm"]):
        return 128000
    elif "deepseek" in model:
        return 64000
    elif "kimi" in model or "moonshot" in model:
        return 128000
    else:
        # Conservative default for unknown models
        return 32000


def get_context_stats(messages: list, config: dict) -> dict:
    """Get current context window statistics."""
    limit = get_context_window_limit(config)
    used = estimate_messages_tokens(messages)
    system_tokens = estimate_tokens(config.get("_system_prompt", ""))
    total_with_system = used + system_tokens
    pct_used = total_with_system / limit if limit > 0 else 0
    
    # Count messages by type
    user_count = sum(1 for m in messages if m.get("role") == "user")
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    tool_count = sum(1 for m in messages if m.get("role") == "tool")
    
    # Find largest tool results
    tool_results = [(i, m) for i, m in enumerate(messages) if m.get("role") == "tool"]
    tool_results.sort(key=lambda x: estimate_message_tokens(x[1]), reverse=True)
    
    return {
        "limit": limit,
        "used": used,
        "system_tokens": system_tokens,
        "total": total_with_system,
        "pct_used": pct_used,
        "remaining": max(0, limit - total_with_system),
        "message_count": len(messages),
        "user_messages": user_count,
        "assistant_messages": assistant_count,
        "tool_results": tool_count,
        "largest_tool_results": tool_results[:5],  # top 5 by token count
    }


def summarize_message_content(content: str, max_len: int = 200) -> str:
    """Create a brief summary of message content for compaction."""
    if len(content) <= max_len:
        return content
    
    # Remove code blocks, keep only the description
    code_blocks = re.findall(r'```(?:\w+)\n(.*?)```', content, re.DOTALL)
    code_summary = ""
    if code_blocks:
        n_blocks = len(code_blocks)
        code_summary = f"\n[Code: {n_blocks} block(s) omitted during compaction]"
    
    # Keep first and last portion
    half = max_len // 2
    return content[:half] + f"\n[... {len(content) - max_len} chars omitted ...]\n" + content[-half:] + code_summary


def compact_tool_results(messages: list, target_reduction: int) -> tuple:
    """Compact tool results by keeping headers/metadata and truncating output.
    
    Returns: (new_messages, tokens_reduced)
    """
    new_messages = list(messages)
    total_reduced = 0
    
    # Find tool results sorted by size (largest first)
    tool_results = [(i, m) for i, m in enumerate(new_messages) if m.get("role") == "tool"]
    tool_results.sort(key=lambda x: estimate_message_tokens(x[1]), reverse=True)
    
    for idx, msg in tool_results:
        if total_reduced >= target_reduction:
            break
            
        content = msg.get("content", "")
        original_tokens = estimate_tokens(content)
        
        # Truncate to 25% of original or 500 chars, whichever is larger
        max_chars = max(500, len(content) // 4)
        if len(content) > max_chars:
            new_content = summarize_message_content(content, max_len=max_chars)
            new_messages[idx] = {
                **msg,
                "content": new_content
            }
            total_reduced += original_tokens - estimate_tokens(new_content)
    
    return new_messages, total_reduced


def summarize_early_turns(messages: list) -> tuple:
    """Replace early conversation turns with a summary marker.
    
    Keeps: first user message + last 3 turns
    Replaces: everything in between with a summary
    
    Returns: (new_messages, tokens_reduced)
    """
    if len(messages) < 8:
        return messages, 0
    
    # Keep first message (usually the original prompt)
    keep_first = messages[0]
    # Keep last 6 messages (3 complete turns: user + assistant + tool results)
    keep_last = messages[-6:]
    
    # Calculate tokens being removed
    removed = messages[1:-6]
    tokens_removed = estimate_messages_tokens(removed)
    
    # Insert summary marker
    summary_msg = {
        "role": "system",
        "content": (
            f"[Context compacted: {len(removed)} intermediate messages "
            f"({estimate_messages_tokens(removed)} tokens) summarized. "
            f"User's original request: {keep_first.get('content', '')[:100]}...]"
        )
    }
    
    new_messages = [keep_first, summary_msg] + keep_last
    return new_messages, tokens_removed


def emergency_truncate(messages: list, config: dict) -> tuple:
    """Emergency truncation when context is critically full.
    
    Keeps only: first message + last 2 complete turns
    """
    if len(messages) < 4:
        return messages, 0
    
    keep_first = messages[0]
    keep_last = messages[-4:]  # 2 turns
    
    removed = messages[1:-4]
    tokens_removed = estimate_messages_tokens(removed)
    
    summary_msg = {
        "role": "system",
        "content": (
            f"[EMERGENCY COMPACTION: {len(removed)} messages removed "
            f"to prevent context overflow. Original request preserved.]"
        )
    }
    
    return [keep_first, summary_msg] + keep_last, tokens_removed


def maybe_compact(messages: list, config: dict) -> Optional[dict]:
    """Compact message history if approaching context window limit.
    
    Returns dict with compaction stats if compaction occurred, None otherwise.
    
    Strategy priority:
    1. Compact large tool results (preserve structure, reduce content)
    2. Summarize intermediate conversation turns
    3. Emergency truncation (last resort)
    """
    stats = get_context_stats(messages, config)
    pct = stats["pct_used"]
    
    if pct < THRESHOLD_COMPACT:
        return None  # No compaction needed
    
    # Determine reduction target (aim for 60% usage after compaction)
    target_pct = 0.60
    target_tokens = int(get_context_window_limit(config) * target_pct)
    reduction_needed = stats["total"] - target_tokens
    
    if reduction_needed <= 0:
        return None
    
    new_messages = list(messages)
    total_reduced = 0
    strategies_used = []
    
    # Strategy 1: Compact tool results
    if stats["tool_results"] > 0:
        new_messages, reduced = compact_tool_results(new_messages, reduction_needed)
        total_reduced += reduced
        strategies_used.append("compact_tool_results")
    
    # Strategy 2: Summarize intermediate turns (if still need more reduction)
    remaining_to_reduce = reduction_needed - total_reduced
    if remaining_to_reduce > 0 and len(new_messages) >= 8:
        new_messages, reduced = summarize_early_turns(new_messages)
        total_reduced += reduced
        strategies_used.append("summarize_early_turns")
    
    # Strategy 3: Emergency truncation (if still critically full)
    interim_stats = get_context_stats(new_messages, config)
    if interim_stats["pct_used"] > THRESHOLD_EMERGENCY:
        new_messages, reduced = emergency_truncate(new_messages, config)
        total_reduced += reduced
        strategies_used.append("emergency_truncate")
    
    # Update the messages list in place
    messages.clear()
    messages.extend(new_messages)
    
    return {
        "compacted": True,
        "tokens_removed": total_reduced,
        "strategies_used": strategies_used,
        "messages_before": len(messages),
        "messages_after": len(messages),  # len after extend
        "context_pct_before": round(pct * 100, 1),
        "context_pct_after": round(interim_stats["pct_used"] * 100, 1) if "interim_stats" not in dir() else round(get_context_stats(messages, config)["pct_used"] * 100, 1),
    }


def microcompact_messages(
    messages: list,
    keep_recent_n: int = 3,
    clear_threshold_chars: int = 2000,
    compactable_tools: set = None,
) -> dict:
    """Micro-compaction: clear old tool result content while keeping recent ones.
    
    Based on Claude Code's microCompact.ts pattern. Unlike full compaction,
    this doesn't call an LLM - it just clears the content of old tool results
    while preserving the most recent N.
    
    Args:
        messages: List of message dicts (modified in place)
        keep_recent_n: Number of recent tool results to preserve (default 3)
        clear_threshold_chars: Only clear results larger than this (default 2000)
        compactable_tools: Set of tool names that can be compacted (default: all)
    
    Returns:
        dict with microcompaction stats, or None if nothing was cleared.
    
    Strategy (mirrors Claude Code):
    - Find all tool_result messages
    - Keep the most recent N intact
    - Clear content of older ones above threshold to placeholder text
    - This is MUCH cheaper than LLM compaction and preserves structure
    """
    compactable = compactable_tools or {"Read", "Search", "Grep", "Glob", "WebFetch", "WebSearch", "Terminal", "Bash"}
    
    # Collect all tool results with their indices
    tool_results = []
    for i, m in enumerate(messages):
        if m.get("role") == "tool" and m.get("name") in compactable:
            content = m.get("content", "")
            if len(content) > clear_threshold_chars:
                tool_results.append((i, m, len(content)))
    
    if not tool_results:
        return None
    
    # Keep the most recent N, clear the rest
    tool_results.sort(key=lambda x: x[0])  # Sort by index
    to_keep = tool_results[-keep_recent_n:] if len(tool_results) > keep_recent_n else []
    to_clear = [tr for tr in tool_results if tr not in to_keep]
    
    if not to_clear:
        return None
    
    total_cleared = 0
    for idx, msg, orig_len in to_clear:
        total_cleared += estimate_tokens(msg.get("content", ""))
        name = msg.get("name", "Tool")
        messages[idx] = {
            **msg,
            "content": f"[Tool result cleared by micro-compaction ({orig_len} chars, {estimate_tokens('x' * orig_len)} tokens). Use the {name} tool again if you need this content.]"
        }
    
    return {
        "microcompacted": True,
        "tools_cleared": len(to_clear),
        "tools_kept": len(to_keep),
        "tokens_cleared": total_cleared,
        "type": "microcompact",
    }


def time_based_microcompact(
    messages: list,
    gap_threshold_minutes: float = 30.0,
    keep_recent: int = 2,
    compactable_tools: set = None,
    last_activity_time: float = None,
    current_time: float = None,
) -> dict:
    """Time-based microcompaction: clear old tool results after a time gap.
    
    Based on Claude Code's timeBasedMCConfig pattern. When there's been a gap
    since the last assistant message (user stepped away, context went cold),
    clear old tool results because the cache has expired anyway.
    
    Args:
        messages: List of message dicts
        gap_threshold_minutes: Minutes of inactivity to trigger (default 30)
        keep_recent: Number of recent tool results to keep (default 2)
        compactable_tools: Set of tool names that can be compacted
        last_activity_time: Timestamp of last activity (Unix epoch)
        current_time: Current timestamp (defaults to time.time())
    
    Returns:
        dict with stats or None if no compaction needed.
    """
    import time as _time
    
    current = current_time or _time.time()
    
    # Find last assistant message timestamp
    last_assistant = None
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("timestamp"):
            try:
                last_assistant = float(m["timestamp"])
                break
            except (ValueError, TypeError):
                pass
    
    # If no timestamp recorded, use the last assistant message index as heuristic
    if last_assistant is None and last_activity_time is not None:
        last_assistant = last_activity_time
    
    if last_assistant is None:
        return None
    
    gap_minutes = (current - last_assistant) / 60.0
    if gap_minutes < gap_threshold_minutes:
        return None
    
    # Gap exceeds threshold - clear old tool results
    result = microcompact_messages(
        messages,
        keep_recent_n=keep_recent,
        compactable_tools=compactable_tools,
    )
    
    if result:
        result["type"] = "time_based_microcompact"
        result["gap_minutes"] = round(gap_minutes, 1)
        result["threshold_minutes"] = gap_threshold_minutes
    
    return result


def get_recently_accessed_files(messages: list, max_files: int = 5) -> list:
    """Extract the most recently read/written files from conversation history.
    
    Used for post-compact file restoration - after compaction, these files
    should be re-attached so the model doesn't have to re-read them.
    
    Based on Claude Code's createPostCompactFileAttachments pattern.
    
    Args:
        messages: Conversation messages
        max_files: Maximum number of files to return (default 5)
    
    Returns:
        List of dicts with 'path' and 'tool' keys, ordered by recency.
    """
    import re
    
    file_accesses = []
    seen_paths = set()
    
    for m in reversed(messages):
        name = m.get("name", "")
        role = m.get("role", "")
        
        # Track file reads
        if name in ("Read", "read_file"):
            inputs = m.get("tool_call_id", "")  # Could extract from content
            content = m.get("content", "")
            # Try to extract file path from content
            import re
            path_match = re.search(r'file[:\s]+([\/\w\.\-\~]+)', content[:200], re.IGNORECASE)
            if path_match:
                path = path_match.group(1)
                if path not in seen_paths:
                    seen_paths.add(path)
                    file_accesses.append({"path": path, "tool": name})
        
        # Also track from tool call inputs if available
        if role == "tool" and name in ("Read", "Search", "Glob", "Grep"):
            # Look for file paths in the content
            content = m.get("content", "")
            paths = re.findall(r'(/[^\s:\n]+\.\w+)', content[:500])
            for p in paths[:2]:
                if p not in seen_paths:
                    seen_paths.add(p)
                    file_accesses.append({"path": p, "tool": name})
        
        if len(file_accesses) >= max_files:
            break
    
    return file_accesses[:max_files]


def build_post_compact_context(
    messages: list,
    compact_result: dict,
    max_files_to_restore: int = 5,
) -> dict:
    """Build post-compaction context with restored file info.
    
    After compaction, the model loses access to file contents that were in
    the conversation. This function identifies which files should be re-read
    to restore working context.
    
    Args:
        messages: Messages AFTER compaction
        compact_result: Result from maybe_compact()
        max_files_to_restore: Max files to suggest re-reading
    
    Returns:
        dict with restoration plan
    """
    recent_files = get_recently_accessed_files(messages, max_files=max_files_to_restore)
    
    return {
        "compacted": compact_result.get("compacted", False),
        "tokens_removed": compact_result.get("tokens_removed", 0),
        "files_to_restore": recent_files,
        "restoration_prompt": (
            f"Context was compacted ({compact_result.get('tokens_removed', 0)} tokens removed). "
            f"The following files were recently accessed and may need to be re-read:\n" +
            "\n".join(f"- {f['path']} (via {f['tool']})" for f in recent_files)
            if recent_files else "No files need restoration."
        ),
    }


# Quick self-test
if __name__ == "__main__":
    # Test token estimation (3.5 chars/token)
    assert estimate_tokens("hello world") >= 1
    assert estimate_tokens("a" * 3500) == 1000  # 3500 / 3.5 = 1000
    
    # Test context stats
    test_messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well! Here's a long response." + "x" * 4000},
        {"role": "user", "content": "Can you write some code?"},
        {"role": "assistant", "content": "Sure! ```python\nprint('hi')\n```"},
    ]
    config = {"model": "claude-sonnet-4-5"}
    stats = get_context_stats(test_messages, config)
    assert stats["limit"] == 200000
    assert stats["message_count"] == 4
    assert 0 < stats["pct_used"] < 1
    
    # Test micro-compaction
    big_messages = [
        {"role": "user", "content": "Read these files"},
        {"role": "assistant", "content": "Reading..."},
        {"role": "tool", "name": "Read", "content": "File 1 content " * 500, "tool_call_id": "1"},
        {"role": "assistant", "content": "More..."},
        {"role": "tool", "name": "Read", "content": "File 2 content " * 500, "tool_call_id": "2"},
        {"role": "assistant", "content": "Even more..."},
        {"role": "tool", "name": "Read", "content": "File 3 content " * 500, "tool_call_id": "3"},
        {"role": "tool", "name": "Terminal", "content": "ls output " * 400, "tool_call_id": "4"},
    ]
    
    result = microcompact_messages(big_messages, keep_recent_n=2)
    assert result is not None
    assert result["tools_cleared"] == 2  # First 2 cleared
    assert result["tools_kept"] == 2     # Last 2 kept
    assert "cleared" in big_messages[2]["content"].lower()
    assert "cleared" in big_messages[4]["content"].lower()
    assert "cleared" not in big_messages[6]["content"].lower()
    assert "cleared" not in big_messages[7]["content"].lower()
    
    # Test file extraction
    file_messages = [
        {"role": "tool", "name": "Read", "content": "File contents of /home/user/project/main.py\n..." },
        {"role": "tool", "name": "Read", "content": "Contents of /home/user/project/utils.py\n..."},
        {"role": "tool", "name": "Search", "content": "Found in /home/user/project/config.json\n..."},
    ]
    files = get_recently_accessed_files(file_messages, max_files=3)
    assert len(files) >= 1
    
    print("All tests passed!")
