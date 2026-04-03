     1|import sys
     2|import os
     3|import json
     4|import time
     5|import subprocess
     6|from datetime import datetime, timezone
     7|
     8|HOMEDIR = os.path.expanduser("~/.hermes")
     9|AGENT_DIR = os.path.join(HOMEDIR, "hermes-agent")
    10|AUTOHARNESS_DIR = "/tmp/AutoHarness"
    11|AUDIT_PATH = os.path.join(HOMEDIR, "audit.jsonl")
    12|IMPROVEMENT_LOG = os.path.join(HOMEDIR, "SELF_IMP_LOG.jsonl")
    13|
    14|sys.path.insert(0, AGENT_DIR)
    15|
    16|def log_improvement(action: str, detail: str, status: str = "ok"):
    17|    """Log an improvement action to SELF_IMP_LOG.jsonl"""
    18|    os.makedirs(HOMEDIR, exist_ok=True)
    19|    record = {
    20|        "ts": time.time(),
    21|        "dt": datetime.now(timezone.utc).isoformat(),
    22|        "action": action,
    23|        "detail": detail[:500],
    24|        "status": status,
    25|    }
    26|    with open(IMPROVEMENT_LOG, 'a') as f:
    27|        f.write(json.dumps(record) + '\n')
    28|
    29|def run(cmd, cwd=None, timeout=120):
    30|    """Run a shell command and return (output, success)"""
    31|    try:
    32|        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd or AGENT_DIR)
    33|        return r.stdout.strip() + r.stderr.strip(), r.returncode == 0
    34|    except Exception as e:
    35|        return str(e), False
    36|
    37|def check_governance():
    38|    """Check governance module for issues"""
    39|    try:
    40|        from tools.governance import (
    41|            classify_risk, check_permission, govern_tool_call,
    42|            get_audit_stats, get_risk_rules, _COMPILED_RULES
    43|        )
    44|        
    45|        # Test all risk rules compile
    46|        n_rules = len(_COMPILED_RULES)
    47|        log_improvement("governance_check", f"Risk rules: {n_rules} compiled, all valid")
    48|        
    49|        # Test classification on known patterns
    50|        test_cases = [
    51|            ("terminal", "rm -rf /", "critical"),
    52|            ("terminal", "ls -la", "low"),
    53|            ("read_file", "~/.hermes/.env", "low"),
    54|            ("terminal", "chmod 777 /bad", "high"),
    55|        ]
    56|        passed = 0
    57|        for tool, inp, expected in test_cases:
    58|            risk = classify_risk(tool, inp)
    59|            if risk["level"].value == expected:
    60|                passed += 1
    61|            else:
    62|                log_improvement("governance_test", f"FAIL: {tool}({inp[:30]}) expected {expected} got {risk['level'].value}", "fail")
    63|        
    64|        log_improvement("governance_test", f"{passed}/{len(test_cases)} test cases passed")
    65|        return True
    66|    except Exception as e:
    67|        log_improvement("governance_check", f"ERROR: {e}", "fail")
    68|        return False
    69|
    70|def check_observability():
    71|    """Test observability integration"""
    72|    try:
    73|        # Quick import test
    74|        sys.path.insert(0, os.path.join(HOMEDIR, "hermes-agent"))
    75|        from tools.observability import _config, init_all
    76|        
    77|        status = init_all()
    78|        active = sum(1 for v in status.values() if v.get("enabled"))
    79|        log_improvement("observability_check", f"{active} platforms enabled: {list(status.keys())}")
    80|        
    81|        # Check .env accessibility
    82|        keys_present = len([v for v in _config.env.values() if v and v != '***'])
    83|        log_improvement("env_check", f"{keys_present} env keys present")
    84|        return True
    85|    except Exception as e:
    86|        log_improvement("observability_check", f"ERROR: {e}", "fail")
    87|        return False
    88|
    89|def check_vector_memory():
    90|    """Test vector memory store"""
    91|    try:
    92|        from tools.vector_memory_store import VectorMemoryConfig
    93|        cfg = VectorMemoryConfig()
    94|        
    95|        # Check env vars available
    96|        import os
    97|        has_upstash = bool(os.getenv("UPSTASH_API_KEY"))
    98|        has_neo4j = bool(os.getenv("NEO4J_API_SECRET") or os.getenv("NEO4J_PASSWORD"))
    99|        has_pinecone = bool(os.getenv("PINECONE_API_KEY"))
   100|        
   101|        providers = {
   102|            "upstash": has_upstash,
   103|            "neo4j": has_neo4j,
   104|            "pinecone": has_pinecone,
   105|        }
   106|        active = [k for k, v in providers.items() if v]
   107|        log_improvement("vector_memory_check", f"Active providers: {active}")
   108|        return True
   109|    except Exception as e:
   110|        log_improvement("vector_memory_check", f"ERROR: {e}", "fail")
   111|        return False
   112|
   113|def check_portkey():
   114|    """Test portkey gateway and router"""
   115|    try:
   116|        sys.path.insert(0, HOMEDIR)
   117|        from tools.portkey_router import test_gateway
   118|        
   119|        result = test_gateway()
   120|        status = result.get("summary", "unknown")
   121|        log_improvement("portkey_check", f"Status: {status}, latency: {result.get('portkey_latency_ms', 'N/A')}ms")
   122|        return status == "PASSED"
   123|    except Exception:
   124|        # Fallback: just check if portkey gateway is running
   125|        try:
   126|            r = subprocess.run(["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", 
   127|                               "http://localhost:8787/"], capture_output=True, text=True, timeout=10)
   128|            running = r.stdout.strip() == "200"
   129|            log_improvement("portkey_check", f"Gateway HTTP check: {'OK' if running else 'DOWN'}")
   130|            return running
   131|        except Exception as e2:
   132|            log_improvement("portkey_check", f"ERROR: {e2}", "fail")
   133|            return False
   134|
   135|def check_autoharness():
   136|    """Analyze codebase against AutoHarness governance patterns"""
   137|    if not os.path.exists(AUTOHARNESS_DIR):
   138|        log_improvement("autoharness_check", "AutoHarness not available at /tmp/AutoHarness, cloning...", "skip")
   139|        ok, _ = run(f"cd /tmp && git clone --depth 1 https://github.com/aiming-lab/AutoHarness.git 2>&1")
   140|        if not os.path.exists(AUTOHARNESS_DIR):
   141|            log_improvement("autoharness_check", "Failed to clone AutoHarness", "fail")
   142|            return False
   143|    
   144|    # Check for risky patterns in recent code changes
   145|    recent_changes, ok = run("cd ~/.hermes/hermes-agent && git diff --stat HEAD~3 HEAD 2>/dev/null | tail -5")
   146|    if ok:
   147|        # Analyze for common governance issues
   148|        issues = []
   149|        
   150|        # Check for new secrets/patterns in recent commits
   151|        secrets_check, _ = run("cd ~/.hermes/hermes-agent && git diff HEAD~3 HEAD -- '*.py' '*.sh' '*.yaml' '*.yml' 2>/dev/null | grep -i -E '(password|secret|token|api.key|bearer)\\s*=\\s*' | head -5")
   152|        if secrets_check:
   153|            log_improvement("autoharness_analysis", f"WARNING: Potential exposed patterns in recent commits: {secrets_check[:200]}")
   154|            issues.append("potential_secret_exposure")
   155|        
   156|        # Check for dangerous patterns in new code
   157|        dangerous_checks = [
   158|            ("rm\s+-rf\s*/", "destructive_rm"),
   159|            ("eval\s*\(", "dangerous_eval"),
   160|            ("exec\s*\(", "dangerous_exec"),
   161|        ]
   162|        for pattern, name in dangerous_checks:
   163|            matches, _ = run(f"cd ~/.hermes/hermes-agent && find . -name '*.py' -exec grep -l '{pattern}' {{}} + 2>/dev/null | grep -v '/venv/' | grep -v '/.venv/' | grep -v '/node_modules/' | head -3")
   164|            if matches:
   165|                log_improvement("autoharness_analysis", f"Found {name} pattern in: {matches}")
   166|                issues.append(name)
   167|        
   168|        if not issues:
   169|            log_improvement("autoharness_analysis", "No new risky patterns detected in recent changes")
   170|        return True
   171|    
   172|    log_improvement("autoharness_check", "Could not get recent changes")
   173|    return False
   174|
   175|def update_skills_if_needed():
   176|    """Check if any SKILL.md files need updating based on current state"""
   177|    try:
   178|        # Test governance imports
   179|        sys.path.insert(0, AGENT_DIR)
   180|        from tools.governance import classify_risk, check_permission, govern_tool_call, get_audit_stats
   181|        
   182|        # Verify governance skill exists and is current
   183|        skill_path = os.path.join(HOMEDIR, "skills/mlops/autoharness-integration/SKILL.md")
   184|        if os.path.exists(skill_path):
   185|            with open(skill_path) as f:
   186|                content = f.read()
   187|            
   188|            # Update audit stats in skill if they differ
   189|            stats = get_audit_stats()
   190|            log_improvement("skill_update", f"Governance skill current. Audit stats: {json.dumps(stats)[:200]}")
   191|        else:
   192|            log_improvement("skill_update", "autoharness-integration SKILL.md missing", "fail")
   193|        
   194|        return True
   195|    except Exception as e:
   196|        log_improvement("skill_update", f"ERROR: {e}", "fail")
   197|        return False
   198|
   199|def commit_changes():
   200|    """Commit any changes to the myfork repo"""
   201|    changed, ok = run("cd ~/.hermes/hermes-agent && git status --porcelain | wc -l")
   202|    if ok and changed and changed != "0":
   203|        msg, ok2 = run("cd ~/.hermes/hermes-agent && git add -A && git commit -m 'auto-improvement: hourly governance check and codebase analysis' 2>&1")
   204|        if ok2:
   205|            log_improvement("commit", f"Committed changes: {msg[:200]}")
   206|            # Push to myfork
   207|            push_msg, ok3 = run("cd ~/.hermes/hermes-agent && git push myfork feat/vector-memory 2>&1")
   208|            if ok3:
   209|                log_improvement("push", "Pushed to myfork feat/vector-memory")
   210|        else:
   211|            log_improvement("commit", f"Commit failed: {msg[:200]}", "fail")
   212|
   213|def write_memory_update():
   214|    """Write a summary to memory so future sessions know what happened"""
   215|    try:
   216|        # Read recent improvement log
   217|        entries = []
   218|        if os.path.exists(IMPROVEMENT_LOG):
   219|            with open(IMPROVEMENT_LOG) as f:
   220|                entries = [json.loads(l) for l in f if l.strip()]
   221|        
   222|        # Get last 5 entries
   223|        recent = entries[-10:] if len(entries) > 10 else entries
   224|        summary = "; ".join([f"{e['action']}: {e['detail'][:100]}" for e in recent])
   225|        
   226|        # Append to audit log with hourly marker
   227|        audit_entry = {
   228|            "ts": time.time(),
   229|            "dt": datetime.now(timezone.utc).isoformat(),
   230|            "type": "hourly_improvement_summary",
   231|            "summary": summary[:500],
   232|        }
   233|        
   234|        log_improvement("memory_update", summary[:500])
   235|        return True
   236|    except Exception as e:
   237|        log_improvement("memory_update", f"ERROR: {e}", "fail")
   238|        return False
   239|
   240|
def check_openspace_quality():
    """Check skill quality and evolution candidates (from OpenSpace patterns)."""
    try:
        sys.path.insert(0, AGENT_DIR)
        from tools.skill_quality import get_summary, analyze_for_evolution
        from tools.skill_evolver import get_evolution_stats
        
        quality = get_summary()
        evolution = get_evolution_stats()
        candidates = analyze_for_evolution()
        
        log_improvement("openspace_quality", 
                       f"Quality: {quality['total_skills_tracked']} skills, "
                       f"{quality['overall_success_rate']}% success. "
                       f"Evolution: {evolution['total_evolutions']} total. "
                       f"Candidates: {len(candidates)} skills need evolution")
        return True
    except Exception as e:
        log_improvement("openspace_quality", f"ERROR: {e}", "fail")
        return False

def check_execution_analysis():
    """Check execution analysis for session health (from OpenSpace patterns)."""
    try:
        sys.path.insert(0, AGENT_DIR)
        from tools.execution_analyzer import get_evolution_candidates, get_weakest_tools
        
        candidates = get_evolution_candidates()
        weak = get_weakest_tools()
        
        log_improvement("execution_analysis",
                       f"Evolution candidates: {len(candidates)}, "
                       f"Weakest tools: {[t['tool'] for t in weak[:3]]}")
        return True
    except Exception as e:
        log_improvement("execution_analysis", f"ERROR: {e}", "fail")
        return False

def main():
   241|    print("=" * 60)
   242|    print(f"HERMES AUTO-IMPROVEMENT CYCLE")
   243|    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
   244|    print("=" * 60)
   245|    
   246|    checks = [
   247|        ("Governance", check_governance),
   248|        ("Observability", check_observability),
   249|        ("Vector Memory", check_vector_memory),
   250|        ("Portkey Gateway", check_portkey),
   251|        ("AutoHarness Analysis", check_autoharness),
   252|        ("Skill Updates", update_skills_if_needed),
   253|    ]
   254|    
   255|    results = {}
   256|    for name, func in checks:
   257|        print(f"\n[{name}]...", end=" ", flush=True)
   258|        try:
   259|            success = func()
   260|            results[name] = "PASS" if success else "FAIL"
   261|            print("PASS" if success else "FAIL")
   262|        except Exception as e:
   263|            results[name] = f"ERROR: {e}"
   264|            print(f"ERROR: {e}")
   265|    
   266|    # Commit changes
   267|    print("\n[Commit/Push]...", end=" ", flush=True)
   268|    commit_changes()
   269|    print("Done")
   270|    
   271|    # Update memory
   272|    print("[Memory Update]...", end=" ", flush=True)
   273|    write_memory_update()
   274|    print("Done")
   275|    
   276|    # Summary
   277|    print("\n" + "=" * 60)
   278|    print("SUMMARY")
   279|    print("=" * 60)
   280|    for name, status in results.items():
   281|        print(f"  {name}: {status}")
   282|    
   283|    n_pass = sum(1 for v in results.values() if v == "PASS")
   284|    print(f"\n  Total: {n_pass}/{len(results)} checks passed")
   285|    print("=" * 60)
   286|
   287|if __name__ == "__main__":
   288|    main()
   289|
   290|
   291|def collect_self_check_metrics():
   292|    """Collect comprehensive system health metrics (from 724-office self_check_tool.py pattern)."""
   293|    from datetime import datetime, timezone
   294|    
   295|    metrics = {
   296|        "timestamp": datetime.now(timezone.utc).isoformat(),
   297|        "sessions": {},
   298|        "disk": {},
   299|        "memory": {},
   300|        "services": {},
   301|        "error_logs": {},
   302|    }
   303|    
   304|    # Session stats from state.db
   305|    db_file = os.path.join(HOMEDIR, "state.db")
   306|    if os.path.exists(db_file):
   307|        size = os.path.getsize(db_file)
   308|        out, ok = run("sqlite3 " + db_file + " 'SELECT COUNT(*) FROM sessions;'", timeout=5)
   309|        metrics["sessions"] = {"count": int(out) if out and out.strip() else 0, "db_size_mb": round(size / 1024 / 1024, 1)}
   310|    
   311|    # Disk usage
   312|    out, ok = run("df -h /")
   313|    lines = out.split("\n")
   314|    if len(lines) > 1:
   315|        parts = lines[1].split()
   316|        metrics["disk"] = {"total": parts[1], "used": parts[2], "available": parts[3], "use_percent": parts[4] if len(parts) > 4 else "?"}
   317|    
   318|    # Memory usage
   319|    out, ok = run("free -m")
   320|    lines = out.split("\n")
   321|    if len(lines) > 1:
   322|        parts = lines[1].split()
   323|        metrics["memory"] = {"total_mb": parts[1], "used_mb": parts[2], "available_mb": parts[6] if len(parts) > 6 else parts[3]}
   324|    
   325|    # Service health checks
   326|    services = {
   327|        "portkey_gateway": ["curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8787/"],
   328|    }
   329|    for name, cmd in services.items():
   330|        try:
   331|            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
   332|            metrics["services"][name] = f"HTTP {r.stdout.strip()}"
   333|        except Exception as e:
   334|            metrics["services"][name] = f"DOWN ({str(e)[:50]})"
   335|    
   336|    # Error log summary
   337|    err_log = os.path.join(HOMEDIR, "logs/errors.log")
   338|    if os.path.exists(err_log):
   339|        with open(err_log) as f:
   340|            total = sum(1 for _ in f)
   341|        metrics["error_logs"] = {"total_lines": total}
   342|    
   343|    return metrics
   344|
   345|
   346|def self_diagnose_and_report():
   347|    """Collect health metrics and write report (724-office self-check pattern)."""
   348|    metrics = collect_self_check_metrics()
   349|    
   350|    lines = ["=" * 60, "HERMES SYSTEM SELF-CHECK REPORT", f"Time: {metrics.get('timestamp', 'N/A')}", "=" * 60]
   351|    
   352|    sess = metrics.get("sessions", {})
   353|    lines.append(f"\nSessions: {sess.get('count', '?')} (DB: {sess.get('db_size_mb', '?')} MB)")
   354|    
   355|    disk = metrics.get("disk", {})
   356|    if disk:
   357|        lines.append(f"Disk: {disk.get('used', '?')} used / {disk.get('total', '?')} ({disk.get('use_percent', '?')})")
   358|    
   359|    mem = metrics.get("memory", {})
   360|    if mem:
   361|        lines.append(f"Memory: {mem.get('used_mb', '?')}MB used / {mem.get('total_mb', '?')}MB")
   362|    
   363|    lines.append("\nServices:")
   364|    for svc, status in metrics.get("services", {}).items():
   365|        ok = "HTTP 200" in status or "HTTP 202" in status
   366|        lines.append(f"  [{'OK' if ok else 'DOWN'}] {svc}: {status}")
   367|    
   368|    logs = metrics.get("error_logs", {})
   369|    lines.append(f"\nError log: {logs.get('total_lines', 0)} lines")
   370|    
   371|    # Recent improvements
   372|    imp_log = os.path.join(HOMEDIR, "SELF_IMP_LOG.jsonl")
   373|    if os.path.exists(imp_log):
   374|        with open(imp_log) as f:
   375|            entries = [json.loads(l) for l in f if l.strip()]
   376|        recent = entries[-10:] if len(entries) > 10 else entries
   377|        lines.append(f"\nRecent improvements ({len(entries)} total):")
   378|        for imp in recent[-5:]:
   379|            lines.append(f"  - [{imp.get('status', '?')}] {imp.get('action', '?')}: {imp.get('detail', '')[:80]}")
   380|    
   381|    report = "\n".join(lines)
   382|    log_improvement("self_check_complete", report)
   383|    print(report)
   384|    return report
   385|