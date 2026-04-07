#!/usr/bin/env python3
"""
Hermes ecosystem orchestration and update manager.

Manages external agent-framework integrations through a declarative manifest.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "ecosystem-modules.yaml"


@dataclass
class ModuleSpec:
    name: str
    kind: str
    enabled: bool
    path: Path | None
    remote: str
    branch: str
    update_commands: list[str]
    check_commands: list[str]
    doctor_commands: list[str]
    backup_paths: list[Path]
    notes: str
    extension_module: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run(
    cmd: str,
    cwd: Path | None = None,
    check: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            shlex.split(cmd),
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout="",
            stderr=str(e),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
        )


def _config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_path = os.getenv("HERMES_ECOSYSTEM_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()
    hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    user_cfg = hermes_home / "ecosystem" / "modules.yaml"
    if user_cfg.exists():
        return user_cfg
    return DEFAULT_CONFIG_PATH


def _load_modules(config_path: Path) -> list[ModuleSpec]:
    if not config_path.exists():
        raise FileNotFoundError(f"Ecosystem config not found: {config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    modules = []
    for raw in data.get("modules", []):
        root = PROJECT_ROOT
        path = raw.get("path")
        resolved_path = (root / path).resolve() if path else None
        modules.append(
            ModuleSpec(
                name=str(raw["name"]),
                kind=str(raw.get("type", "command")),
                enabled=bool(raw.get("enabled", True)),
                path=resolved_path,
                remote=str(raw.get("remote", "origin")),
                branch=str(raw.get("branch", "main")),
                update_commands=[str(v) for v in raw.get("update_commands", [])],
                check_commands=[str(v) for v in raw.get("check_commands", [])],
                doctor_commands=[str(v) for v in raw.get("doctor_commands", [])],
                backup_paths=[(root / p).resolve() for p in raw.get("backup_paths", [])],
                notes=str(raw.get("notes", "")),
                extension_module=str(raw.get("extension_module", "")),
            )
        )
    return modules


def _select_modules(all_modules: list[ModuleSpec], names: list[str] | None, include_disabled: bool) -> list[ModuleSpec]:
    if names:
        selected = {n.strip() for n in names if n and n.strip()}
        filtered = [m for m in all_modules if m.name in selected]
    else:
        filtered = list(all_modules)
    if include_disabled:
        return filtered
    return [m for m in filtered if m.enabled]


def _git_status(module: ModuleSpec, do_fetch: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "git", "ok": False}
    if not module.path or not module.path.exists():
        result["error"] = "path_missing"
        return result
    if not (module.path / ".git").exists():
        result["error"] = "not_git_repo"
        return result

    if do_fetch:
        _run(f"git fetch {module.remote} --prune", cwd=module.path)
    head = _run("git rev-parse HEAD", cwd=module.path)
    upstream = _run(f"git rev-parse {module.remote}/{module.branch}", cwd=module.path)
    if head.returncode != 0 or upstream.returncode != 0:
        result["error"] = "git_rev_parse_failed"
        result["stderr"] = (head.stderr or "") + (upstream.stderr or "")
        return result

    counts = _run(
        f"git rev-list --left-right --count HEAD...{module.remote}/{module.branch}",
        cwd=module.path,
    )
    dirty = _run("git status --porcelain", cwd=module.path)
    behind = 0
    ahead = 0
    if counts.returncode == 0:
        parts = counts.stdout.strip().split()
        if len(parts) == 2:
            ahead = int(parts[0])
            behind = int(parts[1])

    result.update(
        {
            "ok": True,
            "path": str(module.path),
            "head": head.stdout.strip(),
            "upstream": upstream.stdout.strip(),
            "ahead": ahead,
            "behind": behind,
            "dirty": bool((dirty.stdout or "").strip()),
        }
    )
    return result


def _command_status(module: ModuleSpec) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "command", "ok": True}
    if module.path and not module.path.exists():
        result["ok"] = False
        result["error"] = "path_missing"
        return result
    checks = []
    for cmd in module.check_commands:
        proc = _run(cmd, cwd=module.path, timeout=20)
        checks.append(
            {
                "cmd": cmd,
                "ok": proc.returncode == 0,
                "stdout": (proc.stdout or "").strip()[:500],
                "stderr": (proc.stderr or "").strip()[:500],
            }
        )
    result["checks"] = checks
    if checks and any(not c["ok"] for c in checks):
        result["ok"] = False
    return result


def _backup_module(module: ModuleSpec) -> Path | None:
    if not module.backup_paths:
        return None
    hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    out_dir = hermes_home / "backups" / "ecosystem"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"{module.name}-{stamp}.tar.gz"
    with tarfile.open(out_file, "w:gz") as tar:
        for src in module.backup_paths:
            if src.exists():
                tar.add(src, arcname=src.name)
    return out_file


def _run_commands(module: ModuleSpec, commands: list[str], dry_run: bool) -> list[dict[str, Any]]:
    events = []
    for cmd in commands:
        if dry_run:
            events.append({"cmd": cmd, "ok": True, "dry_run": True})
            continue
        proc = _run(cmd, cwd=module.path)
        events.append(
            {
                "cmd": cmd,
                "ok": proc.returncode == 0,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            }
        )
        if proc.returncode != 0:
            break
    return events


def _update_module(module: ModuleSpec, dry_run: bool, with_backup: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"name": module.name, "ok": True, "events": []}
    if module.path and not module.path.exists():
        result["ok"] = False
        result["error"] = "path_missing"
        return result

    if with_backup:
        backup_file = _backup_module(module)
        if backup_file:
            result["backup"] = str(backup_file)

    if module.kind == "extension":
        if dry_run:
            result["events"].append({"cmd": f"extension.update({module.extension_module})", "ok": True, "dry_run": True})
            return result
        if not module.extension_module:
            result["ok"] = False
            result["error"] = "missing_extension_module"
            return result
        try:
            from extensions.registry import update_extension
            ext_result = update_extension(module.extension_module, repo_path=str(module.path) if module.path else None)
            result["events"].append({"cmd": f"extension.update({module.extension_module})", "ok": bool(ext_result.get("success")), "result": ext_result})
            if not ext_result.get("success"):
                result["ok"] = False
            return result
        except Exception as e:
            result["ok"] = False
            result["error"] = f"extension_update_failed: {type(e).__name__}: {e}"
            return result

    if module.kind == "git":
        update_commands = module.update_commands or [
            f"git fetch {module.remote} --prune",
            f"git pull --ff-only {module.remote} {module.branch}",
        ]
    else:
        update_commands = module.update_commands

    if not update_commands:
        result["ok"] = False
        result["error"] = "no_update_commands"
        return result

    result["events"].extend(_run_commands(module, update_commands, dry_run=dry_run))
    if any(not e.get("ok", False) for e in result["events"]):
        result["ok"] = False
        return result

    if module.doctor_commands:
        doctor_events = _run_commands(module, module.doctor_commands, dry_run=dry_run)
        result["doctor"] = doctor_events
        if any(not e.get("ok", False) for e in doctor_events):
            result["ok"] = False
    return result


def _append_logs(records: list[dict[str, Any]]) -> Path:
    hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
    log_dir = hermes_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out = log_dir / f"ecosystem-updates-{_utc_now().strftime('%Y-%m-%d')}.jsonl"
    with out.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def ecosystem_command(args) -> None:
    config_path = _config_path(getattr(args, "config", None))
    modules = _load_modules(config_path)
    selected = _select_modules(
        modules,
        getattr(args, "module", None),
        include_disabled=getattr(args, "all", False),
    )
    if not selected:
        print("No matching ecosystem modules.")
        return

    action = getattr(args, "ecosystem_action", None) or "status"
    if action in {"status", "check", "list"}:
        do_fetch = not getattr(args, "no_fetch", False)
        print(f"Ecosystem config: {config_path}")
        print("")
        for module in selected:
            print(f"[{module.name}] ({'enabled' if module.enabled else 'disabled'})")
            if module.kind == "git":
                status = _git_status(module, do_fetch=do_fetch)
                if not status.get("ok"):
                    print(f"  status: error ({status.get('error')})")
                    continue
                print(f"  path: {status['path']}")
                print(f"  ahead/behind: {status['ahead']}/{status['behind']}")
                print(f"  dirty: {'yes' if status['dirty'] else 'no'}")
                print(f"  head: {status['head'][:12]}  upstream: {status['upstream'][:12]}")
            else:
                status = _command_status(module)
                print(f"  type: command")
                if "error" in status:
                    print(f"  status: error ({status['error']})")
                elif status.get("checks"):
                    failed = [c for c in status["checks"] if not c["ok"]]
                    print(f"  checks: {len(status['checks'])} ({'failed' if failed else 'ok'})")
                else:
                    print("  checks: none")
            if module.notes:
                print(f"  notes: {module.notes}")
            print("")
        return

    if action != "update":
        print(f"Unknown ecosystem action: {action}")
        return

    dry_run = bool(getattr(args, "dry_run", False))
    with_backup = not bool(getattr(args, "no_backup", False))
    strict = bool(getattr(args, "strict", False))

    print(f"Ecosystem config: {config_path}")
    print(f"Modules selected: {len(selected)}")
    print(f"Dry run: {'yes' if dry_run else 'no'}")
    print("")

    records = []
    failures = 0
    for module in selected:
        print(f"→ Updating {module.name} ...")
        record = {
            "timestamp": _utc_now().isoformat(),
            "module": module.name,
            "action": "update",
            "dry_run": dry_run,
        }
        result = _update_module(module, dry_run=dry_run, with_backup=with_backup)
        record["result"] = result
        records.append(record)

        if result.get("ok"):
            print("  ✓ ok")
        else:
            failures += 1
            print(f"  ✗ failed ({result.get('error', 'command_failed')})")
            if strict:
                break

    log_path = _append_logs(records)
    print("")
    print(f"Update log: {log_path}")
    if failures:
        print(f"Completed with {failures} failure(s).")
        raise SystemExit(1)
    print("All selected modules updated successfully.")