#!/usr/bin/env python3
"""
Lifecycle hooks system for Hermes agent.

Based on the hooks architecture from Claude Code's source code.
Hooks are shell commands or Python callbacks that fire at specific lifecycle events.

Events (mirroring Claude Code's HOOK_EVENTS):
- session_start: When a new session begins
- session_end: When a session ends
- pre_tool_use: Before a tool is executed
- post_tool_use: After a tool is executed (success or failure)
- pre_compact: Before context compaction
- post_compact: After context compaction
- notification: When a notification should be shown to the user
- subagent_start: When a subagent is spawned
- subagent_stop: When a subagent completes
- task_created: When a task is created
- task_completed: When a task finishes

Hook types:
- sync: Run and wait for result (can modify behavior)
- async: Fire and forget (non-blocking)
- notification: Show output to user

Hook JSON output:
- sync hooks can return JSON to influence behavior:
  {"block": true, "message": "reason"}  -> block the event
  {"modify": {"key": "value"}}          -> modify event parameters
  {"notify": "message to user"}         -> surface a message

Usage:
    from hooks import HooksManager

    hooks = HooksManager()
    
    # Register a sync hook that runs before every tool use
    @hooks.register("pre_tool_use", sync=True)
    def log_tool_use(event):
        print(f"Tool will run: {event['tool']}")
        return None  # Don't block
    
    # Register an async hook
    hooks.register_async("post_tool_use", lambda e: log_to_file(e))
    
    # Fire hooks
    result = hooks.fire("pre_tool_use", {"tool": "Read", "path": "/tmp/test.txt"})
    if not result.blocked:
        # Run the tool
        hooks.fire("post_tool_use", {"tool": "Read", "result": "content"})

Configuration (CLAUDE.md format):
    Hooks are discovered from:
    1. ~/.hermes/hooks/ directory (user-level)
    2. .hermes/hooks/ directory (project-level)
    3. Programmatic registration
    Each .sh or .py file in the hooks/ directory fires on the matching event.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

# --------------- Hook Event Types ---------------

@dataclass
class HookResult:
    """Result from a hook execution."""
    hook_name: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    blocked: bool = False
    block_reason: str = ""
    notification: str = ""
    modifications: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0


# --------------- Hook Registration ---------------

class HookRegistration:
    """Registration info for a single hook."""
    def __init__(
        self,
        name: str,
        event: str,
        handler: Optional[Callable] = None,
        script_path: Optional[str] = None,
        sync: bool = False,
        matcher: Optional[str] = None,  # regex to match against tool name etc
        timeout: int = 30,
    ):
        self.name = name
        self.event = event
        self.handler = handler
        self.script_path = script_path
        self.sync = sync
        self.matcher = matcher
        self.timeout = timeout

    def matches(self, event_data: Dict[str, Any]) -> bool:
        """Check if this hook should fire for the given event data."""
        if self.matcher:
            # Match against tool name, command, or other identifiable fields
            candidates = [
                event_data.get("tool", ""),
                event_data.get("command", ""),
                event_data.get("name", ""),
                event_data.get("event_type", ""),
            ]
            combined = " ".join(str(c) for c in candidates if c)
            return bool(re.search(self.matcher, combined, re.IGNORECASE))
        return True


# --------------- Hooks Manager ---------------

class HooksManager:
    """Manages lifecycle hooks for the Hermes agent.
    
    Mirrors Claude Code's hooks system with Python-native implementation.
    """
    
    # All supported hook events (mirrors HOOK_EVENTS from Claude Code)
    VALID_EVENTS = {
        "session_start",
        "session_end",
        "pre_tool_use",
        "post_tool_use",
        "pre_compact",
        "post_compact",
        "notification",
        "subagent_start",
        "subagent_stop",
        "task_created",
        "task_completed",
    }
    
    # Default hooks directory paths
    HOOK_DIRS = [
        Path.home() / ".hermes" / "hooks",        # User-level
    ]

    def __init__(self):
        self._hooks: Dict[str, List[HookRegistration]] = {
            event: [] for event in self.VALID_EVENTS
        }
        self._async_hooks: List[HookRegistration] = []
        self._fired_count: Dict[str, int] = {event: 0 for event in self.VALID_EVENTS}
    
    def register(
        self,
        event: str,
        sync: bool = False,
        matcher: Optional[str] = None,
        timeout: int = 30,
        name: Optional[str] = None,
    ):
        """Decorator to register a hook handler.
        
        @hooks.register("pre_tool_use", sync=True, matcher="Read|Write")
        def log_file_access(event_data):
            print(f"File accessed: {event_data.get('path')}")
        """
        if event not in self.VALID_EVENTS:
            raise ValueError(f"Unknown hook event: {event}. Valid: {sorted(self.VALID_EVENTS)}")
        
        def decorator(func):
            hook = HookRegistration(
                name=name or func.__name__,
                event=event,
                handler=func,
                sync=sync,
                matcher=matcher,
                timeout=timeout,
            )
            if sync:
                self._hooks[event].append(hook)
            else:
                self._async_hooks.append(hook)
            return func
        return decorator
    
    def register_script(
        self,
        event: str,
        script_path: str,
        sync: bool = False,
        matcher: Optional[str] = None,
        timeout: int = 30,
    ):
        """Register a shell script as a hook."""
        if not os.path.isfile(script_path):
            return  # Silently skip missing scripts
        
        hook = HookRegistration(
            name=Path(script_path).stem,
            event=event,
            script_path=script_path,
            sync=sync,
            matcher=matcher,
            timeout=timeout,
        )
        if sync:
            self._hooks[event].append(hook)
        else:
            self._async_hooks.append(hook)
    
    def fire(self, event: str, event_data: Dict[str, Any] = None) -> List[HookResult]:
        """Fire synchronous hooks for an event.
        
        Args:
            event: The hook event name
            event_data: Data to pass to hook handlers
            
        Returns:
            List of HookResult objects. If any hook blocks, the event should be blocked.
        """
        if event not in self.VALID_EVENTS:
            return []
        
        event_data = event_data or {}
        results = []
        
        for hook in self._hooks[event]:
            # Check if hook's matcher applies
            if not hook.matches(event_data):
                continue
            
            if hook.handler:
                result = self._run_handler_hook(hook, event_data)
            elif hook.script_path:
                result = self._run_script_hook(hook, event_data)
            else:
                continue
            
            results.append(result)
            self._fired_count[event] += 1
            
            # If any hook blocks, return immediately with block signal
            if result.blocked:
                return results  # Short-circuit
        
        # Fire async hooks (non-blocking)
        for hook in self._async_hooks:
            if hook.event == event and hook.matches(event_data):
                self._run_async_hook(hook, event_data)
        
        return results
    
    def _run_handler_hook(self, hook: HookRegistration, event_data: Dict) -> HookResult:
        """Run a Python function hook."""
        start = time.time()
        try:
            result = hook.handler(event_data)
            duration = int((time.time() - start) * 1000)
            
            # Parse result
            return self._parse_hook_result(hook.name, result, duration)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return HookResult(
                hook_name=hook.name,
                exit_code=1,
                stderr=str(e),
                duration_ms=duration,
            )
    
    def _run_script_hook(self, hook: HookRegistration, event_data: Dict) -> HookResult:
        """Run a shell script hook."""
        start = time.time()
        env = os.environ.copy()
        env["HERMES_HOOK_EVENT"] = hook.event
        env["HERMES_HOOK_NAME"] = hook.name
        env["HERMES_HOOK_DATA"] = json.dumps(event_data, default=str)
        
        try:
            proc = subprocess.run(
                ["/bin/bash", hook.script_path],
                env=env,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
            )
            duration = int((time.time() - start) * 1000)
            
            # Try to parse JSON output
            result = self._parse_hook_result(
                hook.name,
                proc.stdout.strip(),
                duration,
                proc.exitcode,
                proc.stderr.strip(),
            )
            return result
        except subprocess.TimeoutExpired:
            duration = int((time.time() - start) * 1000)
            return HookResult(
                hook_name=hook.name,
                exit_code=124,  # timeout exit code
                stderr=f"Hook timed out after {hook.timeout}s",
                duration_ms=duration,
                blocked=False,
            )
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            return HookResult(
                hook_name=hook.name,
                exit_code=1,
                stderr=str(e),
                duration_ms=duration,
            )
    
    def _run_async_hook(self, hook: HookRegistration, event_data: Dict):
        """Run an async hook (fire and forget)."""
        import threading
        
        def _run():
            try:
                if hook.handler:
                    hook.handler(event_data)
                elif hook.script_path:
                    env = os.environ.copy()
                    env["HERMES_HOOK_EVENT"] = hook.event
                    env["HERMES_HOOK_DATA"] = json.dumps(event_data, default=str)
                    subprocess.run(
                        ["/bin/bash", hook.script_path],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=hook.timeout,
                    )
            except Exception:
                pass  # Async hooks never raise
        
        t = threading.Thread(target=_run, daemon=True)
        t.start()
    
    def _parse_hook_result(
        self,
        hook_name: str,
        output: Any,
        duration_ms: int,
        exit_code: int = 0,
        stderr: str = "",
    ) -> HookResult:
        """Parse hook output into a HookResult."""
        if output is None:
            return HookResult(
                hook_name=hook_name,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
        
        # If output is a dict, parse directly
        if isinstance(output, dict):
            return HookResult(
                hook_name=hook_name,
                exit_code=exit_code,
                duration_ms=duration_ms,
                blocked=output.get("block", False),
                block_reason=output.get("message", ""),
                notification=output.get("notify", output.get("notification", "")),
                modifications=output.get("modify", {}),
            )
        
        # If output is a string, try to parse as JSON
        if isinstance(output, str):
            try:
                data = json.loads(output)
                if isinstance(data, dict):
                    return HookResult(
                        hook_name=hook_name,
                        exit_code=exit_code,
                        duration_ms=duration_ms,
                        blocked=data.get("block", False),
                        block_reason=data.get("message", ""),
                        notification=data.get("notify", data.get("notification", "")),
                        modifications=data.get("modify", {}),
                    )
            except json.JSONDecodeError:
                # Plain text output -> just capture it
                pass
        
        return HookResult(
            hook_name=hook_name,
            exit_code=exit_code,
            stdout=str(output) if not isinstance(output, str) else output,
            stderr=stderr,
            duration_ms=duration_ms,
        )
    
    def discover_hooks_from_dirs(self):
        """Auto-discover hooks from the standard directories.
        
        Directory structure:
            ~/.hermes/hooks/
                session_start.sh        # Fires on session_start
                pre_tool_use.sh         # Fires before every tool
                post_tool_use.sh        # Fires after every tool
                pre_compact.sh          # Fires before compaction
        """
        for hook_dir in self.HOOK_DIRS:
            if not hook_dir.is_dir():
                continue
            
            pattern = re.compile(rf'^({"|".join(self.VALID_EVENTS)})(_.+)?\.(sh|py)$')
            
            for script in sorted(hook_dir.glob("*")):
                match = pattern.match(script.name)
                if match:
                    event = match.group(1)
                    self.register_script(
                        event=event,
                        script_path=str(script),
                        sync=True,
                    )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get hook execution statistics."""
        sync_total = sum(len(hooks) for hooks in self._hooks.values())
        async_total = len(self._async_hooks)
        
        return {
            "sync_hooks": sync_total,
            "async_hooks": async_total,
            "total_hooks": sync_total + async_total,
            "fires": dict(self._fired_count),
        }


# --------------- Convenience Functions ---------------

_default_hooks: Optional[HooksManager] = None


def get_hooks() -> HooksManager:
    """Get the global hooks manager."""
    global _default_hooks
    if _default_hooks is None:
        _default_hooks = HooksManager()
        _default_hooks.discover_hooks_from_dirs()
    return _default_hooks


# Quick self-test
if __name__ == "__main__":
    import tempfile

    hooks = HooksManager()
    
    # Test registration
    @hooks.register("pre_tool_use", sync=True)
    def log_tool(event):
        return None
    
    @hooks.register("session_start", sync=True)
    def welcome(event):
        return {"notify": "Welcome!"}
    
    @hooks.register("pre_tool_use", sync=True, matcher="Bash")
    def log_bash(event):
        return None
    
    # Test firing
    results = hooks.fire("pre_tool_use", {"tool": "Read", "path": "/tmp"})
    assert len(results) == 1  # Only log_tool matches (log_bash has Bash matcher)
    
    results = hooks.fire("pre_tool_use", {"tool": "Bash", "command": "ls"})
    assert len(results) == 2  # Both log_tool and log_bash match
    
    results = hooks.fire("session_start", {})
    assert len(results) == 1
    assert results[0].notification == "Welcome!", f"Got: '{results[0].notification}'"
    
    # Test blocking
    @hooks.register("post_tool_use", sync=True, matcher="rm")
    def block_rm(event):
        if "-rf" in event.get("command", ""):
            return json.dumps({"block": True, "message": "Blocked recursive rm"})
        return None
    
    results = hooks.fire("post_tool_use", {"tool": "Bash", "command": "rm -rf /"})
    assert len(results) == 1, f"Expected 1, got {len(results)}"
    assert results[0].blocked == True
    assert "recursive rm" in results[0].block_reason
    
    results = hooks.fire("post_tool_use", {"tool": "Bash", "command": "rm file.txt"})
    assert len(results) == 1, f"Expected 1, got {len(results)}"
    assert results[0].blocked == False  # Not recursive
    
    # Test stats
    stats = hooks.get_stats()
    # pre_tool_use: log_tool, log_bash (2) + session_start: welcome (1) + post_tool_use: block_rm (1) = 4
    assert stats["sync_hooks"] == 4, f"Got {stats['sync_hooks']}"
    assert stats["async_hooks"] == 0
    
    print("All hooks tests passed!")
