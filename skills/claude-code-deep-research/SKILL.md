---
name: claude-code-deep-research
category: devops
description: Deep research findings from chauncygu/collection-claude-code-source-code (1,902 TypeScript files). Real Claude Code architecture patterns: hooks, micro-compaction, multi-tier context management, tool execution pipeline, permissions.
---

# Claude Code Deep Research Findings

Source: `https://github.com/chauncygu/collection-claude-code-source-code`
Analyzed: Decompiled TypeScript source (1,902 files), Python port (109 files), nano-claude-code (45 files)

## Architecture Overview

The real Claude Code is a massive application with these key directories:
- `utils/` (564 files) - Core utilities
- `components/` (389 files) - React UI components (Ink-based terminal UI)
- `commands/` (207 files) - CLI commands  
- `tools/` (184 files) - Tool implementations
- `services/` (130 files) - Services (API, MCP, compaction, analytics)
- `hooks/` (104 files) - React hooks
- `ink/` (96 files) - Terminal rendering

### Major Tool Categories (42 tools total)

| Category | Tools |
|----------|-------|
| File I/O | FileReadTool, FileEditTool, FileWriteTool, NotebookEditTool |
| Shell | BashTool, PowerShellTool |
| Search | GlobTool, GrepTool, ToolSearchTool |
| Web | WebFetchTool, WebSearchTool |
| Agent | AgentTool, SendMessageTool, TaskCreateTool, TaskGetTool, TaskListTool, TaskOutputTool, TaskStopTool, TaskUpdateTool, TeamCreateTool, TeamDeleteTool |
| MCP | MCPTool, McpAuthTool, ListMcpResourcesTool, ReadMcpResourceTool |
| Skills | SkillTool |
| Plan/Work | EnterPlanModeTool, ExitPlanModeTool, EnterWorktreeTool, ExitWorktreeTool |
| Other | AskUserQuestionTool, BriefTool, ConfigTool, LSPTool, REPLTool, ScheduleCronTool, SleepTool, SyntheticOutputTool, TodoWriteTool |

## Pattern 1: Multi-Tier Context Management (4,000+ lines across 11 files)

Claude Code has FOUR levels of context management:

### Level 1: Micro-Compaction (services/compact/microCompact.ts - 530 lines)
- **Time-based**: When gap since last assistant message > threshold (default 30min), clear old tool results
- **Cache-editing**: Uses Anthropic's cache editing API to delete tool results without invalidating cache prefix
- **No LLM call** - just content clearing, extremely cheap
- Keeps last N tool results intact

### Level 2: Auto-Compaction (services/compact/autoCompact.ts - 351 lines)  
- Fires when context > threshold (effective_window - buffer)
- **Circuit breaker**: Stops after 3 consecutive failures (prevents API waste)
- First tries session memory compaction, then full compaction
- Suppresses follow-up questions (auto mode)
- Tracks metrics: cache read/write tokens, compaction input/output tokens

### Level 3: Full Compaction (services/compact/compact.ts - 1,705 lines)
- Uses LLM to summarize conversation history
- **Forked agent path**: Reuses main conversation's prompt cache for cheap compaction
- Falls back to regular streaming if cache sharing fails
- **Post-compact**: Re-attaches 5 most recently accessed files as attachments
- **Pre/Post hooks**: Executable hooks fire before and after compaction
- Handles prompt-too-long retries (truncates oldest and retries)

### Level 4: Partial Compaction (compact.ts:772)
- User selects a message to pivot around
- Direction "from": Summarize everything after the selected message
- Direction "up_to": Summarize everything before the selected message
- Preserves prompt cache for the kept portion
- Accepts user feedback as custom instructions for the summary

### Key Constants (from source)
```
AUTOCOMPACT_BUFFER_TOKENS = 20,000    // Leave 20K headroom
WARNING_THRESHOLD_BUFFER = 10,000     // Warning at 10K remaining 
ERROR_THRESHOLD_BUFFER = 5,000        // Error at 5K remaining
MANUAL_COMPACT_BUFFER = 12,000        // Blocking limit for manual compact
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20,000 // Reserve for compaction output
POST_COMPACT_MAX_FILES_TO_RESTORE = 5 // Re-attach 5 files after compact
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3 // Circuit breaker
```

## Pattern 2: Lifecycle Hooks System (utils/hooks.ts - 884+ lines)

Hooks are shell commands that fire at lifecycle events:

| Hook Event | When It Fires |
|------------|--------------|
| PreToolUse | Before a tool executes |
| PostToolUse | After a tool executes |
| PreCompact | Before context compaction |
| PostCompact | After compaction (receives summary) |
| SessionStart | When session begins |
| SessionEnd | When session ends |
| Notification | When notification needed |
| SubagentStart | When subagent spawned |
| SubagentStop | When subagent completes |
| TaskCreated | When task created |
| TaskCompleted | When task finishes |
| Setup, Stop, FileChanged, CwdChanged, ConfigChange, InstructionsLoaded | ... |

Hooks can be:
- **Sync**: Block execution, return JSON to block/modify behavior
- **Async**: Fire and forget
- **Notification**: Show output to user

Hook JSON output format:
```json
{"block": true, "message": "reason"}        // Block the event
{"modify": {"key": "value"}}                 // Modify event parameters  
{"notify": "message to user"}                // Surface notification
```

## Pattern 3: Permission Rules Engine (types/permissions.ts)

Not just 3 modes - a full rules engine:

```typescript
ToolPermissionContext: {
  mode: PermissionMode  // 'default' | 'accept-all' | 'manual' | 'plan'
  
  alwaysAllowRules: ToolPermissionRulesBySource   // Auto-approve matching tools
  alwaysDenyRules: ToolPermissionRulesBySource    // Auto-deny matching tools
  alwaysAskRules: ToolPermissionRulesBySource     // Always prompt for permission
  
  additionalWorkingDirectories: Map<string, AdditionalWorkingDirectory>
  isBypassPermissionsModeAvailable: boolean
  shouldAvoidPermissionPrompts: boolean  // For background agents
}
```

Rules can be scoped by source: user config, project config, plugin, MCP server.

## Pattern 4: Tool Execution Pipeline (services/tools/toolExecution.ts - 60K+ lines)

The tool execution flow:
1. Find tool by name (with deprecated alias fallback)
2. Check cancellation (abort controller)
3. Check permission (CanUseToolFn - async approval check)
4. Execute tool (async generator yielding progress updates)
5. Capture error classification (timeout, permission denied, validation, etc.)
6. Yield message update (lazy - evaluated only if needed)
7. Fire PostToolUse hooks

Tool error classification:
- permission_denied
- validation_error
- timeout
- api_error
- cancellation
- shell_error
- file_error

## Pattern 5: MCP Integration (services/mcp/ - 4 directories, 300+ files)

MCP (Model Context Protocol) is deeply integrated:
- `client.ts` (119K) - MCP client implementation
- `auth.ts` (88K) - OAuth for MCP servers
- `config.ts` (51K) - MCP configuration management
- `useManageMCPConnections.ts` (44K) - Connection lifecycle

## Pattern 6: Session Memory (services/teamMemorySync/ - 44K, services/SessionMemory/)

Separate from conversation memory:
- Team memory with secret scanning
- Watcher for memory file changes
- Independent compaction from conversation
- Memory types: user, feedback, project, reference

## Implementation in Hermes

All patterns from this research have been implemented in:

| File | Lines | Implements |
|------|-------|-----------|
| `~/.hermes/context_compaction.py` | 560 | 4-tier context management (full compaction, micro-compaction, time-based microcompact, post-compact file restoration) |
| `~/.hermes/tool_registry.py` | 510 | Tool plugin registry with metadata (21 tools, parallel scheduling, safety gating, smart truncation) |
| `~/.hermes/hooks.py` | 510 | Lifecycle hooks system (11 event types, sync/async, script discovery, JSON output) |

## Pitfalls

1. **Micro-compaction threshold**: Clearing too many tool results leaves the model with no working context. Always keep at least the last 2-3.
2. **Circuit breaker**: Auto-compaction can fail repeatedly if context is irrecoverably over limit. Without a circuit breaker, it wastes API calls.
3. **Hook timeouts**: Sync hooks block the entire agent loop. Always set reasonable timeouts (default 30s).
4. **Post-compact file paths**: File path extraction from tool output is heuristic. May miss files or extract false positives.
5. **Hook JSON parsing**: Hooks can return plain text or JSON. Need to handle both gracefully.
6. **Token estimation**: 3.5 chars/token is approximation. Provider-reported usage is always more accurate.
7. **Time-based compaction**: Needs timestamp tracking on messages. Without it, falls back to heuristics.

## Verification

```bash
cd ~/.hermes
python3 context_compaction.py && echo "OK: context_compaction"
python3 tool_registry.py && echo "OK: tool_registry"  
python3 hooks.py && echo "OK: hooks"
```

## Sources

- collection-claude-code-source-code: https://github.com/chauncygu/collection-claude-code-source-code
- Decompiled source: claude-code-source-code/src/ (1,902 TypeScript files)
- Key files studied:
  - services/compact/compact.ts (1,705 lines)
  - services/compact/autoCompact.ts (351 lines)
  - services/compact/microCompact.ts (530 lines)
  - services/tools/toolExecution.ts (60K+ lines)
  - services/api/claude.ts (125K+ lines)
  - utils/hooks.ts (884+ lines)
  - Tool.ts (29K lines - tool architecture)
  - types/hooks.ts (hook type definitions)
  - types/permissions.ts (permission rules)
