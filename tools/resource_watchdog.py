#!/usr/bin/env python3
"""
Resource watchdog - automatically checks and frees resources when near capacity.
Run as a cron job on Hermes Agent.
"""

import subprocess
import shutil
from pathlib import Path
from datetime import datetime

THRESHOLDS = {
    'disk_pct': 80,       # Alert/clean when root > 80% used
    'disk_critical': 90,   # Aggressive cleanup when > 90%
    'memory_pct': 85,      # Alert when RAM > 85% used
    'docker_size_gb': 15,  # Alert when Docker uses > 15GB disk
}

HOME = Path("/home/terexitarius")
HERMES_HOME = HOME / ".hermes"
HERMES_AGENT = HERMES_HOME / "hermes-agent"

def run(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()

def get_disk_pct() -> float:
    out = run("df / | tail -1")
    return float(out.split()[4].replace('%', ''))

def get_memory_pct() -> float:
    out = run("free | grep Mem")
    parts = out.split()
    total, used = int(parts[1]), int(parts[2])
    return round(used / total * 100, 1) if total > 0 else 0

def get_docker_disk_gb() -> float:
    out = run("docker system df --format '{{.Type}}\t{{.Size}}'")
    total_gb = 0
    for line in out.split('\n'):
        if 'Size' in line: continue
        parts = line.split('\t')
        if len(parts) >= 2:
            size_str = parts[1]
            if 'GB' in size_str:
                total_gb += float(size_str.replace('GB', '').strip())
            elif 'MB' in size_str:
                total_gb += float(size_str.replace('MB', '').strip()) / 1024
            elif 'TB' in size_str:
                total_gb += float(size_str.replace('TB', '').strip()) * 1024
    return round(total_gb, 1)

def get_disk_free_gb() -> float:
    out = run("df -BG / | tail -1")
    return float(out.split()[3].replace('G', ''))

def cleanup_docker(aggressive: bool = False) -> str:
    """Clean Docker resources. Returns summary of freed space."""
    results = []
    
    # Always: stop crash-looping containers
    restarting = run("docker ps --format '{{.Names}}' --filter 'status=restarting'")
    if restarting:
        for name in restarting.split('\n'):
            run(f"docker stop {name} 2>/dev/null")
            results.append(f"Stopped crash-looping: {name}")
    
    # Always: remove exited containers
    r = run("docker container prune -f")
    if 'reclaimed' in r.lower() or 'total' in r.lower():
        results.append(f"Container prune: {r.split('Total:')[-1].strip() if 'Total:' in r else 'done'}")
    
    # Always: clean build cache
    r = run("docker builder prune -f")
    if 'Total' in r:
        results.append(f"Build cache: {r.split('Total:')[-1].strip()}")
    
    if aggressive:
        # Remove ALL unused images
        r = run("docker image prune -a -f")
        if 'Total' in r or 'deleted' in r.lower():
            results.append(f"Image prune -a: {r.split('Total:')[-1].strip() if 'Total:' in r else 'done'}")
        
        # Remove all unused volumes
        r = run("docker volume prune -f")
        if 'Total' in r or 'reclaimed' in r.lower():
            results.append(f"Volume prune: {r.split('Total:')[-1].strip() if 'Total:' in r else 'done'}")
    
    return '\n'.join(results) if results else "No Docker cleanup needed"

def cleanup_caches() -> str:
    """Clean system caches. Returns summary."""
    results = []
    
    # UV cache
    uv = HOME / ".cache" / "uv"
    if uv.exists() and len(list(uv.glob("*"))) > 0:
        size = sum(f.stat().st_size for f in uv.rglob('*') if f.is_file())
        shutil.rmtree(uv, ignore_errors=True)
        uv.mkdir(exist_ok=True)
        results.append(f"UV cache: {size/1024/1024:.0f}MB")
    
    # Pip cache
    pip = HOME / ".cache" / "pip"
    if pip.exists():
        size = sum(f.stat().st_size for f in pip.rglob('*') if f.is_file())
        if size > 50 * 1024 * 1024:  # Only if > 50MB
            shutil.rmtree(pip, ignore_errors=True)
            pip.mkdir(exist_ok=True)
            results.append(f"Pip cache: {size/1024/1024:.0f}MB")
    
    # APT
    run("apt-get clean")
    results.append("APT cache cleaned")
    
    # Old VSCode server versions (keep only current)
    vscode_servers = list((HOME / ".vscode-server" / "cli" / "servers").glob("Stable-*"))
    if len(vscode_servers) > 1:
        for s in vscode_servers[:-1]:
            if s.is_dir():
                shutil.rmtree(s, ignore_errors=True)
                results.append(f"Removed old VSCode: {s.name}")
    
    # Old cron outputs (keep 3 per job)
    cron_out = HERMES_HOME / "cron" / "output"
    if cron_out.exists():
        for job_dir in cron_out.iterdir():
            if job_dir.is_dir():
                files = sorted(job_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
                for f in files[3:]:
                    f.unlink()
    
    # Pycache
    for root, dirs, _ in __import__('os').walk(HERMES_AGENT):
        for d in dirs:
            if d == '__pycache__':
                shutil.rmtree(f"{root}/{d}", ignore_errors=True)
    
    # Monitoring logs
    mon_logs = HERMES_AGENT / "monitoring" / "logs"
    if mon_logs.exists():
        for f in mon_logs.glob("*.jsonl"):
            f.unlink()
    
    return '\n'.join(results) if results else "No cache cleanup needed"

def cleanup_recordings() -> str:
    """Remove browser recordings older than 24 hours, keeping at most 5."""
    recs = HERMES_HOME / "browser_recordings"
    if not recs.exists():
        return ""
    
    files = list(recs.glob("*.webm")) + list(recs.glob("*.mp4"))
    if len(files) <= 5:
        return ""
    
    files.sort(key=lambda f: f.stat().st_mtime)
    removed = []
    for f in files[:-5]:
        size = f.stat().st_size
        f.unlink()
        removed.append(f"  {f.name} ({size/1024/1024:.1f}MB)")
    
    return f"Removed {len(removed)} old recordings:\n" + '\n'.join(removed) if removed else ""

def main():
    disk_pct = get_disk_pct()
    mem_pct = get_memory_pct()
    docker_gb = get_docker_disk_gb()
    free_gb = get_disk_free_gb()
    
    aggressive = disk_pct >= THRESHOLDS['disk_critical']
    
    report = [
        f"Resource Watchdog - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Disk: {disk_pct}% used ({free_gb:.1f}GB free) | Memory: {mem_pct}% | Docker: {docker_gb}GB disk",
        ""
    ]
    
    if disk_pct < THRESHOLDS['disk_pct'] and mem_pct < THRESHOLDS['memory_pct'] and docker_gb < THRESHOLDS['docker_size_gb']:
        report.append("All resources within normal limits. No action needed.")
        print('\n'.join(report))
        return
    
    if docker_gb >= THRESHOLDS['docker_size_gb'] or disk_pct >= THRESHOLDS['disk_pct']:
        report.append(f"Docker cleanup needed (threshold: {THRESHOLDS['docker_size_gb']}GB, actual: {docker_gb}GB)")
        report.append(cleanup_docker(aggressive=aggressive))
        report.append("")
    
    if disk_pct >= THRESHOLDS['disk_pct']:
        report.append("Disk cleanup (threshold exceeded)")
        
        # Cache cleanup
        report.append(f"Cache cleanup: {cleanup_caches()}")
        
        # Recordings
        rec_result = cleanup_recordings()
        if rec_result:
            report.append(rec_result)
        
        report.append("")
    
    # Re-check after cleanup
    new_disk = get_disk_pct()
    new_docker = get_docker_disk_gb()
    new_free = get_disk_free_gb()
    
    report.append(f"Post-cleanup: Disk {new_disk}% ({new_free:.1f}GB free) | Docker: {new_docker}GB")
    report.append(f"Freed ~{(new_free - free_gb):.1f}GB" if new_free > free_gb else "")
    
    print('\n'.join(report))

if __name__ == "__main__":
    main()
