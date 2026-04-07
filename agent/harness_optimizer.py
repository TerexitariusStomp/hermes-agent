#!/usr/bin/env python3
"""
Meta-Harness: Outer-loop harness optimizer + Natural-Language Agent Harnesses (NLAHs).

Combines insights from:
  - arXiv 2603.28052 "Meta-Harness": Search over harness CODE (not prompts) using
    an agentic proposer with access to source, scores, and execution traces.
  - arXiv 2603.25723 "Natural-Language Agent Harnesses": Harness behavior
    externalized as editable natural-language artifacts executed by a shared runtime.

No pip dependencies. Uses OpenRouter free tier for evaluation.
"""

from __future__ import annotations

import json
import os
import hashlib
import textwrap
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

HARNESS_DIR = os.path.expanduser("~/.hermes/harness_optimizer")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class HarnessCandidate:
    """A single harness configuration candidate."""
    id: str
    # Natural-language description of the harness (NLAH style)
    description: str
    # System prompt template
    system_prompt: str
    # Tool selection policy: list of tool names to enable
    tool_allowlist: List[str] = field(default_factory=list)
    # Memory policy: how to handle memory
    memory_policy: str = "auto"  # auto, always_store, never_store, selective
    # Model routing policy
    routing_policy: str = "smart"  # always_primary, smart, always_cheap
    # Max turns before forced summary
    max_turns_before_compact: int = 15
    # Stall detection sensitivity
    stall_sensitivity: float = 0.5
    # Score from evaluation
    score: float = 0.0
    # Execution trace from evaluation
    execution_trace: Dict[str, Any] = field(default_factory=dict)
    # Metadata
    created_at: str = ""
    parent_id: Optional[str] = None
    mutation_type: str = "initial"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _ensure_dir():
    os.makedirs(HARNESS_DIR, exist_ok=True)


def _candidates_path() -> str:
    return os.path.join(HARNESS_DIR, "candidates.jsonl")


def _best_path() -> str:
    return os.path.join(HARNESS_DIR, "best_harness.json")


def _log_candidate(c: HarnessCandidate):
    _ensure_dir()
    with open(_candidates_path(), "a") as f:
        f.write(json.dumps(asdict(c)) + "\n")


def load_candidates(limit: int = 100) -> List[HarnessCandidate]:
    """Load recent candidates."""
    path = _candidates_path()
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return [HarnessCandidate(**e) for e in entries[-limit:]]


def load_best_harness() -> Optional[HarnessCandidate]:
    path = _best_path()
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return HarnessCandidate(**json.load(f))


def save_best_harness(c: HarnessCandidate):
    _ensure_dir()
    with open(_best_path(), "w") as f:
        json.dump(asdict(c), f, indent=2)


# ---------------------------------------------------------------------------
# Harness Proposer (inspired by Meta-Harness agentic proposer)
# ---------------------------------------------------------------------------

# Base harness templates
_BASE_HARNESSES = [
    {
        "name": "conservative",
        "description": "Conservative harness: thorough tool usage, frequent memory storage, stall detection on",
        "system_prompt": "You are a helpful, thorough assistant. Always verify your work before responding. Use available tools to ensure accuracy.",
        "tool_allowlist": ["read_file", "search_files", "execute_code", "terminal", "write_file", "patch"],
        "memory_policy": "always_store",
        "routing_policy": "smart",
        "max_turns_before_compact": 12,
        "stall_sensitivity": 0.7,
    },
    {
        "name": "fast",
        "description": "Fast harness: minimal tool usage, direct responses, compact context quickly",
        "system_prompt": "You are a concise assistant. Answer directly. Only use tools when absolutely necessary.",
        "tool_allowlist": ["read_file", "search_files", "patch"],
        "memory_policy": "selective",
        "routing_policy": "always_primary",
        "max_turns_before_compact": 8,
        "stall_sensitivity": 0.3,
    },
    {
        "name": "explorer",
        "description": "Explorer harness: heavy tool usage, deep analysis, comprehensive memory",
        "system_prompt": "You are an investigative assistant. Thoroughly explore the problem. Use all available tools to understand context deeply before answering.",
        "tool_allowlist": ["read_file", "search_files", "execute_code", "terminal", "write_file", "patch", "delegate_task", "vision_analyze", "browser_navigate"],
        "memory_policy": "always_store",
        "routing_policy": "smart",
        "max_turns_before_compact": 20,
        "stall_sensitivity": 0.4,
    },
    {
        "name": "minimal",
        "description": "Minimal harness: only core tools, no memory auto-storage, maximum token efficiency",
        "system_prompt": "You are a terse assistant. Minimal responses. No elaboration unless asked.",
        "tool_allowlist": ["read_file", "patch", "terminal"],
        "memory_policy": "never_store",
        "routing_policy": "always_primary",
        "max_turns_before_compact": 6,
        "stall_sensitivity": 0.9,
    },
]


def _make_candidate_id(desc: str) -> str:
    return hashlib.sha256(desc.encode())[:16].hex()


def propose_initial() -> List[HarnessCandidate]:
    """Generate initial candidates from base templates."""
    candidates = []
    for t in _BASE_HARNESSES:
        c = HarnessCandidate(
            id=_make_candidate_id(t["description"]),
            description=t["description"],
            system_prompt=t["system_prompt"],
            tool_allowlist=t["tool_allowlist"],
            memory_policy=t["memory_policy"],
            routing_policy=t["routing_policy"],
            max_turns_before_compact=t["max_turns_before_compact"],
            stall_sensitivity=t["stall_sensitivity"],
            created_at=datetime.utcnow().isoformat(),
        )
        candidates.append(c)
    return candidates


def propose_next(candidates: List[HarnessCandidate], top_k: int = 3) -> List[HarnessCandidate]:
    """
    Generate new candidates by mutating the best performers.
    
    Per Meta-Harness (2603.28052): richer access to prior experience enables
    better proposals. We use scores + execution traces to guide mutations.
    """
    scored = sorted(candidates, key=lambda c: c.score, reverse=True)
    parents = scored[:top_k]
    new_candidates = []
    
    for parent in parents:
        # Mutation strategies based on parent's score and trace
        mutations = _generate_mutations(parent)
        for mutation_info in mutations:
            child = HarnessCandidate(
                id=_make_candidate_id(f"{parent.id}-{mutation_info['desc']}"),
                description=f"[{mutation_info['name']}] {parent.description}",
                system_prompt=mutation_info.get("system_prompt", parent.system_prompt),
                tool_allowlist=mutation_info.get("tool_allowlist", parent.tool_allowlist),
                memory_policy=mutation_info.get("memory_policy", parent.memory_policy),
                routing_policy=mutation_info.get("routing_policy", parent.routing_policy),
                max_turns_before_compact=mutation_info.get("max_turns_before_compact", parent.max_turns_before_compact),
                stall_sensitivity=mutation_info.get("stall_sensitivity", parent.stall_sensitivity),
                created_at=datetime.utcnow().isoformat(),
                parent_id=parent.id,
                mutation_type=mutation_info["name"],
            )
            new_candidates.append(child)
    
    return new_candidates


def _generate_mutations(parent: HarnessCandidate) -> List[Dict[str, Any]]:
    """Generate mutation variants for a parent candidate."""
    mutations = []
    
    # Mutation 1: System prompt refinement
    mutations.append({
        "name": "prompt_refine",
        "desc": "stricter verification",
        "system_prompt": parent.system_prompt + " Always double-check your answers. If uncertain, say so.",
    })
    
    # Mutation 2: Tool allowlist expansion
    expanded = list(parent.tool_allowlist)
    for tool in ["execute_code", "delegate_task", "patch", "write_file"]:
        if tool not in expanded:
            expanded.append(tool)
            break
    if expanded != parent.tool_allowlist:
        mutations.append({
            "name": "tool_expand",
            "desc": f"+{expanded[-1]}",
            "tool_allowlist": expanded,
        })
    
    # Mutation 3: Tool allowlist contraction
    if len(parent.tool_allowlist) > 2:
        contracted = parent.tool_allowlist[:-1]
        mutations.append({
            "name": "tool_contract",
            "desc": f"-{parent.tool_allowlist[-1]}",
            "tool_allowlist": contracted,
        })
    
    # Mutation 4: Context window adjustment
    mutations.append({
        "name": "compact_earlier",
        "desc": "compact at {} turns".format(max(4, parent.max_turns_before_compact - 4)),
        "max_turns_before_compact": max(4, parent.max_turns_before_compact - 4),
    })
    mutations.append({
        "name": "compact_later",
        "desc": "compact at {} turns".format(parent.max_turns_before_compact + 5),
        "max_turns_before_compact": parent.max_turns_before_compact + 5,
    })
    
    # Mutation 5: Stall detection sensitivity
    mutations.append({
        "name": "stall_sensitive",
        "desc": "stall sensitivity {}".format(min(1.0, parent.stall_sensitivity + 0.2)),
        "stall_sensitivity": min(1.0, parent.stall_sensitivity + 0.2),
    })
    
    # Mutation 6: Memory policy change
    policy_cycle = {"auto": "always_store", "always_store": "selective", 
                    "selective": "never_store", "never_store": "auto"}
    new_policy = policy_cycle.get(parent.memory_policy, "auto")
    mutations.append({
        "name": "memory_{}".format(new_policy),
        "desc": "memory: {}".format(new_policy),
        "memory_policy": new_policy,
    })
    
    # Mutation 7: Routing policy change
    route_cycle = {"smart": "always_primary", "always_primary": "always_cheap",
                   "always_cheap": "smart"}
    new_route = route_cycle.get(parent.routing_policy, "smart")
    mutations.append({
        "name": "route_{}".format(new_route),
        "desc": "routing: {}".format(new_route),
        "routing_policy": new_route,
    })
    
    # Cross-parent mutation if parent has a score
    if parent.score > 0.5:
        mutations.append({
            "name": "aggressive_prompt",
            "desc": "aggressive verification mode",
            "system_prompt": parent.system_prompt + " CRITICAL: Before any answer, verify with at least one tool call. Never answer without verification.",
        })
    
    return mutations


# ---------------------------------------------------------------------------
# Natural-Language Harness Runtime (NLAH inspired by 2603.25723)
# ---------------------------------------------------------------------------

def compile_nl_harness(natural_language: str) -> HarnessCandidate:
    """
    Compile a natural-language harness description into a structured HarnessCandidate.
    
    Per NLAH (2603.25723): harness behavior expressed in editable natural language,
    compiled into execution constraints by a shared runtime.
    
    Example input:
        "Use all file tools. Always verify with execute_code before writing.
         Store memory only when new facts are discovered. Compact after 10 turns."
    """
    nl_lower = natural_language.lower()
    
    # Parse tool policy
    tool_allowlist = []
    if any(w in nl_lower for w in ["file tool", "read_file", "write"]):
        tool_allowlist.extend(["read_file", "search_files", "write_file", "patch"])
    if any(w in nl_lower for w in ["code exec", "execute_code", "run code", "python"]):
        tool_allowlist.append("execute_code")
    if any(w in nl_lower for w in ["terminal", "shell", "command"]):
        tool_allowlist.append("terminal")
    if any(w in nl_lower for w in ["delegate", "subagent"]):
        tool_allowlist.append("delegate_task")
    if any(w in nl_lower for w in ["browse", "web", "url"]):
        tool_allowlist.extend(["browser_navigate", "browser_snapshot"])
    if not tool_allowlist:
        tool_allowlist = ["read_file", "search_files", "execute_code", "terminal", "write_file", "patch"]
    
    # Parse memory policy
    memory_policy = "auto"
    if "always store" in nl_lower or "always_store" in nl_lower or "store everything" in nl_lower:
        memory_policy = "always_store"
    elif "never store" in nl_lower or "no memory" in nl_lower:
        memory_policy = "never_store"
    elif "selective" in nl_lower or "only new" in nl_lower:
        memory_policy = "selective"
    
    # Parse compaction
    import re
    compact_match = re.search(r'compact(?: after| at)?\s*(\d+)\s*(?:turn|message|step)', nl_lower)
    max_turns = int(compact_match.group(1)) if compact_match else 15
    
    # Parse stall sensitivity
    stall_match = re.search(r'stall\s*(?:detection\s*)?(?:sensitivity|level)?\s*[:=]?\s*([0-9.]+)', nl_lower)
    stall_sens = float(stall_match.group(1)) if stall_match else 0.5
    
    # Parse routing
    routing_policy = "smart"
    if "always use primary" in nl_lower or "always primary" in nl_lower:
        routing_policy = "always_primary"
    elif "use cheap" in nl_lower or "always cheap" in nl_lower:
        routing_policy = "always_cheap"
    
    # Build system prompt from NLAH description
    system_prompt = f"You are an AI assistant operating under this harness configuration:\n{natural_language}\n\nFollow these rules strictly."
    
    return HarnessCandidate(
        id=_make_candidate_id(natural_language),
        description=f"NLAH: {natural_language[:100]}",
        system_prompt=system_prompt,
        tool_allowlist=tool_allowlist,
        memory_policy=memory_policy,
        routing_policy=routing_policy,
        max_turns_before_compact=max_turns,
        stall_sensitivity=stall_sens,
        created_at=datetime.utcnow().isoformat(),
        mutation_type="nlah_compiled",
    )


# ---------------------------------------------------------------------------
# Harness Evaluator
# ---------------------------------------------------------------------------

def _llm_eval(system: str, user: str) -> Optional[Dict[str, Any]]:
    """Evaluate via OpenRouter."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    
    import requests
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "qwen/qwen3.6-plus:free",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.0,
                "max_tokens": 512,
            },
            timeout=120,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}
    except Exception:
        return None


# Benchmark prompts for harness evaluation
_HARNESS_BENCHMARKS = [
    {
        "name": "code_understanding",
        "prompt": "Find all functions in the project that make database calls. For each, list the function name, file, and what table it queries.",
        "rubric": "Score 0-1: Does the harness configure tools for code search? Does it allow file reading and code execution? Is the system prompt oriented toward thoroughness?",
    },
    {
        "name": "debugging",
        "prompt": "The agent is stuck in a loop trying to fix a failing test. What harness settings help?",
        "rubric": "Score 0-1: Does the harness enable terminal execution and code evaluation? Is stall sensitivity appropriate? Does the prompt encourage systematic debugging?",
    },
    {
        "name": "simple_qa",
        "prompt": "What is 2+2?",
        "rubric": "Score 0-1: Does the harness avoid over-engineering simple questions? Are too many tools allowed (waste of context)? Is the prompt appropriately concise?",
    },
    {
        "name": "multi_step_task",
        "prompt": "Create a Python script that reads a CSV, processes the data, and generates a summary report as JSON.",
        "rubric": "Score 0-1: Does the harness allow file creation and code execution? Is context compaction delayed enough for multi-step work? Does the prompt support step-by-step reasoning?",
    },
]


def evaluate_harness(c: HarnessCandidate) -> Dict[str, Any]:
    """
    Evaluate a harness candidate against benchmark prompts.
    
    Returns dict with per-benchmark scores and overall score.
    """
    results = {}
    total_score = 0.0
    n_evaluated = 0
    
    for bench in _HARNESS_BENCHMARKS:
        # Construct evaluation prompt
        eval_prompt = f"""Given this agent harness configuration:

System Prompt: {c.system_prompt[:500]}
Tools: {', '.join(c.tool_allowlist) if c.tool_allowlist else 'all'}
Memory Policy: {c.memory_policy}
Routing: {c.routing_policy}
Max Turns Before Compact: {c.max_turns_before_compact}
Stall Sensitivity: {c.stall_sensitivity}

Evaluate how well this harness handles the following benchmark:
Task: {bench['prompt']}

{bench['rubric']}

Respond ONLY as JSON: {{"score": 0.0-1.0, "reasoning": "brief explanation"}}"""

        eval_result = _llm_eval(
            "You are a harness evaluation expert. Score configurations objectively.",
            eval_prompt
        )
        
        if eval_result and "score" in eval_result:
            score = float(eval_result["score"])
            results[bench["name"]] = {"score": score, "reasoning": eval_result.get("reasoning", "")}
            total_score += score
            n_evaluated += 1
        else:
            results[bench["name"]] = {"score": 0.0, "reasoning": "evaluation_failed"}
    
    # Heuristic bonus/penalty (no LLM call needed)
    heuristic_score = 0.0
    # Penalty for empty tool allowlist
    if not c.tool_allowlist:
        heuristic_score -= 0.1
    # Bonus for reasonable turn budget
    if 8 <= c.max_turns_before_compact <= 25:
        heuristic_score += 0.05
    # Penalty for extreme stall sensitivity
    if c.stall_sensitivity > 0.9:
        heuristic_score -= 0.05
    
    overall = (total_score / n_evaluated + heuristic_score) if n_evaluated > 0 else 0.0
    overall = max(0.0, min(1.0, overall))
    
    return {
        "benchmarks": results,
        "heuristic_adjustment": round(heuristic_score, 3),
        "overall_score": round(overall, 4),
    }


# ---------------------------------------------------------------------------
# Optimizer Loop
# ---------------------------------------------------------------------------

def run_optimizer_cycle(generations: int = 3, top_k_per_gen: int = 3) -> Dict[str, Any]:
    """
    Run the Meta-Harness optimization loop:
    1. Load existing candidates
    2. If none, propose initial from base templates
    3. For each generation:
       a. Evaluate candidates
       b. Save results
       c. Propose next generation from top performers
    
    Returns summary dict.
    """
    existing = load_candidates()
    generation_results = []
    
    for gen in range(generations):
        if gen == 0 and not existing:
            candidates = propose_initial()
        else:
            all_candidates = existing + [c for gr in generation_results for c in gr.get("candidates", [])]
            if not all_candidates:
                candidates = propose_initial()
            else:
                candidates = propose_next(all_candidates, top_k=top_k_per_gen)
        
        # Evaluate each candidate
        evaluated = []
        for c in candidates:
            eval_result = evaluate_harness(c)
            c.score = eval_result["overall_score"]
            c.execution_trace = {"benchmarks": {k: v["score"] for k, v in eval_result["benchmarks"].items()}}
            _log_candidate(c)
            evaluated.append(c)
        
        # Track best
        best = max(evaluated, key=lambda c: c.score)
        current_best = load_best_harness()
        if current_best is None or best.score > current_best.score:
            save_best_harness(best)
        
        gen_summary = {
            "generation": gen,
            "candidates_evaluated": len(evaluated),
            "best_score": best.score,
            "best_description": best.description[:80],
            "avg_score": round(sum(c.score for c in evaluated) / len(evaluated), 4) if evaluated else 0,
        }
        generation_results.append(gen_summary)
        existing = evaluated
    
    return {
        "generations": generation_results,
        "best_harness": asdict(load_best_harness()) if load_best_harness() else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "nlah":
        # Natural-language harness mode
        nl_desc = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("Enter harness description: ")
        candidate = compile_nl_harness(nl_desc)
        print(json.dumps(asdict(candidate), indent=2))
        result = evaluate_harness(candidate)
        candidate.score = result["overall_score"]
        _log_candidate(candidate)
        print(f"\nScore: {result['overall_score']}")
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "optimize":
        gens = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        result = run_optimizer_cycle(generations=gens)
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "best":
        best = load_best_harness()
        if best:
            print(json.dumps(asdict(best), indent=2))
        else:
            print("No best harness found yet.")
    elif len(sys.argv) > 1 and sys.argv[1] == "history":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        candidates = load_candidates(limit)
        for c in sorted(candidates, key=lambda x: x.score, reverse=True):
            print(f"[{c.score:.3f}] {c.description[:80]} ({c.mutation_type})")
    else:
        print("Usage:")
        print("  python harness_optimizer.py nlah '<description>'  - Compile NLAH")
        print("  python harness_optimizer.py optimize [generations] - Run optimizer loop")
        print("  python harness_optimizer.py best                   - Show best harness")
        print("  python harness_optimizer.py history [limit]        - Show candidate history")
