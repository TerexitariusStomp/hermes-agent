#!/usr/bin/env python3
"""
Diagnostic Tool - Session File & System Health Diagnostics
Adapted from 724-Office (wangziqi06/724-office, MIT License)
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone

from tools.registry import registry

HERMES_HOME = os.path.expanduser("~/.hermes")
STATE_DB = os.path.join(HERMES_HOME, "state.db")


def diagnose(target="all", task_id=None):
    """Diagnose system problems. Check session file health, memory status,
    recent error log details. Call this first when encountering issues.
    target: 'sessions', 'memory', 'errors', 'all'
    """
    report = []

    if target in ("sessions", "all"):
        report.append("== Session File Health Check ==")
        if os.path.exists(STATE_DB):
            try:
                conn = sqlite3.connect(STATE_DB)
                sessions = conn.execute(
                    "SELECT session_id, platform FROM sessions ORDER BY session_id"
                ).fetchall()

                for sid, platform in sessions:
                    messages = conn.execute(
                        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY rowid",
                        (sid,),
                    ).fetchall()

                    if not messages:
                        continue

                    issues = []
                    # Check for orphan tool messages at start
                    if messages[0][0] == "tool":
                        issues.append("Starts with orphan tool message (causes LLM 400)")

                    # Check for assistant with tool_calls but missing results
                    if messages[0][0] == "assistant":
                        content = messages[0][1]
                        try:
                            parsed = json.loads(content)
                            if parsed.get("tool_calls"):
                                issues.append(
                                    "Starts with assistant+tool_calls (missing results, causes 400)"
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # Count orphan tool messages
                    tc_ids = set()
                    for role, content in messages:
                        if role == "assistant":
                            try:
                                parsed = json.loads(content)
                                for tc in parsed.get("tool_calls", []):
                                    tc_ids.add(tc.get("id", ""))
                            except (json.JSONDecodeError, TypeError):
                                pass

                    orphan_tools = sum(
                        1
                        for role, content in messages
                        if role == "tool" and content not in tc_ids
                    )
                    if orphan_tools:
                        issues.append(
                            f"{orphan_tools} tool messages with no matching tool_call_id"
                        )

                    total_bytes = sum(len(json.dumps(m)) for m in messages)
                    status = f"ISSUES: {'; '.join(issues)}" if issues else "OK"
                    roles = {}
                    for role, _ in messages:
                        roles[role] = roles.get(role, 0) + 1
                    report.append(
                        f"  [{platform}] {sid}: {len(messages)} msgs ({roles}), "
                        f"{total_bytes} bytes, {status}"
                    )

                conn.close()
            except Exception as e:
                report.append(f"  DB check failed: {e}")
        else:
            report.append("  state.db not found")

    if target in ("memory", "all"):
        report.append("\n== Memory Status ==")
        memory_md = os.path.join(HERMES_HOME, "memories", "MEMORY.md")
        if os.path.exists(memory_md):
            size = os.path.getsize(memory_md)
            mtime = datetime.fromtimestamp(
                os.path.getmtime(memory_md), timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            report.append(f"  MEMORY.md: {size} bytes ({size/1024:.1f}KB), updated {mtime}")
        else:
            report.append("  MEMORY.md: not found")

        # Check memory_db for compressed memories
        mem_db = os.path.join(HERMES_HOME, "memory_db", "compressed_memories.json")
        if os.path.exists(mem_db):
            try:
                with open(mem_db) as f:
                    data = json.load(f)
                report.append(f"  Compressed memories: {len(data)} entries")
            except Exception:
                report.append("  Compressed memories: read failed")

    if target in ("errors", "all"):
        report.append("\n== Recent Error Details ==")
        err_log = os.path.join(HERMES_HOME, "logs", "errors.log")
        if os.path.exists(err_log):
            try:
                result = subprocess.run(
                    ["bash", "-c", f'tail -20 {err_log} | grep -i "error\\|exception\\|400" | tail -10'],
                    capture_output=True, text=True, timeout=10,
                )
                if result.stdout.strip():
                    report.append(result.stdout.strip())
                else:
                    report.append("  No recent errors in log")
            except Exception as e:
                report.append(f"  Read failed: {e}")
        else:
            report.append("  Error log not found")

    if target in ("services", "all"):
        report.append("\n== Service Health ==")
        # Portkey Gateway
        try:
            r = subprocess.run(
                ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8787/"],
                capture_output=True, text=True, timeout=10,
            )
            status = "OK" if "200" in r.stdout else "DOWN"
            report.append(f"  Portkey Gateway: {status} (HTTP {r.stdout.strip()})")
        except Exception:
            report.append("  Portkey Gateway: DOWN (connection failed)")

        # Disk and memory
        try:
            df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5).stdout.strip().split("\n")
            if len(df) > 1:
                report.append(f"  Disk: {df[1]}")
            mem = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5).stdout.strip().split("\n")
            if len(mem) > 1:
                report.append(f"  Memory: {mem[1]}")
        except Exception as e:
            report.append(f"  System stats: {e}")

    return "\n".join(report)


registry.register(
    name="diagnose",
    toolset="diagnostics",
    schema={
        "name": "diagnose",
        "description": "Diagnose system problems. Check session file health, memory status, "
                       "recent error log details. Use when encountering issues - detects "
                       "orphan tool messages, bad session starts, and system resource problems.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Diagnosis target: 'sessions' (check for 400-causing issues), "
                                   "'memory' (state and size), 'errors' (recent log entries), "
                                   "'services' (gateway and resources), 'all' (everything)",
                    "enum": ["sessions", "memory", "errors", "services", "all"],
                },
            },
        },
    },
    handler=lambda args, **kw: diagnose(target=args.get("target", "all"), task_id=kw.get("task_id")),
)
