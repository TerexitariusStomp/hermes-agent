#!/usr/bin/env python3
"""
Runtime Tool Creation - Adapted from 724-Office (MIT License)
Allows Hermes Agent to create new tools at runtime and register them dynamically.
"""

import os
import sys
import json
import re
import importlib.util
import logging
from datetime import datetime, timezone

logger = logging.getLogger("hermes-create-tool")

CUSTOM_TOOLS_DIR = os.path.expanduser("~/.hermes/hermes-agent/tools/custom_tools/")
os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)

_custom_tool_registry = {}

def register_tool(name, fn, description=None):
    """Register a custom tool in the runtime registry."""
    _custom_tool_registry[name] = {
        "fn": fn,
        "description": description or fn.__doc__ or "Custom runtime tool",
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Registered custom tool: {name}")

def create_tool(name, code, governance_check=True):
    """
    Create a new tool at runtime by writing Python code to a file and importing it.
    """
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        return {"status": "error", "message": f"Invalid tool name: {name}"}
    
    if governance_check:
        try:
            from tools.governance import classify_risk, check_permission
            risk = classify_risk("create_tool", code)
            perm = check_permission("create_tool", code, risk)
            if perm["decision"].value == "deny":
                return {"status": "denied", "reason": perm["reason"], "risk": risk}
        except ImportError:
            logger.warning("Governance module not available, skipping checks")
    
    dangerous = ["__import__", "exec(", "eval(", "os.system", "subprocess.Popen"]
    for pattern in dangerous:
        if pattern in code:
            return {"status": "error", "message": f"Code contains dangerous pattern: {pattern}"}
    
    tool_path = os.path.join(CUSTOM_TOOLS_DIR, f"{name}.py")
    
    boilerplate = """import sys
import os
sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))

def tool(tool_name, description, properties, required=None):
    def decorator(fn):
        fn._tool_name = tool_name
        fn._tool_description = description
        fn._tool_properties = properties
        fn._tool_required = required
        return fn
    return decorator
"""
    
    try:
        with open(tool_path, 'w') as f:
            f.write(boilerplate + code)
        
        with open(tool_path) as f:
            compile(f.read(), tool_path, 'exec')
        
        spec = importlib.util.spec_from_file_location(f"custom_tools.{name}", tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        found = False
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and hasattr(attr, '_tool_name'):
                register_tool(attr._tool_name, attr, attr._tool_description)
                found = True
                break
        
        if not found and hasattr(module, name):
            register_tool(name, getattr(module, name), "Custom tool")
        
        return {"status": "success", "tool_name": name, "file_path": tool_path}
        
    except SyntaxError as e:
        return {"status": "error", "message": f"Syntax error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create tool: {e}"}

def list_custom_tools():
    """List all registered custom tools."""
    return [{"name": n, "description": i["description"], "added_at": i["added_at"]}
            for n, i in _custom_tool_registry.items()]

def execute_custom_tool(name, args, ctx=None):
    """Execute a custom tool by name."""
    if name not in _custom_tool_registry:
        return {"error": f"Tool '{name}' not found"}
    try:
        result = _custom_tool_registry[name]["fn"](args, ctx or {})
        return {"result": result}
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}

def init_custom_tools():
    """Load all custom tools from the directory."""
    if not os.path.exists(CUSTOM_TOOLS_DIR):
        return
    for filename in os.listdir(CUSTOM_TOOLS_DIR):
        if filename.endswith('.py') and filename != '__init__.py':
            tool_name = filename[:-3]
            tool_path = os.path.join(CUSTOM_TOOLS_DIR, filename)
            try:
                spec = importlib.util.spec_from_file_location(f"custom_tools.{tool_name}", tool_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if callable(attr) and hasattr(attr, '_tool_name'):
                        register_tool(attr._tool_name, attr, attr._tool_description)
            except Exception as e:
                logger.error(f"Failed to load custom tool {tool_name}: {e}")

init_custom_tools()
