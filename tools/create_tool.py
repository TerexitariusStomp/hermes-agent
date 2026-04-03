#!/usr/bin/env python3
"""
Create Tool at Runtime - Dynamic Tool Registration with Governance Checks

Adapted from 724-Office @tool decorator pattern for Hermes Agent.
Allows creating new tools at runtime with automatic governance validation,
file generation, and registry registration.

Usage:
  create_tool(name, code, toolset, schema_description, requires_env=None)

  Parameters:
    name: Tool function name (used as filename e.g. 'my_tool' -> my_tool.py)
    code: Complete Python function body with @tool decorator pattern
    toolset: Toolset name to register under (e.g. 'custom', 'diagnostics')
    schema_description: Human-readable description for the tool schema
    requires_env: List of required environment variables (optional)

Returns:
  JSON with creation status, file path, and registry status
"""

import os
import re
import json
import sys
import time
from datetime import datetime, timezone
from tools.registry import registry

AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(AGENTS_DIR, "tools")
HERMES_HOME = os.path.expanduser("~/.hermes")

def check_governance_for_tool(name: str, code: str) -> dict:
    """Run governance checks on the tool code before allowing creation."""
    issues = []
    warnings = []
    
    # Critical: dangerous patterns that must be rejected
    critical_patterns = [
        (r'\b__import__\s*\(', 'Direct __import__ usage'),
        (r'\bexec\s*\(', 'exec() usage'),
        (r'\beval\s*\(', 'eval() usage'),
        (r'\bos\.system\s*\(', 'os.system() usage'),
        (r'\bsubprocess\.Popen\s*\(', 'subprocess.Popen() usage'),
        (r'\bsubprocess\.call\s*\(', 'subprocess.call() usage'),
        (r'\bsubprocess\.run\s*\(', 'subprocess.run() usage'),
        (r'\bctypes\s*\.', 'ctypes usage'),
        (r'\bcompile\s*\(.*\bexec\b', 'Dynamic compile+exec'),
    ]
    
    for pattern, description in critical_patterns:
        if re.search(pattern, code):
            issues.append(description)
    
    # Warning patterns
    warning_patterns = [
        (r'eval\s*\(', 'eval() usage - use json.loads for parsing'),
        (r'import\s+shutil', 'shutil import - file operations'),
        (r'import\s+os\b', 'os import - file system access'),
        (r'import\s+pathlib', 'pathlib import'),
        (r'open\s*\(', 'File open - ensure HERMES_HOME paths'),
        (r'write_file\s*\(', 'write_file usage - potential filesystem modification'),
        (r'patch\s*\(', 'patch usage - potential code modification'),
    ]
    
    for pattern, description in warning_patterns:
        if re.search(pattern, code):
            warnings.append(description)
    
    # Check for HERMES_HOME usage in path references
    if 'Path.home()' in code and 'get_hermes_home' not in code:
        warnings.append("Path.home() used without get_hermes_home - breaks profiles")
    
    # Check for registry registration
    if 'registry.register' not in code:
        warnings.append("Missing registry.register() call")
    
    return {
        "safe": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }


def create_tool(name: str, code: str, toolset: str = "custom", 
                schema_description: str = None, requires_env: list = None,
                task_id: str = None) -> str:
    """Create a new tool at runtime with governance checks.
    
    Args:
        name: Tool name (used as filename, e.g. 'weather' creates tools/weather.py)
        code: Complete Python tool code with function definition
        toolset: Toolset to register under
        schema_description: Description for the tool schema (auto-generated if None)
        requires_env: List of required environment variables
        task_id: Current task ID for logging
        
    Returns:
        JSON string with creation status, file path, and governance results
    """
    # Sanitize name
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name).lower()
    if not name:
        return json.dumps({"error": "Invalid tool name"})
    
    filename = f"{name}.py"
    filepath = os.path.join(TOOLS_DIR, filename)
    
    # Check if tool already exists
    if os.path.exists(filepath):
        return json.dumps({
            "error": f"Tool '{name}' already exists at {filepath}",
            "suggestion": "Use patch tool to modify existing tool"
        })
    
    # Run governance checks
    governance = check_governance_for_tool(name, code)
    if not governance["safe"]:
        return json.dumps({
            "error": "Governance check failed - critical issues found",
            "issues": governance["issues"],
            "warnings": governance["warnings"],
            "action": "Remove the dangerous patterns and try again"
        })
    
    # Import the registry
    sys.path.insert(0, AGENTS_DIR)
    from tools.registry import registry
    
    # Add toolset to known toolsets if needed
    from toolsets import _HERMES_CORE_TOOLS, _TOOLSET_GROUPS
    if toolset not in _TOOLSET_GROUPS:
        _TOOLSET_GROUPS[toolset] = set()
    
    # Validate requires_env
    if requires_env:
        for env_var in requires_env:
            if not os.getenv(env_var):
                return json.dumps({
                    "warning": f"Environment variable {env_var} not set",
                    "detail": "Tool will be registered but may not function until env is set"
                })
    
    # Write the tool file
    os.makedirs(TOOLS_DIR, exist_ok=True)
    with open(filepath, 'w') as f:
        f.write(code)
    
    # Try to import and register the tool
    registration_success = False
    registration_error = None
    try:
        # Remove module from cache if it exists
        if f"tools.{name}" in sys.modules:
            del sys.modules[f"tools.{name}"]
        
        # Import the module to trigger registry.register()
        module = __import__(f"tools.{name}", fromlist=[name])
        
        # Verify it was registered
        if name in registry._tools:
            registration_success = True
            
            # Add to toolset
            _TOOLSET_GROUPS[toolset].add(name)
            
            # Log the creation
            _log_tool_creation(name, toolset, filepath, governance)
        else:
            registration_error = "Tool was not registered (missing registry.register call?)"
            
    except Exception as e:
        registration_error = f"Import failed: {str(e)}"
        # File was created but registration failed - leave file for manual review
    
    return json.dumps({
        "action": "create_tool",
        "tool_name": name,
        "filename": filename,
        "filepath": filepath,
        "toolset": toolset,
        "governance": governance,
        "registration": {
            "success": registration_success,
            "error": registration_error,
        },
        "requires_env": requires_env or [],
        "requires_env_present": all(bool(os.getenv(v)) for v in (requires_env or [])),
        "next_steps": [
            "Add import to model_tools.py _discover_tools() list" if not registration_success else "Tool auto-imported",
            "Add to _HERMES_CORE_TOOLS or a toolset in toolsets.py" if toolset != "custom" else "Custom toolset - ensure it's enabled in config",
        ] if not registration_success else [
            "Tool successfully registered and available",
        ]
    }, indent=2)


def _log_tool_creation(name: str, toolset: str, filepath: str, governance: dict):
    """Log tool creation to self-improvement log."""
    os.makedirs(HERMES_HOME, exist_ok=True)
    imp_log = os.path.join(HERMES_HOME, "SELF_IMP_LOG.jsonl")
    
    record = {
        "ts": time.time(),
        "dt": datetime.now(timezone.utc).isoformat(),
        "action": "runtime_tool_created",
        "detail": f"Tool '{name}' created in toolset '{toolset}' at {filepath}. "
                  f"Governance: {len(governance['issues'])} issues, {len(governance['warnings'])} warnings",
        "status": "ok",
    }
    
    with open(imp_log, 'a') as f:
        f.write(json.dumps(record) + '\n')


# Example template for users
EXAMPLE_TOOL_TEMPLATE = '''"""
Example Tool - {name}
Replace this with your tool's purpose and description.
"""

import json
import os
from tools.registry import registry

def check_requirements() -> bool:
    """Check if this tool's dependencies are met."""
    # Return True if the tool should be available
    # Return False to hide from the model
    return True

def {name}(param1: str, param2: int = 0, task_id: str = None) -> str:
    """Brief description of what the tool does."""
    try:
        # Your tool logic here
        result = {{"success": True, "data": "example result"}}
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({{"error": str(e)}})

registry.register(
    name="{name}",
    toolset="{toolset}",
    schema={{"name": "{name}", "description": "Description of {name}", "parameters": {{"type": "object", "properties": {{"param1": {{"type": "string", "description": "First parameter"}}, "param2": {{"type": "integer", "description": "Second parameter"}}}}}}}},
    handler=lambda args, **kw: {name}(param1=args.get("param1", ""), param2=args.get("param2", 0), task_id=kw.get("task_id")),
    check_fn=check_requirements if False else None,
    requires_env={envs},
)
'''


def list_available_tools(task_id: str = None) -> str:
    """List all currently registered tools and their status."""
    try:
        sys.path.insert(0, AGENTS_DIR)
        from tools.registry import registry
    except:
        return json.dumps({"error": "Failed to import registry"})
    
    tools_info = {}
    for name, entry in registry._tools.items():
        tools_info[name] = {
            "toolset": entry.toolset,
            "description": entry.description[:100],
            "requires_env": entry.requires_env,
            "emoji": entry.emoji,
            "is_available": entry.check_fn() if entry.check_fn else True,
        }
    
    return json.dumps({
        "total_tools": len(tools_info),
        "tools": tools_info,
        "toolsets": list(set(t['toolset'] for t in tools_info.values())),
    }, indent=2, default=str)


# Register the create_tool and list tools
registry.register(
    name="create_tool",
    toolset="custom",
    schema={
        "name": "create_tool",
        "description": "Create a new custom tool at runtime with governance checks. "
                       "Write the Python tool code with registry.register() call. "
                       "Returns governance results and registration status.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Tool name (used as filename, e.g. 'weather' creates tools/weather.py)"
                },
                "code": {
                    "type": "string",
                    "description": "Complete Python tool code with function definition and registry.register() call"
                },
                "toolset": {
                    "type": "string",
                    "description": "Toolset name to register under (default: 'custom')"
                },
                "schema_description": {
                    "type": "string",
                    "description": "Brief description of what the tool does (used in schema)"
                },
                "requires_env": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of required environment variable names"
                }
            },
            "required": ["name", "code"],
        },
    },
    handler=lambda args, **kw: create_tool(
        name=args.get("name", ""),
        code=args.get("code", ""),
        toolset=args.get("toolset", "custom"),
        schema_description=args.get("schema_description"),
        requires_env=args.get("requires_env"),
        task_id=kw.get("task_id"),
    ),
)


registry.register(
    name="list_tools",
    toolset="custom",
    schema={
        "name": "list_tools",
        "description": "List all currently registered tools and their availability status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=lambda args, **kw: list_available_tools(task_id=kw.get("task_id")),
)
