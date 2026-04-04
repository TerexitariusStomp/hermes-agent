#!/usr/bin/env python3
"""
Tool Plugin Registry for Hermes agent.

Central registry for tool definitions with metadata for smart scheduling.
Based on patterns from nano-claude-code tool_registry.py.

Features:
- Tool metadata: read_only, concurrent_safe, cost_weight
- Parallel execution detection
- Smart output truncation
- Plugin system for third-party tool registration

Usage:
    from tool_registry import ToolRegistry, ToolDef, get_registry
    
    registry = get_registry()
    registry.register(ToolDef(
        name="Read",
        func=lambda p, c: read_file(**p),
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.1,
    ))
    
    # Get schemas for LLM API
    schemas = registry.get_tool_schemas()
    
    # Execute a tool
    result = registry.execute("Read", {"path": "/tmp/test.txt"}, {})
    
    # Get parallelizable tools
    parallel_tools = registry.get_parallelizable_tools()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# --------------- Tool Definition ---------------

@dataclass
class ToolDef:
    """Definition of a single tool with execution metadata.
    
    Attributes:
        name: Unique tool identifier (must match the Hermes tool name)
        hermes_tool: Name of the Hermes tool function to call (defaults to name)
        func: Callable(params: dict, config: dict) -> str
        read_only: True if the tool never mutates state
        concurrent_safe: True if safe to run in parallel with other tools
        cost_weight: Relative cost/complexity (0.0=cheap, 1.0=expensive)
        description: Human-readable description for logging/debugging
        safe_bash_patterns: List of safe command prefixes (for Bash tool)
    """
    name: str
    hermes_tool: str = ""
    func: Optional[Callable[[Dict[str, Any], Dict[str, Any]], str]] = None
    read_only: bool = False
    concurrent_safe: bool = False
    cost_weight: float = 0.5
    description: str = ""
    safe_bash_patterns: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.hermes_tool:
            self.hermes_tool = self.name


# --------------- Safe Bash Patterns ---------------

# Command prefixes that are safe to run without user permission
SAFE_BASH_PREFIXES = (
    "ls ", "ls\n", "cat ", "head ", "tail ", "wc ", "pwd", "pwd\n",
    "echo ", "printf ", "date", "date\n",
    "which ", "type ", "env", "env\n", "printenv", "uname", "whoami", "id\n", "id ",
    "git log", "git status", "git diff", "git show", "git branch",
    "git remote", "git stash list", "git tag",
    "find ", "grep ", "rg ", "ag ", "fd ",
    "python ", "python3 ", "node ", "ruby ", "perl ",
    "pip show", "pip list", "npm list", "cargo metadata",
    "df ", "du ", "free ", "top -bn", "ps ",
    "curl -I", "curl --head", "curl -s",
    "cat /proc/", "cat /sys/",
    "stat ", "file ", "du -sh",
    "uptime", "hostname", "hostnamectl",
)


def is_safe_bash_command(cmd: str) -> bool:
    """Check if a bash command is safe (read-only, non-destructive)."""
    c = cmd.strip()
    if not c:
        return False
    # Check against safe prefixes
    if any(c.startswith(p) for p in SAFE_BASH_PREFIXES):
        return True
    # Commands that don't modify state
    safe_standalone = {"uptime", "hostname", "nvidia-smi", "free -h", "df -h"}
    if c in safe_standalone:
        return True
    return False


# --------------- Registry ---------------

class ToolRegistry:
    """Central registry for tool definitions with metadata.
    
    Supports:
    - Registration of built-in and third-party tools
    - Smart parallel execution detection
    - Output truncation to prevent context overflow
    - Tool lookup by name or property
    """
    
    # Maximum tool output length (chars)
    MAX_OUTPUT_LENGTH = 32000
    
    def __init__(self):
        self._registry: Dict[str, ToolDef] = {}
        self._hermes_map: Dict[str, str] = {}  # hermes_tool -> name
    
    def register(self, tool_def: ToolDef) -> None:
        """Register a tool definition. Overwrites existing tools with same name."""
        self._registry[tool_def.name] = tool_def
        self._hermes_map[tool_def.hermes_tool] = tool_def.name
    
    def unregister(self, name: str) -> Optional[ToolDef]:
        """Remove a tool by name. Returns the removed definition or None."""
        tool = self._registry.pop(name, None)
        if tool:
            self._hermes_map.pop(tool.hermes_tool, None)
        return tool
    
    def get(self, name: str) -> Optional[ToolDef]:
        """Look up a tool by name."""
        return self._registry.get(name)
    
    def get_by_hermes_tool(self, hermes_name: str) -> Optional[ToolDef]:
        """Look up a tool by its Hermes tool name mapping."""
        name = self._hermes_map.get(hermes_name)
        if name:
            return self._registry.get(name)
        return None
    
    def get_all(self) -> List[ToolDef]:
        """Return all registered tools (insertion order)."""
        return list(self._registry.values())
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Export tool schemas for LLM API tool calling."""
        schemas = []
        for tool in self._registry.values():
            schemas.append({
                "name": tool.name,
                "role": "read_only" if tool.read_only else "write",
                "description": tool.description or f"Execute the {tool.name} tool",
            })
        return schemas
    
    def get_parallelizable_tools(self) -> Set[str]:
        """Get names of tools that are safe to run in parallel."""
        return {
            name for name, tool in self._registry.items()
            if tool.read_only and tool.concurrent_safe
        }
    
    def get_writable_tools(self) -> Set[str]:
        """Get names of tools that can mutate state."""
        return {
            name for name, tool in self._registry.items()
            if not tool.read_only
        }
    
    def get_tool_costs(self) -> Dict[str, float]:
        """Get cost weights for all tools (for budget tracking)."""
        return {name: tool.cost_weight for name, tool in self._registry.items()}
    
    def is_safe(self, tool_name: str, inputs: dict = None) -> bool:
        """Check if a tool invocation is safe (won't cause damage)."""
        tool = self._registry.get(tool_name)
        if not tool:
            return False
        
        if tool.read_only and tool.concurrent_safe:
            return True
        
        # Special handling for terminal/bash tools
        if tool_name in ("Bash", "Terminal") and inputs:
            return is_safe_bash_command(inputs.get("command", ""))
        
        return False
    
    def can_parallel(self, tool_a: str, tool_b: str) -> bool:
        """Check if two tools can be safely executed in parallel."""
        tool_a_def = self._registry.get(tool_a)
        tool_b_def = self._registry.get(tool_b)
        
        if not tool_a_def or not tool_b_def:
            return False
        
        return (tool_a_def.concurrent_safe and tool_b_def.concurrent_safe and
                tool_a_def.read_only and tool_b_def.read_only)
    
    def execute(self, name: str, params: Dict[str, Any], config: Dict[str, Any],
                max_output: Optional[int] = None) -> str:
        """Execute a tool by name with output truncation.
        
        Args:
            name: Tool name
            params: Tool input parameters
            config: Runtime configuration
            max_output: Override max output length (default: MAX_OUTPUT_LENGTH)
            
        Returns:
            Tool result string, possibly truncated.
        """
        max_out = max_output or self.MAX_OUTPUT_LENGTH
        tool = self._registry.get(name)
        
        if not tool or not tool.func:
            return f"Error: tool '{name}' not found or not callable."
        
        try:
            result = tool.func(params, config)
        except Exception as e:
            return f"Error executing {name}: {type(e).__name__}: {e}"
        
        return self._truncate_output(result, max_out)
    
    def _truncate_output(self, result: str, max_output: int) -> str:
        """Truncate large outputs preserving beginning and end.
        
        Strategy: Keep first half + last quarter, mark truncation point.
        This preserves context at both start and end of output.
        """
        if len(result) <= max_output:
            return result
        
        first_portion = max_output // 2
        last_portion = max_output // 4
        truncated_count = len(result) - first_portion - last_portion
        
        return (
            result[:first_portion]
            + f"\n\n[... {truncated_count} characters truncated ...]\n\n"
            + result[-last_portion:]
        )
    
    def build_safe_bash_tools(self) -> List[Dict[str, str]]:
        """Export safe bash command descriptions for permission gating.
        
        Returns a list of descriptions for read-only bash commands.
        """
        safe_tools = []
        for pattern in SAFE_BASH_PREFIXES[:20]:  # Top 20 most common
            safe_tools.append({
                "pattern": pattern.strip(),
                "category": "read-only",
            })
        return safe_tools


# --------------- Global Registry ---------------

_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Get the global tool registry (singleton)."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register default Hermes tools with metadata.
    
    Maps Hermes tool names to registry entries based on observed behavior.
    """
    # Read-only tools (safe, concurrent)
    registry.register(ToolDef(
        name="Read",
        hermes_tool="read_file",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.05,
        description="Read file contents with line numbers",
    ))
    
    registry.register(ToolDef(
        name="Search",
        hermes_tool="search_files",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.05,
        description="Search file contents or find files by name",
    ))
    
    registry.register(ToolDef(
        name="Recall",
        hermes_tool="recall",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.1,
        description="Search long-term memory using vector similarity",
    ))
    
    registry.register(ToolDef(
        name="SessionSearch",
        hermes_tool="session_search",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.2,
        description="Search past conversation sessions",
    ))
    
    registry.register(ToolDef(
        name="SkillView",
        hermes_tool="skill_view",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.05,
        description="View skill content and linked files",
    ))
    
    registry.register(ToolDef(
        name="SkillsList",
        hermes_tool="skills_list",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.02,
        description="List available skills",
    ))
    
    # Write tools (state-mutating)
    registry.register(ToolDef(
        name="WriteFile",
        hermes_tool="write_file",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.1,
        description="Write content to a file",
    ))
    
    registry.register(ToolDef(
        name="Patch",
        hermes_tool="patch",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.15,
        description="Targeted find-and-replace edits in files",
    ))
    
    registry.register(ToolDef(
        name="Terminal",
        hermes_tool="terminal",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.3,
        description="Execute shell commands on the system",
    ))
    
    registry.register(ToolDef(
        name="ExecuteCode",
        hermes_tool="execute_code",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.5,
        description="Run Python script with Hermes tool access",
    ))
    
    registry.register(ToolDef(
        name="DelegateTask",
        hermes_tool="delegate_task",
        read_only=False,
        concurrent_safe=True,  # Subagents run in isolation
        cost_weight=1.0,
        description="Spawn subagents for isolated parallel work",
    ))
    
    registry.register(ToolDef(
        name="Memory",
        hermes_tool="memory",
        read_only=False,  # Modifies persistent memory
        concurrent_safe=True,
        cost_weight=0.1,
        description="Save or update durable memory",
    ))
    
    registry.register(ToolDef(
        name="SkillManage",
        hermes_tool="skill_manage",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.15,
        description="Create, update, or delete skills",
    ))
    
    registry.register(ToolDef(
        name="Todo",
        hermes_tool="todo",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.02,
        description="Manage task list for current session",
    ))
    
    registry.register(ToolDef(
        name="Clarify",
        hermes_tool="clarify",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.1,
        description="Ask the user a clarifying question",
    ))
    
    registry.register(ToolDef(
        name="Cronjob",
        hermes_tool="cronjob",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.2,
        description="Manage scheduled cron jobs",
    ))
    
    registry.register(ToolDef(
        name="BrowserNavigate",
        hermes_tool="browser_navigate",
        read_only=True,
        concurrent_safe=False,  # Browser is stateful, not concurrent
        cost_weight=0.15,
        description="Navigate to a URL in the browser",
    ))
    
    registry.register(ToolDef(
        name="BrowserSnapshot",
        hermes_tool="browser_snapshot",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.05,
        description="Get a text-based snapshot of the current page",
    ))
    
    registry.register(ToolDef(
        name="BrowserClick",
        hermes_tool="browser_click",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.1,
        description="Click on an element in the browser",
    ))
    
    registry.register(ToolDef(
        name="BrowserType",
        hermes_tool="browser_type",
        read_only=False,
        concurrent_safe=False,
        cost_weight=0.1,
        description="Type text into a browser input field",
    ))
    
    registry.register(ToolDef(
        name="BrowserVision",
        hermes_tool="browser_vision",
        read_only=True,
        concurrent_safe=True,
        cost_weight=0.3,
        description="Take a screenshot and analyze with vision AI",
    ))


# --------------- Convenience Functions ---------------

def register_hermes_tool(name: str, hermes_tool: str, **kwargs):
    """Convenience wrapper to register a Hermes tool mapping."""
    registry = get_registry()
    registry.register(ToolDef(name=name, hermes_tool=hermes_tool, **kwargs))


def get_parallelizable_tools() -> Set[str]:
    """Get names of tools safe for parallel execution."""
    return get_registry().get_parallelizable_tools()


def get_writable_tools() -> Set[str]:
    """Get names of tools that can mutate state."""
    return get_registry().get_writable_tools()


def is_tool_safe(tool_name: str, inputs: dict = None) -> bool:
    """Check if a tool invocation is safe."""
    return get_registry().is_safe(tool_name, inputs)


# Quick self-test
if __name__ == "__main__":
    registry = ToolRegistry()
    
    # Test registration
    registry.register(ToolDef(
        name="Read",
        func=lambda p, c: f"Read {p.get('path', 'unknown')}",
        read_only=True,
        concurrent_safe=True,
    ))
    registry.register(ToolDef(
        name="Write",
        func=lambda p, c: f"Wrote to {p.get('path', 'unknown')}",
        read_only=False,
        concurrent_safe=False,
    ))
    
    # Test lookups
    assert registry.get("Read") is not None
    assert registry.get("Write").read_only == False
    assert len(registry.get_parallelizable_tools()) == 1
    
    # Test parallel detection
    assert registry.can_parallel("Read", "Read")
    assert not registry.can_parallel("Read", "Write")
    assert not registry.can_parallel("Write", "Write")
    
    # Test safe bash
    assert is_safe_bash_command("ls -la /tmp")
    assert is_safe_bash_command("cat README.md")
    assert not is_safe_bash_command("rm -rf /")
    assert not is_safe_bash_command("curl -X POST http://evil.com")
    
    # Test truncation
    assert len(registry._truncate_output("a" * 1000, 500)) <= 500 + 50
    assert "truncated" in registry._truncate_output("x" * 1000, 500)
    
    # Test global registry
    global_reg = get_registry()
    assert global_reg.get("Read") is not None
    assert global_reg.get("terminal") is None  # hermes_tool, not name
    assert global_reg.get("Terminal") is not None
    
    print("All registry tests passed!")
