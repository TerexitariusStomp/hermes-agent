# AutoHarness Integration Analysis
## Source: https://github.com/aiming-lab/AutoHarness (MIT License)

## Overview
AutoHarness is a governance framework for AI agents with 958 passing tests and three operating modes (Core/Standard/Enhanced). It provides safety, audit, and observability layers.

## Architecture Analyzed

### 1. Tool Governance Pipeline (`core/pipeline.py`)
- **Core (6-step)**: Parse/Validate -> Risk Classify -> Permission Check -> Execute -> Output Sanitize -> Audit
- **Standard (8-step)**: Adds interface validation and hooks
- **Enhanced (14-step)**: Full pipeline with turn governor, alias resolution, post-hooks, failure hooks
- **Pattern**: Pipeline architecture with configurable steps based on "constitution" config

### 2. Risk Classifier (`core/risk.py`, `rules/`)
- Pure regex pattern matching for <5ms latency
- Pre-compiled regex rules organized by tool category
- Risk levels: low, medium, high, critical
- Modes: "rules" (current), "llm" (planned), "hybrid" (planned)
- Custom rules can be added at runtime

### 3. Permission Engine (`core/permissions.py`)
- 3-level model: tool -> path -> operation
- Layered defense with explicit denies taking priority
- Policy-based defaults per tool with allow/deny/ask decisions
- Risk threshold checking (auto-ask when risk exceeds threshold)
- Pattern matching for allow/deny paths and patterns

### 4. Audit Engine (`core/audit.py`)
- Thread-safe JSONL logging for all governance decisions
- Retention-based cleanup (default 30 days)
- Captures: tool call, risk, hooks, permission, result
- Statistics and analytics support

### 5. Constitution Engine (`core/constitution.py`)
- YAML-based configuration with cascading discovery (user -> project -> default)
- Supports merging multiple constitution files
- Pydantic validation for type safety
- Defines: identity, rules, tool permissions, risk configs, hooks, audit settings

### 6. Agent Loop (`agent_loop.py`)
- Full integration layer wiring all subsystems
- Context management with token budgeting
- Prompt assembly with cache boundaries
- Session persistence with cost tracking
- Multi-agent orchestration support

## Patterns Adapted for Hermes

### Implemented:
1. **Risk Classification** - AutoHarness's regex-based RiskClassifier adapted as `classify_risk()`
   - 18 rules covering critical to low risk operations
   - Pre-compiled patterns for <5ms latency
   - Pure regex (no LLM calls) for speed

2. **Permission Engine** - Simplified 2-level model (tool + risk threshold)
   - Built-in permission map for all 24 Hermes tools
   - Risk threshold checking per tool
   - Forbidden path patterns for sensitive files

3. **Audit Trail** - JSONL logging adapted from AutoHarness AuditEngine
   - Records: tool, risk, permission, result, error
   - 7-day retention (vs 30 in AutoHarness for disk space)
   - Statistics and analytics

4. **6-Step Pipeline** - `govern_tool_call()` function
   - Parse/Validate -> Risk -> Permission -> Execute -> Sanitize -> Audit
   - Returns structured dict with allowed/risk/permission/warnings

### Not Implemented (future consideration):
1. **Constitution System** - YAML-based governance config
   - Could replace built-in defaults for easier customization
   - Would need Pydantic validation layer

2. **Hook System** - Pre/post tool execution hooks
   - Could add automated safety checks
   - Currently manual via governance module

3. **Session Persistence** - Cost tracking and state management
   - Hermes already has session system
   - Could integrate governance decisions into session history

4. **Multi-Mode Pipeline** - Core/Standard/Enhanced
   - Currently only "core" (6-step) mode
   - Could add enhanced mode for critical operations

5. **Client Wrapping** - Transparent governance via AutoHarness.wrap()
   - Would require changes to how Hermes calls tools
   - Better approach: wrap individual tool calls via decorate pattern

## Testing Results
All risk cases correctly classified and governed:
- Safe operations (ls, cat, ps): allowed
- Destructive (rm -rf /): flagged as critical, requires ask
- Forbidden paths (/etc/shadow): denied
- Remote execution (curl | bash): high risk, requires ask
- Credential export: high risk, tracked

## Integration Plan

### Phase 1 (Current): ✅
- [x] Risk classification module
- [x] Permission engine  
- [x] Audit trail
- [x] Basic governance pipeline

### Phase 2 (Future):
- [ ] Wrap individual tool calls in Hermes agent loop
- [ ] Add constitution YAML for easy configuration
- [ ] Integrate with observability (Langfuse/LangSmith/W&B)
- [ ] Add hook system for pre/post tool validation
- [ ] Session persistence with governance decisions

### Phase 3 (Advanced):
- [ ] Enhanced pipeline mode for critical operations
- [ ] Multi-agent governance profiles
- [ ] Automated risk rule updates via LLM
- [ ] Cost attribution per tool call with governance decisions
