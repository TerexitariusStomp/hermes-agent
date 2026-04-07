# Lessons Learned from aden-hive/hive Integration

## What Works Well

### 1. Tool Result Spillover
- **Pattern**: Large outputs go to disk, conversation gets a pointer
- **Why it works**: Prevents context exhaustion without losing data
- **Hermes impact**: Can add this to tool result processing in model_tools.py
- **Caveat**: Never spill error results (always show full errors). Never re-spill load_data() results.

### 2. Multi-Tier Compaction
- **Pattern**: Cheap-to-expensive cascade (microcompact → token prune → content spillover → LLM summary)
- **Why it works**: Minimizes LLM API calls; free operations first
- **Hermes impact**: Add to context_compressor.py as pre-LLM filtering layer
- **Caveat**: Keep system prompt and last few messages always intact

### 3. Stall Detection
- **Pattern**: Fingerprint tool calls (tool_name + args_hash), detect repetition
- **Thresholds**: 3+ identical tool+args = exact repetition, 5+ same tool = single tool loop
- **Why it works**: Catches doom loops early before wasting too many iterations
- **Hermes impact**: Add lightweight check to the agent loop in run_agent.py
- **Caveat**: Don't false-positive on legitimate iterative work (reading multiple files sequentially)

### 4. Checkpoint Recovery
- **Pattern**: Save conversation at tool call boundaries
- **Why it works**: Can resume after crashes without losing progress
- **Hermes impact**: Add checkpoint save after each tool call result
- **Caveat**: Trim conversation before checkpointing (keep first 2 + last 30 messages)

## Integration Priority (Highest Impact First)

1. **Tool Result Spillover** - Immediate benefit: no more context exhaustion from large outputs
2. **Stall Detection** - Saves tokens by catching loops early  
3. **Multi-Tier Compaction** - Free compaction before expensive LLM summarization
4. **Checkpoint Recovery** - Production reliability feature
5. **Implicit Judge** - Nice-to-have loop termination optimization

## What Didn't Translate
- **Graph execution model**: Hermes is fundamentally a single-agent loop, not a graph
- **Queen Bee orchestration**: Hermes uses delegate_task, not a central orchestrator
- **Judge as separate agent**: Hermes can do quality checks in-line
- **HITL pause/resume**: Hermes has /suspend, different pattern
