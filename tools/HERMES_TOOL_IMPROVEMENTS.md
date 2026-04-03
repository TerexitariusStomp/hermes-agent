# HERMES_TOOL_IMPROVEMENTS.md -- Patterns from 724-Office

## Status as of 2026-04-03 (Cycle 2)

### ✅ COMPLETED (Cycle 1: 2026-04-03 04:48)
- `tools/recall_tool.py` — semantic memory retrieval (line 806-813 of 724-office tools.py)
- `tools/memory_compress.py` — LLM-based compression pipeline (memory.py 3-stage)
- `tools/self_check_tool.py` — system health diagnostics (tools.py 818-920)
- `tools/create_tool.py` — runtime tool creation (tools.py 1024-1055)
- `vector-memory-routing` SKILL.md updated with 3-stage compression pattern
- `auto_improve.py` enhanced with session health + memory file checks

### ✅ COMPLETED (Cycle 2: 2026-04-03 16:00)
- `tools/diagnose_tool.py` — session file health diagnostics (tools.py 925-1019)
  - Detects orphan tool messages, bad session starts, MCP status, error logs
- `tools/self_check_tool.py` — registered via `registry.register()` (added handler)
- `tools/create_tool.py` — registered 3 tools: `create_custom_tool`, `list_custom_tools`, `remove_custom_tool`
- `tools/custom_tools/` — created directory + `__init__.py` for plugin hot-loading
- `model_tools.py` — added imports for `self_check_tool`, `create_tool`, `memory_compress`, `diagnose_tool`
  - Fixes orphaned module problem from Cycle 1 (modules existed but were never imported)

### 🔧 REMAINING (from 724-office)

## LOW Priority

### 1. `self_check` -- System Self-Diagnostic
**724-Office source**: `tool_self_check` (tools.py lines 818-920)
**Pattern**: Collects session stats, error counts, service uptime, memory/disk, scheduled tasks, memory file status.
**Adapt for Hermes**: Create a tool in `tools/self_check_tool.py` that:
- Reads sessions from state.db (already collected by auto_improve.py)
- Queries systemd/journalctl for error counts
- Checks gateway service health
- Reports MEMORY.md size and freshness
- Returns consolidated health report
**Estimated effort**: 2-3 hours
**Integration**: Register via `registry.register()` in `tools/self_check_tool.py`

### 2. `diagnose` -- Session File & MCP Health Diagnostic
**724-Office source**: `tool_diagnose` (tools.py lines 925-1000)
**Pattern**: Checks session files for JSON validity, orphan tool messages, starts-with-tool-message issues that cause LLM 400 errors.
**Adapt for Hermes**: Create `tools/diagnose_tool.py` that:
- Scans `~/.hermes/state.db` for session message integrity
- Detects orphan tool messages (no matching tool_call_id)
- Flags sessions starting with tool/assistant-with-tool_calls
- Reports byte sizes and message counts per session
**Estimated effort**: 1-2 hours
**Integration**: Register via `registry.register()` in `tools/diagnose_tool.py`

### 3. Three-Stage Memory Pipeline (compress -> deduplicate -> retrieve)
**724-Office source**: memory.py (360 lines)
**Pattern**:
  1. Compress: LLM extracts structured facts (fact, keywords, persons, timestamp, topic) from evicted messages
  2. Deduplicate: Cosine similarity > 0.92 threshold against existing memories, skip duplicates
  3. Retrieve: Embed query, vector search, filter, return formatted text
**Adapt for Hermes**: Current auto_improve does health checks but lacks LLM-based memory compression from evicted session messages. Add a background thread in `run_conversation()` that:
- Detects when session messages are evicted (context window overflow)
- Calls LLM to extract structured facts via COMPRESS_PROMPT
- Deduplicates via cosine similarity
- Stores in vector memory providers (Pinecone/Upstash)
**Estimated effort**: 4-6 hours
**Integration**: Add to `tools/vector_memory_store.py` or create `tools/memory_compressor.py`

## MEDIUM Priority

### 4. `recall` -- Semantic Memory Recall Tool
**724-Office source**: `tool_recall` (tools.py lines 806-813)
**Pattern**: Exposes memory retrieval as a tool callable by the LLM during conversations.
**Adapt for Hermes**: Register memory retrieval as a callable tool so the agent can explicitly search its memory rather than only injecting context at session start.
**Estimated effort**: 30 minutes
**Integration**: Register via `registry.register()` wrapping `vector_memory_store.retrieve()`

### 5. `search_memory` -- Keyword Search in Memory
**724-Office source**: `tool_search_memory` (tools.py lines 766-800)
**Pattern**: grep-based keyword search in memory directory files.
**Adapt for Hermes**: Add a keyword-based search tool for `~/.hermes/memories/` directory (MEMORY.md + daily logs).
**Estimated effort**: 15 minutes

### 6. Context Cache for Zero-Latency Channels
**724-Office source**: `get_cached_context(session_key)` in memory.py line 148
**Pattern**: Pre-computed memory summary cached per session for hardware/voice channels.
**Adapt for Hermes**: Useful for Telegram/WhatsApp gateway where latency matters. Cache the last memory injection result per session_key.
**Estimated effort**: 30 minutes

### 7. Plugin/Extension Directory Pattern
**724-Office source**: `_load_plugins()` (tools.py lines 527-544)
**Pattern**: Scans `plugins/` directory at startup, exec()'s .py files which use @tool decorator to register.
**Adapt for Hermes**: Current `create_tool.py` already implements this. Could add auto-discovery of `~/.hermes/hermes-agent/tools/custom_tools/` at startup in `model_tools.py`.
**Estimated effort**: 1 hour
**Status**: create_tool.py already exists but not integrated into startup

## LOW Priority

### 8. Cross-Session Context Bridge
**724-Office source**: `_get_recent_scheduler_context()` in llm.py lines 231-285
**Pattern**: Extracts recent (2h) scheduled task output from scheduler session and injects into DM session system prompt.
**Adapt for Hermes**: Useful for cross-gateway session continuity.
**Estimated effort**: 1-2 hours

### 9. Multi-Engine Search with Keyword Routing
**724-Office source**: `tool_web_search` (tools.py lines 705-760)
**Pattern**: Routes queries to specialized sources (Tavily for AI, GitHub for code, HF for models).
**Adapt for Hermes**: Hermes already has `web_search` tool with Firecrawl and Parallel. Could extend with GitHub/HF routing.
**Estimated effort**: 1 hour
