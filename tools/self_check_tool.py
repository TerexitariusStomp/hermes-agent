#!/usr/bin/env python3
"""
Self-Check Tool - System Health Monitoring
Adapted from 724-Office (wangziqi06/724-office, MIT License)
"""

import os
import json
import subprocess
import time
from datetime import datetime, timezone

HERMES_HOME = os.path.expanduser("~/.hermes")

def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode == 0
    except:
        return "", False

def collect_health_metrics():
    """Collect comprehensive system health metrics."""
    metrics = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sessions": {},
        "disk": {},
        "memory": {},
        "services": {},
        "error_logs": {},
        "scheduled_tasks": [],
        "recent_improvements": [],
    }
    
    # 1. Session stats from state.db
    db_file = os.path.join(HERMES_HOME, "state.db")
    if os.path.exists(db_file):
        size = os.path.getsize(db_file)
        out, ok = run_cmd(f"sqlite3 {db_file} 'SELECT COUNT(*) FROM sessions;'", timeout=5)
        metrics["sessions"] = {"count": int(out) if out and out.isdigit() else 0, "db_size_mb": round(size / 1024 / 1024, 1)}
    
    # 2. Disk usage
    out, ok = run_cmd("df -h /")
    lines = out.split("\n")
    if len(lines) > 1:
        parts = lines[1].split()
        metrics["disk"] = {"total": parts[1], "used": parts[2], "available": parts[3], "use_percent": parts[4] if len(parts) > 4 else "?"}
    
    # 3. Memory usage
    out, ok = run_cmd("free -m")
    lines = out.split("\n")
    if len(lines) > 1:
        parts = lines[1].split()
        metrics["memory"] = {"total_mb": parts[1], "used_mb": parts[2], "available_mb": parts[6] if len(parts) > 6 else parts[3]}
    
    # 4. Service health checks
    services = {
        "portkey_gateway": ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8787/"],
    }
    for name, cmd in services.items():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            metrics["services"][name] = f"HTTP {r.stdout.strip()}"
        except Exception as e:
            metrics["services"][name] = f"DOWN"
    
    # 5. Error log summary
    err_log = os.path.join(HERMES_HOME, "logs/errors.log")
    if os.path.exists(err_log):
        try:
            with open(err_log) as f:
                total = sum(1 for _ in f)
            metrics["error_logs"] = {"file": err_log, "total_lines": total}
        except:
            metrics["error_logs"] = {"file": err_log, "total_lines": 0, "error": "could not read"}
    
    # 6. Recent improvements from SELF_IMP_LOG
    imp_log = os.path.join(HERMES_HOME, "SELF_IMP_LOG.jsonl")
    if os.path.exists(imp_log):
        try:
            with open(imp_log) as f:
                entries = [json.loads(l) for l in f if l.strip()]
            metrics["recent_improvements"] = entries[-10:] if len(entries) > 10 else entries
        except:
            metrics["recent_improvements"] = []
    
    return metrics

def format_report(metrics=None):
    """Format health metrics into a readable report."""
    if not metrics:
        metrics = collect_health_metrics()
    
    lines = ["=" * 60, "HERMES SYSTEM SELF-CHECK REPORT", f"Time: {metrics.get('timestamp', 'N/A')}", "=" * 60]
    
    sess = metrics.get("sessions", {})
    lines.append(f"\nSessions: {sess.get('count', '?')} (DB: {sess.get('db_size_mb', '?')} MB)")
    
    disk = metrics.get("disk", {})
    if disk:
        lines.append(f"Disk: {disk.get('used', '?')} used / {disk.get('total', '?')} ({disk.get('use_percent', '?')})")
    
    mem = metrics.get("memory", {})
    if mem:
        lines.append(f"Memory: {mem.get('used_mb', '?')}MB used / {mem.get('total_mb', '?')}MB")
    
    lines.append("\nServices:")
    for svc, status in metrics.get("services", {}).items():
        ok = "HTTP 200" in status or "HTTP 202" in status
        lines.append(f"  [{'OK' if ok else 'DOWN'}] {svc}: {status}")
    
    logs = metrics.get("error_logs", {})
    lines.append(f"\nError log: {logs.get('total_lines', 0)} lines in {os.path.basename(logs.get('file', ''))}")
    
    improvements = metrics.get("recent_improvements", [])
    if improvements:
        lines.append(f"\nRecent improvements ({len(improvements)}):")
        for imp in improvements[-5:]:
            lines.append(f"  - [{imp.get('status', '?')}] {imp.get('action', '?')}: {imp.get('detail', '')[:80]}")
    
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)

if __name__ == "__main__":
    metrics = collect_health_metrics()
    print(format_report(metrics))
