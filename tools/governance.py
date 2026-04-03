#!/usr/bin/env python3
"""
Hermes Tool Governance
======================
Inspired by AutoHarness (aiming-lab/AutoHarness, MIT License)
Adapted for Hermes agent architecture.

Provides risk classification, permission checking, and audit trail for
all tool calls made by the Hermes agent.

Architecture (adapted from AutoHarness 6-step pipeline):
  1. Parse/Validate    ->  2. Risk Classify  ->  3. Permission Check
  4. Execute           ->  5. Output Sanitize ->  6. Audit Log
"""

import json
import re
import os
import time
import uuid
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes-governance")

# ===========================================================================
# Enums (adapted from AutoHarness)
# ===========================================================================

class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class Decision(str, Enum):
    allow = "allow"
    deny = "deny"
    ask = "ask"

# ===========================================================================
# Risk Classification (adapted from AutoHarness RiskClassifier)
# Pure regex-based, sub-5ms latency, no LLM calls needed
# ===========================================================================

RISK_RULES = [
    # CRITICAL - destructive operations
    {"pattern": r"(rm\s+-rf|:(){:|:|:&;:})\s*/", "level": "critical", "reason": "Recursive destructive command targeting root"},
    {"pattern": r"dd\s+if=/dev/zero", "level": "critical", "reason": "Block device write (zeroing)"},
    {"pattern": r"sudo\s+(rm|chmod|chown|passwd|userdel|groupdel)", "level": "critical", "reason": "Privileged destructive operation"},
    
    # HIGH - system modification
    {"pattern": r"chmod\s+777", "level": "high", "reason": "World-writable permissions on critical system component"},
    {"pattern": r"mkfs\.", "level": "high", "reason": "Filesystem modification"},
    {"pattern": r"chmod\s+[0-9]*777", "level": "high", "reason": "World-writable permission"},
    {"pattern": r"(iptables|ufw|nft)\s+", "level": "high", "reason": "Firewall modification"},
    {"pattern": r"(export|echo)\s+\w*(?:KEY|TOKEN|SECRET|PASSWORD|API_KEY)\s*=", "level": "high", "reason": "Exporting credentials to environment"},
    {"pattern": r"curl.*\|\s*(?:bash|sh|python|python3|perl|ruby)", "level": "high", "reason": "Remote code execution via pipe"},
    
    # MEDIUM - information disclosure / moderate operations
    {"pattern": r"(cat|less|more|grep|find)\s+\S*/(shadow|gshadow|sudoers)", "level": "medium", "reason": "Accessing sensitive system file"},
    {"pattern": r"(pip|npm|apt|yum|dnf|cargo|conda)\s+(install|remove|uninstall)", "level": "medium", "reason": "Package modification"},
    {"pattern": r"crontab\s+-[er]|systemctl\s+(enable|disable|restart|stop)", "level": "medium", "reason": "Cron or service management"},
    {"pattern": r"(ssh|scp|rsync|sftp)\s+\S+@\S+:", "level": "medium", "reason": "Remote network access"},
    {"pattern": r"env\s*$|printenv|declare\s+-x", "level": "medium", "reason": "Environment dump"},
    {"pattern": r"(wget|curl)\s+https?://.*\.(?:sh|py|pl|rb|bash)$", "level": "medium", "reason": "Downloading and executing script"},
    
    # LOW - read-only and safe operations
    {"pattern": r"(ls|tree|du|df|free|uptime|ps|whoami|hostname|uname)\s*", "level": "low", "reason": "Read-only system inspection"},
    {"pattern": r"(cat|less|more|head|tail|grep|find|file|wc|stat)\s+", "level": "low", "reason": "File read operation"},
]

# Pre-compile all rules
_COMPILED_RULES = []
for rule in RISK_RULES:
    _COMPILED_RULES.append({
        "regex": re.compile(rule["pattern"], re.IGNORECASE),
        "level": RiskLevel(rule["level"]),
        "reason": rule["reason"],
    })

_LEVEL_ORDER = {RiskLevel.low: 0, RiskLevel.medium: 1, RiskLevel.high: 2, RiskLevel.critical: 3}

def classify_risk(tool_name: str, tool_input: str) -> Dict[str, Any]:
    """Classify a tool call by risk level using regex pattern matching.
    
    Returns: {"level": RiskLevel, "reasons": [...], "matched_rules": [...]}
    """
    text = f"{tool_name}({tool_input})" if tool_input else tool_name
    
    highest_level = RiskLevel.low
    reasons = []
    
    for rule in _COMPILED_RULES:
        if rule["regex"].search(text):
            if _LEVEL_ORDER[rule["level"]] > _LEVEL_ORDER[highest_level]:
                highest_level = rule["level"]
                reasons.append(rule["reason"])
    
    return {
        "level": highest_level,
        "reasons": reasons,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# ===========================================================================
# Permission Engine (adapted from AutoHarness PermissionEngine)
# ===========================================================================

HERMES_TOOL_PERMISSIONS = {
    "read_file": {"policy": "allow"},
    "search_files": {"policy": "allow"},
    "memory": {"policy": "allow"},
    "todo": {"policy": "allow"},
    "cronjob": {"policy": "allow"},
    "process": {"policy": "allow"},
    "vision_analyze": {"policy": "allow"},
    "image_generate": {"policy": "allow"},
    "text_to_speech": {"policy": "allow"},
    "skill_view": {"policy": "allow"},
    "skills_list": {"policy": "allow"},
    "session_search": {"policy": "allow"},
    "clarify": {"policy": "allow"},
    
    "terminal": {"policy": "allow", "risk_threshold": "high"},
    "execute_code": {"policy": "allow", "risk_threshold": "high"},
    "patch": {"policy": "allow", "risk_threshold": "high"},
    "write_file": {"policy": "allow", "risk_threshold": "high"},
    "delegate_task": {"policy": "allow", "risk_threshold": "high"},
    "skill_manage": {"policy": "allow", "risk_threshold": "high"},
    "mixture_of_agents": {"policy": "allow", "risk_threshold": "high"},
    
    "browser_navigate": {"policy": "allow", "risk_threshold": "medium"},
    "browser_click": {"policy": "allow", "risk_threshold": "medium"},
    "browser_type": {"policy": "allow", "risk_threshold": "medium"},
    "browser_snapshot": {"policy": "allow", "risk_threshold": "medium"},
    "browser_vision": {"policy": "allow", "risk_threshold": "medium"},
    "browser_back": {"policy": "allow", "risk_threshold": "medium"},
    "browser_scroll": {"policy": "allow", "risk_threshold": "medium"},
    "browser_press": {"policy": "allow", "risk_threshold": "medium"},
    "browser_get_images": {"policy": "allow", "risk_threshold": "medium"},
    "browser_console": {"policy": "allow", "risk_threshold": "medium"},
    "browser_close": {"policy": "allow", "risk_threshold": "medium"},
}

# Forbidden paths (never allow tool calls targeting these)
FORBIDDEN_PATHS = [
    r"/etc/shadow", r"/etc/gshadow", r"/etc/sudoers",
    r"\.ssh/authorized_keys", r"\.ssh/id_rsa",
    r"/proc/sys/", r"/sys/class/",
]

def check_permission(tool_name: str, tool_input: str, risk: Dict) -> Dict[str, Any]:
    """Check if a tool call should be allowed based on risk and permissions.
    
    Returns: {"decision": Decision, "reason": str, "risk_level": str}
    """
    perm = HERMES_TOOL_PERMISSIONS.get(tool_name, {})
    policy = perm.get("policy", "ask")
    risk_threshold = perm.get("risk_threshold", "critical")
    
    # Check forbidden paths
    for path_pattern in FORBIDDEN_PATHS:
        if tool_input and re.search(path_pattern, tool_input, re.IGNORECASE):
            return {
                "decision": Decision.deny,
                "reason": f"Forbidden path: {path_pattern}",
                "risk_level": "critical",
            }
    
    # Check risk against threshold
    risk_level = risk.get("level", RiskLevel.low)
    threshold = RiskLevel(risk_threshold)
    
    if _LEVEL_ORDER.get(risk_level, 0) > _LEVEL_ORDER.get(threshold, 3):
        return {
            "decision": Decision.ask,
            "reason": f"Risk {risk_level.value} exceeds threshold {threshold.value}",
            "risk_level": risk_level.value,
        }
    
    # Policy-based decisions
    if policy == "deny":
        return {"decision": Decision.deny, "reason": f"Tool {tool_name} denied", "risk_level": risk_level.value}
    if policy == "ask":
        return {"decision": Decision.ask, "reason": f"Tool {tool_name} requires confirmation", "risk_level": risk_level.value}
    
    # Allow with critical risk safety
    if risk_level == RiskLevel.critical and policy == "allow":
        return {"decision": Decision.ask, "reason": "Critical risk requires manual review", "risk_level": "critical"}
    
    return {"decision": Decision.allow, "reason": "Allowed by policy", "risk_level": risk_level.value}

# ===========================================================================
# Audit Engine (adapted from AutoHarness AuditEngine)
# ===========================================================================

class AuditLogger:
    """Thread-safe-ish JSONL audit logger for Hermes."""
    
    def __init__(self, path: str = "~/.hermes/audit.jsonl", enabled: bool = True, retention_days: int = 7):
        self._path = os.path.expanduser(path)
        self._enabled = enabled
        self._retention_days = retention_days
    
    def log(self, tool_name: str, tool_input: str, risk: Dict, permission: Dict,
            result: Optional[str] = None, error: Optional[str] = None):
        """Log a tool call and its governance decision."""
        if not self._enabled:
            return
        try:
            record = {
                "id": str(uuid.uuid4()),
                "ts": time.time(),
                "dt": datetime.now(timezone.utc).isoformat(),
                "tool": tool_name,
                "risk": risk.get("level", "unknown"),
                "decision": permission.get("decision", "unknown"),
                "reasons": risk.get("reasons", []),
                "perm_reason": permission.get("reason", ""),
            }
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, 'a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.debug(f"Audit log failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get audit statistics."""
        if not os.path.exists(self._path):
            return {"total": 0, "by_risk": {}, "by_decision": {}}
        try:
            with open(self._path) as f:
                records = [json.loads(l) for l in f if l.strip()]
            by_risk = {}
            by_decision = {}
            for r in records:
                risk = r.get("risk", "?")
                dec = r.get("decision", "?")
                by_risk[risk] = by_risk.get(risk, 0) + 1
                by_decision[dec] = by_decision.get(dec, 0) + 1
            return {"total": len(records), "by_risk": by_risk, "by_decision": by_decision}
        except Exception as e:
            return {"error": str(e)}

_audit = AuditLogger()

# ===========================================================================
# Governance Pipeline (adapted from AutoHarness ToolGovernancePipeline)
# ===========================================================================

def govern_tool_call(tool_name: str, tool_input: str) -> Dict[str, Any]:
    """Run through the 6-step governance pipeline.
    
    1. Parse & Validate -> 2. Risk Classify -> 3. Permission Check
    4. Execute (caller)  -> 5. Output Sanitize (caller) -> 6. Audit Log
    """
    # Step 1: Parse & Validate
    if not tool_name or not isinstance(tool_input, str):
        return {
            "allowed": False, "risk": {"level": "critical", "reasons": ["Invalid call"]},
            "permission": {"decision": "deny", "reason": "Malformed call"},
        }
    
    # Step 2: Risk Classify
    risk = classify_risk(tool_name, tool_input)
    
    # Step 3: Permission Check
    permission = check_permission(tool_name, tool_input, risk)
    allowed = permission["decision"] != Decision.deny
    
    # Step 6: Audit Log
    _audit.log(tool_name, tool_input, risk, permission)
    
    return {"allowed": allowed, "risk": risk, "permission": permission}

def govern_result(tool_name: str, tool_input: str, result: Optional[str] = None, error: Optional[str] = None):
    """Log the result of an executed tool call."""
    _audit.log(tool_name, tool_input, {"level": "unknown"}, {"decision": "allow"}, result, error)

def get_audit_stats() -> Dict[str, Any]:
    return _audit.get_stats()

def get_risk_rules() -> List[Dict]:
    return [{"pattern": r["regex"].pattern, "level": r["level"].value, "reason": r["reason"]} for r in _COMPILED_RULES]

def add_custom_rule(pattern: str, level: str, reason: str):
    """Add a custom risk rule at runtime."""
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
        _COMPILED_RULES.append({"regex": compiled, "level": RiskLevel(level), "reason": reason})
    except Exception as e:
        logger.error(f"Failed to add rule: {e}")
