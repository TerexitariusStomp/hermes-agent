"""
Langfuse-compatible observability system for Hermes Agent.

Tracks all LLM API calls (agent + benchmark evaluations) and sends traces to
Langfuse cloud. Works via HTTP API calls — no Langfuse SDK dependency needed.
Complements the benchmark_tool.py evaluation framework.

Features:
- Trace/session tracking for every agent conversation
- Span-level timing for tool calls
- Score ingestion from benchmark evaluations
- Langfuse cloud API integration (already configured: LANGFUSE_BASE_URL)
- Local SQLite fallback for offline operation
- Automatic session correlation via session_id
"""

import json
import os
import time
import hashlib
import requests
from datetime import datetime
from typing import Optional


# ============================================================
# Configuration
# ============================================================

LANGFUSE_URL = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
LANGFUSE_PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SK = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_ENABLED = bool(LANGFUSE_PK and LANGFUSE_SK)

LOCAL_TRACE_DIR = os.path.expanduser("~/.hermes/traces")


def _ensure_local_dir():
    os.makedirs(LOCAL_TRACE_DIR, exist_ok=True)


def _session_id() -> str:
    return os.environ.get("HERMES_SESSION_ID", "default")


# ============================================================
# Langfuse REST API (no SDK needed)
# ============================================================

def _langfuse_auth():
    """Basic auth for Langfuse API."""
    return (LANGFUSE_PK, LANGFUSE_SK)


def _langfuse_post(endpoint: str, data: dict) -> dict:
    """Send a request to Langfuse API."""
    if not LANGFUSE_ENABLED:
        return {"status": "disabled", "local": True}
    url = f"{LANGFUSE_URL.rstrip('/')}{endpoint}"
    try:
        resp = requests.post(url, json=data, auth=_langfuse_auth(),
                            headers={"Content-Type": "application/json"},
                            timeout=10)
        return {"status": resp.status_code, "ok": resp.ok}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def trace_session_create(name: str = None, user_id: str = None, 
                         metadata: dict = None, session_id: str = None) -> dict:
    """
    Create a Langfuse trace for this agent session.
    Returns trace_id for subsequent span/score updates.
    """
    sid = session_id or _session_id()
    ts = datetime.now().isoformat()
    trace = {
        "id": f"hermes-{sid}",
        "name": name or f"hermes-session-{sid}",
        "userId": user_id or "hermes-agent",
        "sessionId": sid,
        "input": {"session_id": sid},
        "metadata": metadata or {"platform": os.environ.get("HERMES_PLATFORM", "cli")},
        "timestamp": ts,
    }
    # Also store locally
    _ensure_local_dir()
    local_path = os.path.join(LOCAL_TRACE_DIR, f"{sid}.jsonl")
    with open(local_path, "a") as f:
        f.write(json.dumps({"type": "trace", "data": trace, "ts": ts}) + "\n")

    if LANGFUSE_ENABLED:
        result = _langfuse_post("/api/public/traces", trace)
        trace["langfuse"] = result
    return trace


def trace_generation(name: str, model: str, prompt: dict, completion: str,
                    usage: dict = None, session_id: str = None,
                    metadata: dict = None) -> dict:
    """
    Trace an LLM generation (agent API call or benchmark eval).
    Creates a span + generation entry.
    """
    sid = session_id or _session_id()
    ts = datetime.now().isoformat()
    gen = {
        "id": f"gen-{hashlib.md5(f'{sid}{ts}'.encode()).hexdigest()[:12]}",
        "traceId": f"hermes-{sid}",
        "name": name,
        "model": model,
        "input": prompt,
        "output": completion[:10000],  # Cap output size
        "usage": usage or {},
        "metadata": metadata or {},
        "startTime": ts,
    }
    # Local trace
    _ensure_local_dir()
    local_path = os.path.join(LOCAL_TRACE_DIR, f"{sid}.jsonl")
    with open(local_path, "a") as f:
        f.write(json.dumps({"type": "generation", "data": gen, "ts": ts}) + "\n")

    if LANGFUSE_ENABLED:
        result = _langfuse_post("/api/public/observations", gen)
        gen["langfuse"] = result
    return gen


def trace_span(name: str, tool_name: str, tool_input: dict, tool_output: str,
              duration_ms: float, session_id: str = None, metadata: dict = None) -> dict:
    """
    Trace a tool execution span.
    """
    sid = session_id or _session_id()
    ts = datetime.now().isoformat()
    span = {
        "id": f"span-{hashlib.md5(f'{sid}{ts}'.encode()).hexdigest()[:12]}",
        "traceId": f"hermes-{sid}",
        "name": tool_name,
        "input": str(tool_input)[:5000],
        "output": str(tool_output)[:5000],
        "startTime": ts,
        "endTime": ts,
        "metadata": {
            "duration_ms": duration_ms,
            **(metadata or {}),
        },
    }
    _ensure_local_dir()
    local_path = os.path.join(LOCAL_TRACE_DIR, f"{sid}.jsonl")
    with open(local_path, "a") as f:
        f.write(json.dumps({"type": "span", "data": span, "ts": ts}) + "\n")

    return span


def trace_score(name: str, value: float, session_id: str = None, 
               comment: str = None, config: dict = None) -> dict:
    """
    Record a score (e.g., benchmark result) on the current trace.
    """
    sid = session_id or _session_id()
    ts = datetime.now().isoformat()
    score = {
        "traceId": f"hermes-{sid}",
        "name": name,
        "value": value,
        "comment": comment or "",
        "config": config or {},
        "dataType": "NUMERIC",
    }
    _ensure_local_dir()
    local_path = os.path.join(LOCAL_TRACE_DIR, f"{sid}.jsonl")
    with open(local_path, "a") as f:
        f.write(json.dumps({"type": "score", "data": score, "ts": ts}) + "\n")

    if LANGFUSE_ENABLED:
        result = _langfuse_post("/api/public/scores", score)
        score["langfuse"] = result
    return score


# ============================================================
# Query helpers
# ============================================================

def query_traces(limit: int = 10, user_id: str = None, name: str = None) -> dict:
    """Query recent traces from Langfuse."""
    if LANGFUSE_ENABLED:
        params = {"limit": limit}
        if user_id:
            params["userId"] = user_id
        if name:
            params["name"] = name
        try:
            resp = requests.get(f"{LANGFUSE_URL.rstrip('/api/public')}/api/public/traces",
                              params=params, auth=_langfuse_auth(), timeout=10)
            if resp.ok:
                return resp.json()
        except:
            pass
    
    # Fallback to local
    _ensure_local_dir()
    traces = []
    for fname in sorted(os.listdir(LOCAL_TRACE_DIR), reverse=True)[:limit]:
        if not fname.endswith(".jsonl"):
            continue
        path = os.path.join(LOCAL_TRACE_DIR, fname)
        first_line = ""
        with open(path) as f:
            first_line = f.readline().strip()
        if first_line:
            try:
                traces.append(json.loads(first_line))
            except:
                pass
    return {"data": traces, "source": "local"}


def get_trace_summary(session_id: str = None) -> dict:
    """Get a summary of all events for a session."""
    sid = session_id or _session_id()
    _ensure_local_dir()
    local_path = os.path.join(LOCAL_TRACE_DIR, f"{sid}.jsonl")
    if not os.path.exists(local_path):
        return {"error": f"No trace found for session {sid}"}
    
    events = {"trace": 0, "generation": 0, "span": 0, "score": 0}
    total_spans = []
    scores = []
    
    with open(local_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                etype = data.get("type", "unknown")
                events[etype] = events.get(etype, 0) + 1
                
                if etype == "span":
                    dur = data["data"].get("metadata", {}).get("duration_ms", 0)
                    total_spans.append(dur)
                elif etype == "score":
                    scores.append(data["data"])
            except:
                pass
    
    summary = {
        "session_id": sid,
        "event_counts": events,
        "total_span_time_ms": sum(total_spans),
        "avg_span_time_ms": round(sum(total_spans) / len(total_spans), 1) if total_spans else 0,
        "scores": scores,
    }
    return summary


# ============================================================
# Tool registration
# ============================================================

def _observability_handler(args: dict) -> str:
    mode = args.get("mode", "status")

    if mode == "status":
        return json.dumps({
            "langfuse_enabled": LANGFUSE_ENABLED,
            "langfuse_url": LANGFUSE_URL,
            "local_trace_dir": LOCAL_TRACE_DIR,
            "trace_files": len([f for f in os.listdir(LOCAL_TRACE_DIR) if f.endswith(".jsonl")]) if os.path.exists(LOCAL_TRACE_DIR) else 0,
        })

    if mode == "create_trace":
        result = trace_session_create(
            name=args.get("name"),
            user_id=args.get("user_id"),
            metadata=json.loads(args["metadata"]) if args.get("metadata") else None,
        )
        return json.dumps(result, default=str)

    if mode == "record_score":
        result = trace_score(
            name=args["name"],
            value=float(args["value"]),
            session_id=args.get("session_id"),
            comment=args.get("comment"),
            config=json.loads(args["config"]) if args.get("config") else None,
        )
        return json.dumps(result, default=str)

    if mode == "query":
        result = query_traces(
            limit=int(args.get("limit", 10)),
            user_id=args.get("user_id"),
            name=args.get("name"),
        )
        return json.dumps(result, default=str)[:20000]

    if mode == "summary":
        result = get_trace_summary(args.get("session_id"))
        return json.dumps(result, default=str)

    return json.dumps({"error": f"Unknown mode: {mode}"})


def register_tool():
    from tools.registry import registry
    registry.register(
        name="observability",
        toolset="observability",
        schema={
            "name": "observability",
            "description": "Trace and monitor LLM API calls and benchmark evaluations. Integrates with Langfuse cloud for observability. Track sessions, record benchmark scores as trace scores, query recent traces. Use after running benchmarks to persist results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["status", "create_trace", "record_score", "query", "summary"],
                        "description": "Mode: 'status' to check config, 'create_trace' for new session, 'record_score' to log benchmark score, 'query' recent traces, 'summary' for session stats",
                    },
                    "name": {"type": "string", "description": "Trace or score name"},
                    "value": {"type": "string", "description": "Score value (numeric, for 'record_score')"},
                    "session_id": {"type": "string", "description": "Session ID to trace"},
                    "user_id": {"type": "string", "description": "User ID for filtering"},
                    "metadata": {"type": "string", "description": "JSON metadata dict"},
                    "comment": {"type": "string", "description": "Score comment"},
                    "config": {"type": "string", "description": "JSON config dict"},
                    "limit": {"type": "string", "description": "Limit for query (default 10)"},
                },
                "required": ["mode"],
            },
        },
        handler=lambda args, **kw: _observability_handler(args),
    )


register_tool()
