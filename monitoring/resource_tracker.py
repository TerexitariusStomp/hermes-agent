#!/usr/bin/env python3
"""
Dual-system resource tracker.
Monitors local (terex) and remote (srv686563) systems continuously.
Logs all metrics to /home/terexitarius/.hermes/hermes-agent/monitoring/logs/
"""

import subprocess
import json
import sys
import time
import os
import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("/home/terexitarius/.hermes/hermes-agent/monitoring/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

REMOTE_HOST = "147.79.71.192"
POLL_INTERVAL = 60  # seconds

logger = logging.getLogger("resource_tracker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def run_local(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    return result.stdout.strip()

REMOTE_UNREACHABLE_COUNT = 0
MAX_REMOTE_RETRIES_BEFORE_QUIET = 5  # After this many consecutive failures, stop spamming logs

def run_remote(cmd: str) -> str:
    global REMOTE_UNREACHABLE_COUNT
    try:
        result = subprocess.run(
            f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o ServerAliveInterval=5 root@{REMOTE_HOST} '{cmd}'",
            shell=True, capture_output=True, text=True, timeout=15
        )
        REMOTE_UNREACHABLE_COUNT = 0  # Reset on success
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        REMOTE_UNREACHABLE_COUNT += 1
        if REMOTE_UNREACHABLE_COUNT <= MAX_REMOTE_RETRIES_BEFORE_QUIET:
            logger.warning(f"Remote host {REMOTE_HOST} unreachable (attempt {REMOTE_UNREACHABLE_COUNT})")
        return "REMOTE_UNREACHABLE"
    except Exception as e:
        REMOTE_UNREACHABLE_COUNT += 1
        if REMOTE_UNREACHABLE_COUNT <= MAX_REMOTE_RETRIES_BEFORE_QUIET:
            logger.warning(f"Remote host {REMOTE_HOST} error: {e}")
        return "REMOTE_UNREACHABLE"

def get_disk_breakdown(host_type: str, data: str) -> list:
    """Parse df output into structured disk data."""
    disks = []
    for line in data.split('\n'):
        if line.startswith('Filesystem') or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 6:
            disks.append({
                'filesystem': parts[0],
                'size': parts[1],
                'used': parts[2],
                'available': parts[3],
                'use_pct': parts[4],
                'mounted_on': parts[5]
            })
    return disks

def get_top_processes(host_type: str) -> list:
    """Get top 5 processes by memory usage."""
    try:
        if host_type == "local":
            raw = run_local("ps aux --sort=-%mem | head -6")
        else:
            raw = run_remote("ps aux --sort=-%mem | head -6")
    except Exception as e:
        # Remote host may be unreachable (SSH timeout, host down) — don't crash metrics
        return [{'error': str(e)}]
    
    processes = []
    for line in raw.split('\n')[1:]:
        if not line.strip(): continue
        parts = line.split(None, 10)
        if len(parts) >= 11:
            processes.append({
                'user': parts[0],
                'pid': parts[1],
                'cpu': parts[2],
                'mem': parts[3],
                'command': parts[10][:100]
            })
    return processes

def collect_system_metrics(host_type: str, runner) -> dict:
    """Collect comprehensive system metrics."""
    timestamp = datetime.now().isoformat()
    
    if host_type == "local":
        hostname = run_local("hostname")
        uptime = run_local("uptime")
    else:
        hostname = run_remote("hostname")
        uptime = run_remote("uptime")
    
    # CPU
    if host_type == "local":
        cpu_raw = run_local("lscpu | grep -E '^(Architecture|Model name|CPU\\(s\\)|Thread|Core|Socket)'")
        gpu_info = run_local("nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'No NVIDIA GPU'")
        docker_info = run_local("docker info 2>/dev/null | grep -E '^(Server|Containers|Images)' || echo 'Docker not running'")
        load_avg = run_local("cat /proc/loadavg")
    else:
        cpu_raw = run_remote("lscpu | grep -E '^(Architecture|Model name|CPU\\(s\\)|Thread|Core|Socket)'")
        gpu_info = run_remote("nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'No NVIDIA GPU'")
        docker_info = run_remote("docker info 2>/dev/null | grep -E '^(Server|Containers|Images)' || echo 'Docker not running'")
        load_avg = run_remote("cat /proc/loadavg")
    
    # Memory
    if host_type == "local":
        mem_raw = run_local("free -m")
        disk_raw = run_local("df -h 2>/dev/null | grep -E '^(/dev|Filesystem)'")
    else:
        mem_raw = run_remote("free -m")
        disk_raw = run_remote("df -h 2>/dev/null | grep -E '^(/dev|Filesystem)'")
    
    # Parse memory
    mem_info = {}
    for line in mem_raw.split('\n'):
        if line.startswith('Mem:'):
            parts = line.split()
            mem_info = {
                'total_mb': int(parts[1]),
                'used_mb': int(parts[2]),
                'free_mb': int(parts[3]),
                'available_mb': int(parts[6])
            }
            mem_info['used_pct'] = round(mem_info['used_mb'] / mem_info['total_mb'] * 100, 1) if mem_info['total_mb'] > 0 else 0
        elif line.startswith('Swap:'):
            parts = line.split()
            mem_info['swap_total_mb'] = int(parts[1])
            mem_info['swap_used_mb'] = int(parts[2])
    
    # Parse disks
    disks = get_disk_breakdown(host_type, disk_raw)
    
    # Parse CPU
    cpu_info = {'raw': cpu_raw}
    for line in cpu_raw.split('\n'):
        if 'Model name' in line:
            cpu_info['model'] = line.split(':', 1)[1].strip()
        elif 'CPU(s):' in line and 'scaling' not in line:
            cpu_info['cores'] = int(line.split(':', 1)[1].strip())
    
    # GPU
    gpu_info_parsed = "No GPU" if "No NVIDIA GPU" in gpu_info else gpu_info
    
    # Docker
    docker_parsed = {
        'running': 'Docker not running' not in docker_info,
        'info': docker_info
    }
    
    # Load average
    load_parts = load_avg.split()
    load_info = {
        '1min': load_parts[0],
        '5min': load_parts[1],
        '15min': load_parts[2],
        'running_processes': load_parts[3]
    }
    
    # Top processes
    top_processes = get_top_processes(host_type)
    
    return {
        'timestamp': timestamp,
        'hostname': hostname,
        'uptime': uptime,
        'cpu': cpu_info,
        'memory': mem_info,
        'disks': disks,
        'gpu': gpu_info_parsed,
        'docker': docker_parsed,
        'load_average': load_info,
        'top_processes': top_processes
    }

def save_metrics(metrics: dict, host_type: str):
    """Save metrics to log file."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"{host_type}_{today}.jsonl"
    with open(log_file, 'a') as f:
        f.write(json.dumps(metrics) + '\n')

def get_latest_metrics():
    """Get the most recent metrics from both systems."""
    latest = {'local': None, 'remote': None}
    for host_type in ['local', 'remote']:
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = LOG_DIR / f"{host_type}_{today}.jsonl"
        if log_file.exists():
            with open(log_file, 'r') as f:
                lines = f.readlines()
                if lines:
                    latest[host_type] = json.loads(lines[-1])
    return latest

def print_summary(metrics, host_type):
    """Print a human-readable summary of metrics."""
    if not metrics:
        print(f"[{host_type.upper()}] No metrics available")
        return
    
    print(f"\n{'='*60}")
    print(f"[{host_type.upper()}] {metrics['hostname']} - {metrics['timestamp']}")
    print(f"{'Uptime':15}: {metrics['uptime']}")
    print(f"{'CPU':15}: {metrics['cpu'].get('model', 'Unknown')}")
    print(f"{'Memory':15}: {metrics['memory'].get('used_mb', 0)}MB / {metrics['memory'].get('total_mb', 0)}MB ({metrics['memory'].get('used_pct', 0)}%)")
    print(f"{'GPU':15}: {metrics['gpu'][:100] if isinstance(metrics['gpu'], str) else metrics['gpu']}")
    print(f"{'Load':15}: {metrics['load_average']['1min']}, {metrics['load_average']['5min']}, {metrics['load_average']['15min']}")
    print(f"{'Docker':15}: {'Running' if metrics['docker']['running'] else 'Not running'}")
    for disk in metrics.get('disks', []):
        print(f"  Disk {disk['mounted_on']}: {disk['used']}/{disk['size']} ({disk['use_pct']})")

def continuous_monitor():
    """Main monitoring loop."""
    logger.info("Starting resource monitoring...")
    logger.info(f"Local system: terex")
    logger.info(f"Remote system: srv686563 (147.79.71.192)")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")
    
    while True:
        try:
            for host_type in ['local', 'remote']:
                runner = run_local if host_type == 'local' else run_remote
                metrics = collect_system_metrics(host_type, runner)
                save_metrics(metrics, host_type)
                
                # Print summary
                print_summary(metrics, host_type)
                
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    if "--once" in sys.argv:
        # One-time collection
        for host_type in ['local', 'remote']:
            runner = run_local if host_type == 'local' else run_remote
            metrics = collect_system_metrics(host_type, runner)
            save_metrics(metrics, host_type)
            print_summary(metrics, host_type)
    else:
        import sys
        continuous_monitor()
