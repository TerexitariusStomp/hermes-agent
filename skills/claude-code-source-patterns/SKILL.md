---
name: claude-code-source-patterns
category: devops
description: Architecture patterns extracted from chauncygu/collection-claude-code-source-code (decompiled Claude Code, claw-code port, nano-claude-code). Implements context compaction, tool plugin registry, neutral message format, and permission gating.
---

# Claude Code Source Patterns

Patterns extracted from the decompiled Claude Code source code repository at
`/tmp/collection-claude-code-source-code/`. Three implementations analyzed:

1. **claude-code-source-code** (1,902 TypeScript files) - Original decompiled source
2. **claw-code** (109 Python files) - Python port/parody with 1:1 module mapping
3. **nano-claude-code** (45 Python files) - Minimal but complete CLI agent (~1000 LOC)

All patterns verified against source.

## Pattern 1: Tool Plugin Registry (tool_registry.py)

Central registry with ToolDef dataclass containing metadata for smart scheduling:

```python
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

@dataclass
class ToolDef:
    name: str
    schema: Dict[str, Any]         # JSON schema for API
    func: Callable[[Dict, Dict], str]  # fn(params, config) -> str
    read_only: bool = False        # Never mutates state
    concurrent_safe: bool = False  # Safe to run in parallel

_registry: Dict[str, ToolDef] = {}

def register_tool(tool_def: ToolDef) -> None:
    _registry[tool_def.name] = tool_def

def get_tool_schemas() -> List[Dict]:  # For LLM API tool parameter
    return [t.schema for t in _registry.values()]

def get_read_only_tools() -> List[str]:  # Parallel execution candidates
    return [t.name for t in _registry.values() if t.read_only and t.concurrent_safe]
```

**Why better than current middleware**: Current `tool_middleware.py` does name sanitization and retry but has no concept of tool properties (read_only, concurrent_safe). This enables:
- Parallel scheduling of independent read-only tools
- Safety gating (auto-approve read-only, gate write tools)
- Plugin system for third-party tools

## Pattern 2: Context Compaction (compaction.py)

Automatic context window management. Three strategies:

```python
def estimate_tokens(text: str) -> int:
    """Rough estimate: 4 chars ≈ 1 token (works well in practice)"""
    return len(text) // 4

def maybe_compact(state: AgentState, config: dict) -> Optional[str]:
    """Compact messages if approaching context window limit.
    
    Strategies (in priority order):
    1. Summarize oldest tool results that are no longer actively referenced
    2. Trim intermediate reasoning steps from conversation
    3. Replace early conversation with a summary checkpoint
    
    Returns summary text if compaction occurred, None otherwise.
    """
```

**Context window thresholds (from Claude Code source)**:
- 80% of limit: Start tracking closely
- 90% of limit: Begin aggressive compaction  
- 95% of limit: Emergency truncation of non-essential context

## Pattern 3: Generator-Based Event Loop (agent.py)

Agent run() returns a generator producing typed events:

```python
from dataclasses import dataclass
from typing import Generator

@dataclass
class TextChunk: text: str
@dataclass  
class ThinkingChunk: text: str
@dataclass
class ToolStart: name: str; inputs: dict
@dataclass
class ToolEnd: name: str; result: str; permitted: bool
@dataclass
class TurnDone: input_tokens: int; output_tokens: int
@dataclass
class PermissionRequest: description: str; granted: bool = False

def run(user_message, state, config, system_prompt) -> Generator:
    """Multi-turn agent loop yielding typed events.
    
    Usage:
        for event in run(prompt, state, config, system):
            if isinstance(event, TextChunk):
                stream_text(event.text)
            elif isinstance(event, ToolStart):
                show_tool_start(event.name, event.inputs)
            elif isinstance(event, ToolEnd):
                show_tool_result(event.name, event.result)
            elif isinstance(event, TurnDone):
                track_tokens(event.input_tokens, event.output_tokens)
    """
```

**Better than current**: Current implementation has flat function calls with no structured event flow. Typed events enable:
- Clean separation of concerns (rendering vs logic)
- Cancelable execution (check between events)
- Easy mocking/testing of individual events
- Stream processing pipelines

## Pattern 4: Neutral Message Format (providers.py)

Provider-agnostic internal message format with conversion adapters:

```python
# Internal neutral format (used throughout the codebase):
# {
#   "role": "assistant",
#   "content": "text response",
#   "tool_calls": [{"id": "x", "name": "Bash", "input": {"command": "ls"}}]
# }
# {
#   "role": "tool",
#   "tool_call_id": "x", 
#   "name": "Bash",
#   "content": "file1.py\nfile2.py"
# }

def messages_to_anthropic(messages: list) -> list:
    """Convert neutral → Anthropic API format (blocks, tool_use, tool_result)"""
    
def messages_to_openai(messages: list) -> list:  
    """Convert neutral → OpenAI API format (function calls, tool role)"""
```

**Already partially implemented** in Hermes via Portkey/OpenRouter, but this formalizes the abstraction.

## Pattern 5: Permission Gating (agent.py, tools.py)

Three-tier permission system with safe command allowlist:

```python
_SAFE_PREFIXES = (
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "printf", "date",
    "which", "type", "env", "printenv", "uname", "whoami", "id",
    "git log", "git status", "git diff", "git show", "git branch",
    "find ", "grep ", "rg ", "fd ",
    "python ", "python3 ", "node ", 
    "df ", "du ", "free ", "ps ",
)

def _is_safe_bash(cmd: str) -> bool:
    return any(cmd.strip().startswith(p) for p in _SAFE_PREFIXES)

def _check_permission(tool_call: dict, config: dict) -> bool:
    mode = config.get("permission_mode", "auto")
    if mode == "accept-all": return True
    if mode == "manual": return False
    # "auto" mode logic
    if tool_call["name"] in ("Read", "Glob", "Grep"): return True
    if tool_call["name"] == "Bash": return _is_safe_bash(cmd)
    return False  # Write, Edit → ask
```

## Pattern 6: Smart Diff Output (tools.py)

File operations return unified diffs instead of plain success/failure:

```python
def generate_unified_diff(old, new, filename, context_lines=3):
    diff = difflib.unified_diff(old.splitlines(keepends=True),
                                new.splitlines(keepends=True),
                                fromfile=f"a/{filename}", 
                                tofile=f"b/{filename}", n=context_lines)
    return "".join(diff)
```

## Pattern 7: Output Truncation Strategy (tool_registry.py)

Smart truncation preserving beginning and end of large outputs:

```python
def truncate_output(result: str, max_output: int = 32000) -> str:
    if len(result) > max_output:
        first_half = max_output // 2
        last_quarter = max_output // 4
        truncated = len(result) - first_half - last_quarter  
        return (result[:first_half] 
                + f"\n[... {truncated} chars truncated ...]\n"
                + result[-last_quarter:])
    return result
```

## Implementation in Hermes

These patterns have been adapted and implemented in:
- `~/.hermes/context_compaction.py` - Context window management
- `~/.hermes/tool_registry.py` - Plugin-based tool registry

## Pitfalls

- **Token estimation**: 3.5 chars ≈ 1 token (nano-claude-code's ratio, more accurate). Some providers (especially thinking models) have different actual counts. Use provider-reported usage when available.
2. **Tool registry collisions**: If loading third-party tools, check for name conflicts before registration.
3. **Permission bypass risk**: The `auto` mode relies on prefix matching for bash commands. Always gate Write/Edit separately.
4. **Diff size**: Large diffs (>80 lines) should be truncated to avoid context pollution.
5. **Event loop depth**: Recursive tool calls from tools (Agent spawning) need depth tracking to prevent infinite nesting.
