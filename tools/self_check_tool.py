#!/usr/bin/env python3
"""
Self-Check Tool - System Health Monitoring
Adapted from 724-Office (wangziqi06/724-office, MIT License)
"""

import os
import json
import subprocess
import time
import sqlite3
from datetime import datetime, timezone

from tools.registry import registry

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
    
    # 1. Session stats from state.db (Python sqlite3, not CLI -- sqlite3 binary often missing)
    db_file = os.path.join(HERMES_HOME, "state.db")
    if os.path.exists(db_file):
        size = os.path.getsize(db_file)
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions")
            count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            msg_count = cur.fetchone()[0]
            
            # Recent session activity (last 24h)
            from datetime import timedelta
            cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
            cur.execute("SELECT COUNT(*) FROM sessions WHERE started_at IS NOT NULL AND started_at > ?", (cutoff_ts,))
            recent = cur.fetchone()[0]
            conn.close()
            
            metrics["sessions"] = {
                "count": count,
                "message_count": msg_count,
                "db_size_mb": round(size / 1024 / 1024, 1),
                "recent_24h": recent,
            }
        except Exception as e:
            metrics["sessions"] = {"count": 0, "error": str(e), "db_size_mb": round(size / 1024 / 1024, 1)}
    
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
        "openrouter": ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "https://openrouter.ai/api/v1/models"],
    }
    for name, cmd in services.items():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            status_code = r.stdout.strip()
            ok = status_code in ("200", "201", "202", "401")  # 401 means reachable but needs auth
            metrics["services"][name] = f"HTTP {status_code} ({'OK' if ok else 'DOWN'})"
        except Exception:
            metrics["services"][name] = "DOWN"
    
    # GPU status (enhanced -- nvidia-smi detection)
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                           "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            gpu_lines = r.stdout.strip().split('\n')
            gpu_info_list = []
            for line in gpu_lines:
                parts = [p.strip() for p in line.split(',')]
                gpu_info_list.append({
                    "name": parts[0] if len(parts) > 0 else "unknown",
                    "mem_used": parts[1] if len(parts) > 1 else "?",
                    "mem_total": parts[2] if len(parts) > 2 else "?",
                    "util_gpu": parts[3] if len(parts) > 3 else "?",
                })
            metrics["gpu"] = {"detected": True, "info": r.stdout.strip(), "gpus": gpu_info_list}
        else:
            metrics["gpu"] = {"detected": False, "info": "No GPU available"}
    except FileNotFoundError:
        metrics["gpu"] = {"detected": False, "info": "nvidia-smi not found"}
    except Exception as e:
        metrics["gpu"] = {"detected": False, "info": f"nvidia-smi failed: {e}"}
    
    # 6. Memory file status
    memory_md = os.path.join(HERMES_HOME, "memories", "MEMORY.md")
    if os.path.exists(memory_md):
        size = os.path.getsize(memory_md)
        mtime = os.path.getmtime(memory_md)
        age_hours = (time.time() - mtime) / 3600
        metrics["memory_file"] = {
            "exists": True,
            "size_bytes": size,
            "size_kb": round(size / 1024, 1),
            "age_hours": round(age_hours, 1),
            "warning": "Near capacity" if size > 2000 else None,
        }
    else:
        metrics["memory_file"] = {"exists": False}
    
    # 7. Error log summary
    err_log = os.path.join(HERMES_HOME, "logs/errors.log")
    if os.path.exists(err_log):
        try:
            with open(err_log) as f:
                total = sum(1 for _ in f)
            metrics["error_logs"] = {"file": err_log, "total_lines": total}
        except:
            metrics["error_logs"] = {"file": err_log, "total_lines": 0, "error": "could not read"}
    
    # 8. Recent improvements from SELF_IMP_LOG
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
    
    # Sessions
    sess = metrics.get("sessions", {})
    sess_parts = []
    if sess:
        sess_parts.append(f"{sess.get('count', '?')} sessions")
        if "message_count" in sess:
            sess_parts.append(f"{sess['message_count']} messages")
        if "recent_24h" in sess:
            sess_parts.append(f"{sess['recent_24h']} active in 24h")
        sess_parts.append(f"DB: {sess.get('db_size_mb', '?')} MB")
    lines.append(f"\nSessions: {', '.join(sess_parts) if sess_parts else 'unknown'}")
    if "error" in sess:
        lines.append(f"  ⚠ Session DB error: {sess['error']}")
    
    # Disk
    disk = metrics.get("disk", {})
    if disk:
        lines.append(f"Disk: {disk.get('used', '?')} used / {disk.get('total', '?')} ({disk.get('use_percent', '?')})")
        pct = disk.get("use_percent", "0%").replace("%", "")
        if pct.isdigit() and int(pct) > 80:
            lines.append("  ⚠ WARNING: Disk usage above 80%")
    
    # Memory
    mem = metrics.get("memory", {})
    if mem:
        lines.append(f"Memory: {mem.get('used_mb', '?')}MB used / {mem.get('total_mb', '?')}MB (available: {mem.get('available_mb', '?')}MB)")
    
    # GPU
    gpu = metrics.get("gpu", {})
    if gpu:
        if gpu.get("detected"):
            lines.append(f"GPU: {gpu.get('info', 'detected')}")
        else:
            lines.append(f"GPU: Not available ({gpu.get('info', 'unknown')})")
    
    # Services
    lines.append("\nServices:")
    for svc, status in metrics.get("services", {}).items():
        ok = "OK" in status
        lines.append(f"  [{'OK' if ok else 'DOWN'}] {svc}: {status}")
    
    # Memory file
    mf = metrics.get("memory_file", {})
    if mf.get("exists"):
        warning = f" ⚠ {mf['warning']}" if mf.get("warning") else ""
        lines.append(f"\nMemory file: {mf.get('size_kb', '?')}KB, {mf.get('age_hours', '?')}h old{warning}")
    else:
        lines.append("\nMemory file: not found")
    
    # Error log
    logs = metrics.get("error_logs", {})
    lines.append(f"\nError log: {logs.get('total_lines', 0)} lines in {os.path.basename(logs.get('file', ''))}")
    
    # Recent improvements
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


registry.register(
    name="self_check",
    toolset="diagnostics",
    schema={
        "name": "self_check",
        "description": "System self-check: collect conversation stats, system health (disk, memory), "
                       "service status, error logs, and recent improvement history. "
                       "Returns a comprehensive health report in text format.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=lambda args, **kw: json.dumps(collect_health_metrics(), indent=2),
)

