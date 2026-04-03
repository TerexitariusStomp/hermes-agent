# Hermes Tool Improvement Opportunities — 724-Office Pattern Analysis

Generated: 2026-04-03 19:38 UTC
Source: 724-office integration cycles 1–4 analysis

## Comparison Matrix: 724-Office vs Hermes

The 724-office project reference (`/tmp/724-office/`) contained ~26 tool definitions across
tools.py, memory.py, and llm.py. Hermes has already adapted the key patterns across prior
integration cycles. This document tracks remaining gaps and future improvement opportunities.

## Patterns Already Adapted

| 724-Office Pattern | Hermes Equivalent | Status |
|---|---|---|
| `self_check_tool.py` | `tools/self_check_tool.py` | ✅ Adapted (fix: schema mismatch, enhanced GPU parsing) |
| `create_tool.py` | `tools/create_tool.py` | ✅ Adapted (governance checks, runtime registration) |
| `memory_compress.py` | `tools/memory_compress.py` | ✅ Adapted (3-stage: compress→deduplicate→retrieve) |
| `auto_improve.py` | `tools/auto_improve.py` | ✅ Adapted (9 check categories, hourly cycle) |
| Session health checks | `state.db` queries | ✅ Adapted in auto_improve.py |
| Plugin scanning | `plugins/` directory scan | ✅ Adapted in `collect_self_check_metrics()` |

## Missing Tool Patterns — Future Improvements

### 1. Rate Limit Tracking Tool (HIGH priority)
- **724-Office Pattern**: Built-in rate limit detection per LLM provider with automatic backoff
- **Hermes Gap**: Rate limits handled per-adapter but no unified tracking/exposure tool
- **Adaptation**: Create `tools/rate_limit_tracker.py` that monitors OpenRouter/Portkey headers
  and exposes a `rate_limit_status()` tool for agent visibility into remaining quota
- **Effort**: Medium (2–3 hours)

### 2. Context Window Budget Calculator (MEDIUM priority)
- **724-Office Pattern**: `llm.py` session management includes token budget tracking per conversation
- **Hermes Gap**: `context_compressor.py` handles overflow but no proactive budget calculator tool
- **Adaptation**: Create `tools/context_budget.py` — agent can query `context_budget()` to see
  remaining tokens, estimated messages before overflow, and compression trigger threshold
- **Effort**: Low (1 hour)

### 3. Tool Error Pattern Analyzer (HIGH priority)
- **724-Office Pattern**: Error classification and pattern extraction from tool call failures
- **Hermes Gap**: Dojo monitor tracks success rates but no real-time tool error pattern tool
- **Adaptation**: Create `tools/error_pattern_analyzer.py` — analyzes recent failed tool calls,
  groups by error type, suggests skill improvements. Integrates with Dojo monitor.py pipeline
- **Effort**: Medium (3–4 hours)

### 4. Memory Freshness Score (LOW priority)
- **724-Office Pattern**: Session-key scoped memories with freshness timestamps
- **Hermes Gap**: MEMORY.md has no structured freshness scoring
- **Adaptation**: Add `_freshness_score(entry)` to `memory_offload.py` — entries older than
  threshold get lower priority during offload/retrieval
- **Effort**: Low (1 hour)

### 5. Multi-Tool Batch Orchestrator (MEDIUM priority)
- **724-Office Pattern**: Sequential tool orchestration with conditional branching
- **Hermes Gap**: Agent makes sequential calls but no orchestrator pattern for complex pipelines
- **Adaptation**: Create `tools/tool_orchestrator.py` — define a pipeline of tool calls where
  output of one feeds into next, with failure handling and retry logic
- **Effort**: High (5–6 hours)

## Recommendations for Next Integration Cycle

1. **Focus on Rate Limit Tracking** — Most actionable, addresses real quota management needs
2. **Enhance memory_compress.py** — Fix the corrupted API key line (`_embedding_api_key=***`)
3. **Add context_budget tool** — Quick win for agent self-awareness
4. **Wire create_tool.py into toolsets.py** — Currently exists but may not be auto-discovered
