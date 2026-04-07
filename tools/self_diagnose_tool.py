"""Self-Diagnose Tool for Hermes Agent

Based on 724-office's diagnose + self_check tool patterns.
Provides structured diagnostic checks for session health, gateway connectivity,
memory providers, and recent errors.

Registered as a Hermes tool available to the LLM.
"""

# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
# import_cleanup_done
from datetime import datetime, timezone
import json
import os
import sqlite3
import subprocess
import time

from tools.registry import registry


HERMES_HOME = os.path.expanduser("~/.hermes")
STATE_DB = os.path.join(HERMES_HOME, "state.db")
SELF_IMP_LOG = os.path.join(HERMES_HOME, "SELF_IMP_LOG.jsonl")


def _get_recent_errors(count=5) -> str:
    """Get recent errors from SELF_IMP_LOG.jsonl."""
    if not os.path.exists(SELF_IMP_LOG):
        return "No improvement log found."
    try:
        with open(SELF_IMP_LOG) as f:
            entries = [json.loads(l) for l in f if l.strip()]
        failed = [e for e in entries if e.get("status") in ("fail", "partial_success")]
        recent = failed[-count:]
        if not recent:
            return "No errors found in improvement log."
        lines = []
        for e in recent:
            lines.append(f"[{e.get('dt', '?')}] {e['action']}: {e.get('detail', '')[:200]}")
        return "\n".join(lines)
    except Exception as ex:
        return f"Error reading log: {ex}"


def _check_session_files_health() -> str:
    """Check state.db for session file health issues (724-office diagnose pattern)."""
    if not os.path.exists(STATE_DB):
        return "state.db not found."
    try:
        conn = sqlite3.connect(STATE_DB)
        c = conn.cursor()

        # Count sessions
        c.execute("SELECT COUNT(*) FROM sessions")
        session_count = c.fetchone()[0]

        # Count messages
        c.execute("SELECT COUNT(*) FROM messages")
        msg_count = c.fetchone()[0]

        # Check for bad roles (orphan messages)
        c.execute("SELECT COUNT(*) FROM messages WHERE role NOT IN ('system', 'user', 'assistant', 'tool', 'session_meta')")
        bad_role = c.fetchone()[0]

        # Count tool messages
        c.execute("SELECT COUNT(*) FROM messages WHERE role='tool'")
        tool_count = c.fetchone()[0]

        # Count assistant messages with tool_calls
        c.execute("SELECT COUNT(*) FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL")
        assistant_with_tools = c.fetchone()[0]

        conn.close()

        status = "OK" if bad_role == 0 else f"ISSUES: {bad_role} messages with invalid roles"
        return (
            f"Sessions: {session_count}, Messages: {msg_count}\n"
            f"  Tool messages: {tool_count}\n"
            f"  Assistant + tool_calls: {assistant_with_tools}\n"
            f"  Bad-role messages: {bad_role}\n"
            f"  Status: {status}"
        )
    except Exception as e:
        return f"Session health check failed: {e}"


def _check_gateway_connectivity() -> str:
    """Check gateway platform connectivity."""
    results = []

    # Portkey Gateway
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
             "http://localhost:8787/"],
            capture_output=True, text=True, timeout=10
        )
        status = r.stdout.strip()
        results.append(f"Portkey Gateway (localhost:8787): {'OK' if status == '200' else f'DOWN (HTTP {status})'}")
    except Exception as e:
        results.append(f"Portkey Gateway: DOWN ({e})")

    # OpenRouter
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
             "https://openrouter.ai/api/v1/models"],
            capture_output=True, text=True, timeout=10
        )
        status = r.stdout.strip()
        results.append(f"OpenRouter: {'OK' if status == '200' else f'DOWN (HTTP {status})'}")
    except Exception as e:
        results.append(f"OpenRouter: DOWN ({e})")

    return "\n".join(results)


def _check_system_resources() -> str:
    """Check system resource usage."""
    results = []

    # Disk
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        if len(lines) > 1:
            parts = lines[1].split()
            results.append(f"Disk: {parts[2]} used / {parts[1]} ({parts[4]}%)")
    except Exception as e:
        results.append(f"Disk check failed: {e}")

    # Memory
    try:
        r = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        if len(lines) > 1:
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            avail = int(parts[6]) if len(parts) > 6 else int(parts[3])
            results.append(f"Memory: {used}MB used / {total}MB ({avail}MB available)")
    except Exception as e:
        results.append(f"Memory check failed: {e}")

    # GPU (if available)
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                           "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            gpus = r.stdout.strip().split("\n")
            for gpu in gpus:
                results.append(f"GPU: {gpu}")
        else:
            results.append("GPU: No NVIDIA GPU detected")
    except FileNotFoundError:
        results.append("GPU: nvidia-smi not found")

    return "\n".join(results)


def diagnose(target="all", task_id=None) -> str:
    """Diagnose system problems. Check session health, gateway connectivity,
    memory providers, system resources, and recent errors.

    Args:
        target: Diagnosis target. One of: 'all', 'session', 'gateway',
                'resources', 'errors', 'memory'
    """
    valid_targets = ("all", "session", "gateway", "resources", "errors", "memory")
    if target not in valid_targets:
        return json.dumps({"error": f"Unknown target: {target}. Valid: {valid_targets}"})

    report = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report.append(f"== Hermes Self-Diagnosis ({now}) ==")

    if target in ("session", "all"):
        report.append("\n== Session Health ==")
        report.append(_check_session_files_health())

    if target in ("gateway", "all"):
        report.append("\n== Gateway Connectivity ==")
        report.append(_check_gateway_connectivity())

    if target in ("resources", "all"):
        report.append("\n== System Resources ==")
        report.append(_check_system_resources())

    if target in ("errors", "all"):
        report.append("\n== Recent Errors ==")
        report.append(_get_recent_errors(10))

    if target in ("memory", "all"):
        report.append("\n== Memory Providers ==")
        env_checks = {
            "Upstash": "UPSTASH_API_KEY",
            "Neo4j": "NEO4J_API_SECRET",
            "Pinecone": "PINECONE_API_KEY",
        }
        for provider, env_var in env_checks.items():
            status = "Configured" if os.getenv(env_var) else "Not configured"
            report.append(f"  {provider}: {status}")

    return "\n".join(report)


# Register the tool
registry.register(
    name="self_diagnose",
    toolset="devops",
    schema={
        "name": "self_diagnose",
        "description": (
            "Diagnose system problems. Check session file health, gateway connectivity, "
            "system resources, memory providers, and recent errors. "
            "Use target='session' to check session health, 'gateway' for connectivity, "
            "'resources' for disk/memory/GPU, 'errors' for recent failures, "
            "'memory' for vector memory provider status, or 'all' for everything. "
            "Call this first when encountering errors, anomalies, or when the user asks about system health."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Diagnosis target: 'all', 'session', 'gateway', 'resources', 'errors', 'memory'",
                    "enum": ["all", "session", "gateway", "resources", "errors", "memory"],
                }
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: diagnose(
        target=args.get("target", "all"),
        task_id=kw.get("task_id")
    ),
)
