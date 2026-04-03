#!/usr/bin/env python3
"""Integration test for the extension point lifecycle hook system."""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))

from tools.hooks.hook_manager import HookManager
from tools.hooks.hook_types import HookContext, HookPoint, HookResult
from tools.hooks.builtin_hooks import register_builtin_hooks
from tools.hooks.integration import (
    make_tool_start_hook, make_tool_complete_hook, make_on_tool_error_hook,
)

passed = 0
failed = 0

def check(name, condition, msg=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} -- {msg}")
        failed += 1


# ── 1. HookManager basic lifecycle ──
print("=== 1. HookManager basic lifecycle ===")
mgr = HookManager()
check("Empty manager has no hooks", len(mgr.list_hooks()) == 0)
check("Empty manager has no hook points", len(mgr.list_hook_points()) == 0)

# ── 2. Registration ──
print("\n=== 2. Registration ===")
call_order = []

async def handler1(ctx):
    call_order.append("high")
    return HookResult(abort=False, reason="high processed", severity="info")

async def handler2(ctx):
    call_order.append("low")
    return None  # explicit no-result

async def handler3(ctx):
    call_order.append("info")
    return HookResult(abort=False, reason="info", severity="info")

mgr.register(HookPoint.TOOL_AFTER_EXECUTE, handler1, "high-prio", priority=10)
mgr.register(HookPoint.TOOL_AFTER_EXECUTE, handler2, "low-prio", priority=1)
mgr.register(HookPoint.TOOL_AFTER_EXECUTE, handler3, "info-hook", priority=5)

check("3 hooks registered", len(mgr.list_hooks(HookPoint.TOOL_AFTER_EXECUTE.value)) == 3)
check("3 hook points", len(mgr.list_hook_points()) == 1)

# ── 3. Execution order (priority) ──
print("\n=== 3. Execution order ===")
call_order.clear()
import asyncio
loop = asyncio.new_event_loop()
ctx = HookContext(agent_id="test", tool_name="terminal", tool_args={"command": "echo hello"})
results = loop.run_until_complete(mgr.fire(HookPoint.TOOL_AFTER_EXECUTE, ctx))
check("Hooks fire in priority order", call_order == ["high", "info", "low"], f"got {call_order}")
print(f"  DEBUG: results = {results}")
print(f"  DEBUG: num results = {len(results)}")
check("Results include handlers returning non-None", len(results) >= 2, f"got {len(results)} results: {[type(r).__name__ for r in results]}")

# ── 4. Empty hook point returns [] ──
print("\n=== 4. No hooks registered ===")
mgr2 = HookManager()
r = loop.run_until_complete(mgr2.fire("nonexistent.point", HookContext()))
check("Empty fire returns []", r == [])

# ── 5. Hook that returns abort ──
print("\n=== 5. Abort short-circuits ===")
abort_order = []

async def abort_handler(ctx):
    abort_order.append("abort")
    return HookResult(abort=True, reason="validation failed", severity="error")

async def after_abort(ctx):
    abort_order.append("should-not-run")

mgr3 = HookManager()
mgr3.register(HookPoint.TOOL_BEFORE_EXECUTE, abort_handler, "abort", priority=10)
mgr3.register(HookPoint.TOOL_BEFORE_EXECUTE, after_abort, "after", priority=1)

r = loop.run_until_complete(mgr3.fire(HookPoint.TOOL_BEFORE_EXECUTE, HookContext()))
check("Abort short-circuits remaining hooks", abort_order == ["abort"])
check("Returns 1 result with abort=True", len(r) == 1 and r[0].abort is True)

# ── 6. Built-in hooks registration ──
print("\n=== 6. Built-in hooks ===")
mgr4 = HookManager()
register_builtin_hooks(mgr4)
all_hooks = mgr4.list_hooks()
check(f"Built-in hooks registered ({len(all_hooks)} hooks)", len(all_hooks) >= 4)

points = mgr4.list_hook_points()
check("tool.afterExecute registered", "tool.afterExecute" in points)
check("tool.onError registered", "tool.onError" in points)
check("agent.afterStop registered", "agent.afterStop" in points)

# ── 7. Integration adapters ──
print("\n=== 7. Integration adapters ===")
mgr5 = HookManager()
register_builtin_hooks(mgr5)

tool_start_cb = make_tool_start_hook(mgr5, agent_id="test-agent")
tool_start_cb("tc1", "terminal", {"command": "ls"})
# Should not raise

tool_complete_cb = make_tool_complete_hook(mgr5, agent_id="test-agent")
tool_complete_cb("tc1", "terminal", {"command": "ls"}, "bin\netc\nhome")
# Should not raise

error_hook = make_on_tool_error_hook(mgr5, agent_id="test-agent")
error_hook("terminal", {"command": "rm -rf /"}, Exception("Permission denied"))
# Should not raise

check("Integration adapters do not raise", True)

# ── 8. Metrics summary ──
print("\n=== 8. Metrics ===")
metrics = mgr5.get_metrics()
check(f"Metrics collected ({metrics.total_invocations} invocations)", metrics.total_invocations > 0)
check("Avg latency reported", metrics.avg_latency_ms >= 0)

# ── 9. Hook manager disable ──
print("\n=== 9. Toggle enable/disable ===")
mgr6 = HookManager()
call_count = [0]
async def counting_handler(ctx):
    call_count[0] += 1

mgr6.register(HookPoint.TOOL_AFTER_EXECUTE, counting_handler, "counter")
mgr6.set_enabled(False)
loop.run_until_complete(mgr6.fire(HookPoint.TOOL_AFTER_EXECUTE, HookContext()))
check("Disabled hooks do not fire", call_count[0] == 0)
mgr6.set_enabled(True)
loop.run_until_complete(mgr6.fire(HookPoint.TOOL_AFTER_EXECUTE, HookContext()))
check("Re-enabled hooks fire", call_count[0] == 1)

# ── 10. AIAgent hook methods ──
print("\n=== 10. AIAgent integration ===")
import run_agent
agent_cls = run_agent.AIAgent
check("AIAgent has _setup_hooks", hasattr(agent_cls, '_setup_hooks'))
check("AIAgent has _fire_tool_before", hasattr(agent_cls, '_fire_tool_before'))
check("AIAgent has _fire_tool_after", hasattr(agent_cls, '_fire_tool_after'))
check("AIAgent has get_hook_manager", hasattr(agent_cls, 'get_hook_manager'))
check("AIAgent has _fire_session_end_hook", hasattr(agent_cls, '_fire_session_end_hook'))

# ── 11. Session persistence ──
print("\n=== 11. Session persistence ===")
from tools.session_persist import save_session, load_session, list_sessions, delete_session, build_resume_prompt

test_sid = "test-hook-session-001"
saved = save_session(
    session_id=test_sid,
    summary="Test session for hook integration",
    current_task="Testing session persistence",
    model_name="claude-sonnet-4-5",
)
check("Session saved", saved.exists())

loaded = load_session(test_sid)
check("Session loaded", loaded is not None)
check("Schema version matches", (loaded or {}).get("schemaVersion") == "hermes.session.v1")

prompt = build_resume_prompt(loaded or {})
check("Resume prompt built", "RESUME FROM PREVIOUS SESSION" in prompt)
check("Resume prompt has task context", "Testing session persistence" in prompt)

all_sessions = list_sessions(limit=50)
check("Session listed", any(s["id"] == test_sid for s in all_sessions))

delete_session(test_sid)
check("Session deleted", not (saved.exists() if hasattr(saved, 'exists') else True))
check("Load deleted returns None", load_session(test_sid) is None)

# ── 12. Continuous observation ──
print("\n=== 12. Continuous observation ===")
obs_ctx = HookContext(
    agent_id="test",
    tool_name="terminal",
    tool_args={"command": "echo hello"},
    tool_result="hello\n",
    tool_duration=0.5,
    turn_number=3,
)
from tools.hooks.builtin_continuous_observation import _log_observation
_log_observation(obs_ctx)
from tools.hooks.builtin_continuous_observation import get_observation_count
count = get_observation_count()
check(f"Observation logged (total: {count})", count > 0)

# ── 13. Compact suggester ──
print("\n=== 13. Compact suggester ===")
mgr7 = HookManager()
from tools.hooks.builtin_compact_suggester import register_compact_suggester, _tool_call_counters, COMPACT_THRESHOLD
register_compact_suggester(mgr7)

# Reset counter to known state for this agent
cs_agent_id = "test-cs-isolated"
if cs_agent_id in _tool_call_counters:
    del _tool_call_counters[cs_agent_id]

# Fire COMPACT_THRESHOLD - 1 calls (should not trigger hint)
for i in range(COMPACT_THRESHOLD - 1):
    loop.run_until_complete(mgr7.fire(HookPoint.TOOL_AFTER_EXECUTE, HookContext(agent_id=cs_agent_id)))
assert _tool_call_counters[cs_agent_id] == COMPACT_THRESHOLD - 1

results_49 = loop.run_until_complete(mgr7.fire(HookPoint.TOOL_AFTER_EXECUTE, HookContext(agent_id=cs_agent_id)))
assert _tool_call_counters[cs_agent_id] == COMPACT_THRESHOLD
check(f"Compact hint at {COMPACT_THRESHOLD}th call", any(
    hasattr(r, 'reason') and r.reason and "compact" in r.reason.lower()
    for r in results_49
))

# ── 14. Quality gates - terminal pre-check ──
print("\n=== 14. Quality gates ===")
mgr8 = HookManager()
from tools.hooks.builtin_quality_gates import register_quality_gates
register_quality_gates(mgr8)

# Long-running command without background
bg_ctx = HookContext(
    agent_id="test",
    tool_name="terminal",
    tool_args={"command": "npm run dev"},
)
bg_results = loop.run_until_complete(mgr8.fire(HookPoint.TOOL_BEFORE_EXECUTE, bg_ctx))
bg_warnings = [r for r in bg_results if r.severity == "warn" and "background" in (r.reason or "").lower()]
check("Quality gate warns on background-less dev command", len(bg_warnings) > 0)

# Normal command should not warn
ok_ctx = HookContext(
    agent_id="test",
    tool_name="terminal",
    tool_args={"command": "ls -la"},
)
ok_results = loop.run_until_complete(mgr8.fire(HookPoint.TOOL_BEFORE_EXECUTE, ok_ctx))
no_bg_warns = all("background" not in (r.reason or "").lower() for r in ok_results)
check("Quality gate does not warn on normal command", no_bg_warns)

# ── 15. Token optimizer ──
print("\n=== 15. Token optimizer ===")
mgr9 = HookManager()
from tools.hooks.builtin_token_optimizer import register_token_optimizer, get_token_stats, _agent_costs
register_token_optimizer(mgr9)
_agent_costs.clear()

tok_ctx = HookContext(
    agent_id="test-token",
    model_name="claude-sonnet-4-5",
    message_token_count=1500,
    turn_number=1,
)
loop.run_until_complete(mgr9.fire(HookPoint.MODEL_AFTER_RESPONSE, tok_ctx))
stats = get_token_stats()
check("Token stats tracked", "test-token" in stats)
check("Model usage recorded", stats.get("test-token", {}).get("model_usage", {}).get("claude-sonnet-4-5", 0) == 1)

# ── Summary ──
print(f"\n{'='*50}")
total = passed + failed
print(f"Results: {passed}/{total} passed, {failed} failed")
if failed:
    sys.exit(1)
else:
    print("All hook system tests passed.")
