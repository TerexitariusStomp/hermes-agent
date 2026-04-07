"""
Analyze trajectory data and compute evolution fitness metrics.
Identifies timeout hotspots, error patterns, and session quality distributions.
"""
import json
from pathlib import Path
from collections import Counter

def analyze_trajectories(trajectory_path=None):
    """Analyze evolution trajectory data for fitness metrics.
    
    Args:
        trajectory_path: Path to trajectories.json (default: workspace location)
    
    Returns:
        Dict with analysis results including score components and recommendations
    """
    tpath = Path(trajectory_path) if trajectory_path else (
        Path.home() / ".hermes" / "workspace" / "evolution" / "trajectories.json"
    )
    
    if not tpath.exists():
        return {"error": f"Trajectory file not found: {tpath}"}
    
    with open(tpath) as f:
        trajectories = json.load(f)
    
    if not trajectories:
        return {"error": "No trajectories found"}
    
    # Signal aggregation
    total = len(trajectories)
    signals = {
        "n_turns": [],
        "n_errors": 0,
        "n_timeouts": 0,
        "submitted": 0,
        "tools_used": Counter(),
        "timeout_tools": Counter(),
        "repeated_commands": Counter(),
        "session_types": Counter(),
        "top_timeout_sessions": [],
        "top_error_sessions": [],
    }
    
    for sid, t in trajectories.items():
        s = t.get("signals", {})
        turns = s.get("n_turns", 0)
        signals["n_turns"].append(turns)
        signals["n_errors"] += s.get("n_errors", 0)
        signals["n_timeouts"] += s.get("n_timeouts", 0)
        if s.get("submitted"):
            signals["submitted"] += 1
        
        for tool, count in s.get("tools_used", {}).items():
            signals["tools_used"][tool] += count
            if s.get("n_timeouts", 0) > 0:
                signals["timeout_tools"][tool] += 1
        
        for cmd in s.get("repeated_commands", []):
            signals["repeated_commands"][cmd] += 1
        
        # Session classification
        timeouts = s.get("n_timeouts", 0)
        errors = s.get("n_errors", 0)
        if timeouts > 10:
            signals["session_types"]["heavy_timeout"] += 1
        elif timeouts > 0:
            signals["session_types"]["some_timeout"] += 1
        elif errors > 0:
            signals["session_types"]["error"] += 1
        elif turns <= 2:
            signals["session_types"]["trivial"] += 1
        else:
            signals["session_types"]["healthy"] += 1
    
    # Build metrics
    result = {
        "total_sessions": total,
        "avg_turns": sum(signals["n_turns"]) / len(signals["n_turns"]) if signals["n_turns"] else 0,
        "total_errors": signals["n_errors"],
        "total_timeouts": signals["n_timeouts"],
        "timeout_rate": signals["n_timeouts"] / total if total else 0,
        "error_rate": signals["n_errors"] / total if total else 0,
        "submission_rate": signals["submitted"] / total if total else 0,
        "session_types": dict(signals["session_types"]),
        "tool_usage": dict(signals["tools_used"].most_common(10)),
        "timeout_tool_usage": dict(signals["timeout_tools"].most_common(10)),
        "looping_commands": dict(signals["repeated_commands"].most_common(5)),
        "health_score": signals["session_types"].get("healthy", 0) / total if total else 0,
        "trivial_ratio": signals["session_types"].get("trivial", 0) / total if total else 0,
    }
    
    # Recommendations
    if result["trivial_ratio"] > 0.3:
        result["recommendations"] = [
            "Trivial sessions dilute scoring signal - filter ≤2 turn sessions before scoring",
            "Increase score computation to use top-quartile mean instead of simple average",
        ]
    if result["timeout_rate"] > 0.25:
        result["recommendations"].extend([
            f"Timeout rate {result['timeout_rate']:.0%} exceeds 25% threshold",
            "Add timeout budgets to terminal calls in scripts",
        ])
    
    return result


import hermes_tools
hermes_tools.registry.register(
    name="trajectory_analyze",
    description="Analyze evolution trajectory data for fitness metrics, timeout patterns, and session quality",
    parameters={
        "type": "object",
        "properties": {
            "trajectory_path": {"type": "string", "description": "Path to trajectories.json"}
        },
        "required": []
    },
    fn=analyze_trajectories
)
