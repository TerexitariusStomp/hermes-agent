---
name: hivemind-integration
description: >
  Patterns from aden-hive/hive -- a production agent harness framework (~65k lines).
  Implements Hive-inspired improvements to Hermes:
  - Tool result spillover (results > 30KB go to disk with pointer)
  - Multi-tier context compaction (microcompact first, then LLM)
  - Implicit judge (auto-complete when task done)
  - Stall/doom loop detection and escape
  - Checkpoint-based crash recovery
version: 1.0.0
license: MIT
metadata:
  author: Hermes Agent
  source: "https://github.com/aden-hive/hive"
  hermes:
    tags: [hive, context-management, crash-recovery, stall-detection]
    category: devops
allowed-tools: Bash(python3:*) Read Write patch execute_code memory
---

## Overview

aden-hive/hive is a production agent runtime with sophisticated context management,
multi-tier compaction, crash recovery, and stall detection. This skill integrates
its best patterns into Hermes.

## Core Improvements

### 1. Tool Result Spillover (spillover.py)

Tool results exceeding a size threshold (default: 30KB) are automatically written
to disk. The conversation receives a pointer file reference instead. The agent calls
`load_data(filepath)` when it needs specific content.

Benefits:
- Prevents context exhaustion from large tool outputs (web search, file reads)
- Error results are never truncated (always full)
- load_data() results are never re-spilled (prevents infinite loops)
- Preserves full information on demand

Usage in Hermes agent loop:
```python
from hivemind_integration.spillover import ToolSpillover
spillover = ToolSpillover(max_inline_chars=30000, spillover_dir="/tmp/hermes_spillover")
result = execute_tool(tool_name, args)
result = spillover.maybe_spill(result, tool_name, exclude_tools={"load_data"})
```

### 2. Multi-Tier Context Compaction (compaction.py)

4-tier compaction system that scales from free to expensive:

- **Tier 0 (Microcompact):** Remove old compactable tool results by count.
  Keep only the 10 most recent read_file/search/extract results.
  Cost: FREE (no LLM call)

- **Tier 1 (Token Budget Prune):** When context > 75% of max, remove oldest
  tool result content, keep structure.
  Cost: FREE

- **Tier 2 (Content Spillover):** Move large inline content to disk files,
  replace with file pointers.
  Cost: FREE

- **Tier 3 (LLM Summary):** When context > 90% of max, use LLM to summarize
  older conversation turns.
  Cost: 1 LLM call

### 3. Implicit Judge (judge.py)

Deterministic completion detection:
- When all required output keys are filled
- When no tools are pending and agent produced substantive content
- Auto-complete the loop without waiting for explicit "I'm done"

### 4. Stall/Doom Loop Detection (stall_detector.py)

Detects when the agent is stuck in a loop:
- Same tool called 5+ times with same args (fingerprint match)
- No progress in tool call pattern (same sequence repeated)
- Excessive retries without state change

Escape strategy:
- Level 1: Inject a meta-prompt reminding agent to try a different approach
- Level 2: Force a different tool selection
- Level 3: Report failure to user with loop details

### 5. Checkpoint Recovery (checkpoint.py)

Persists session state at tool call boundaries:
- Current conversation history
- Tool call sequence
- Visit counts per tool
- Session metadata

Supports resume from last checkpoint after crash/restart.

## Integration Notes

These modules are designed to be drop-in enhancements to the existing Hermes
agent loop. They don't require structural changes to run_agent.py or model_tools.py.

Each module can be used independently:
- Spillover: Add to tool result processing pipeline
- Compaction: Add to context management before API calls
- Judge: Add to loop termination logic
- Stall detection: Add to iteration loop monitoring
- Checkpoint: Add to session state persistence
