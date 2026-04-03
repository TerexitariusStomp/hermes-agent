#!/usr/bin/env python3
"""
Execution Analyzer - Adapted from OpenSpace (HKUDS/OpenSpace, MIT License)
Analyzes task execution results, tracks skill quality, and identifies evolution candidates.
"""

import os
import json
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from collections import defaultdict

HERMES_HOME = os.path.expanduser("~/.hermes")

def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (compact DP)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1))
        prev = curr
    return prev[-1]

def _correct_skill_ids(ids: List[str], known_ids: set) -> List[str]:
    """Best-effort correction of LLM-hallucinated skill IDs."""
    if not known_ids:
        return ids
    
    corrected = []
    for raw_id in ids:
        if raw_id in known_ids:
            corrected.append(raw_id)
            continue
        
        prefix = raw_id.split("__")[0] if "__" in raw_id else ""
        candidates = [k for k in known_ids if prefix and k.split("__")[0] == prefix]
        
        best, best_dist = None, 4
        for cand in candidates:
            d = _edit_distance(raw_id, cand)
            if d < best_dist:
                best, best_dist = cand, d
        
        if best is not None:
            corrected.append(best)
        else:
            corrected.append(raw_id)
    
    return corrected

class ExecutionAnalysis:
    """Result of execution analysis."""
    def __init__(self, task_id: str, task_completed: bool, 
                 skill_judgments: List[Dict], tool_issues: List[str],
                 evolution_suggestions: List[Dict]):
        self.task_id = task_id
        self.task_completed = task_completed
        self.skill_judgments = skill_judgments
        self.tool_issues = tool_issues
        self.evolution_suggestions = evolution_suggestions
        self.timestamp = datetime.now(timezone.utc).isoformat()

class ExecutionAnalyzer:
    """Analyzes task execution results and tracks skill evolution candidates."""
    
    def __init__(self, store_path: str = None):
        self.store_path = store_path or os.path.join(HERMES_HOME, "execution_analyses.jsonl")
        self._analyses = []
        self._load_analyses()
    
    def _load_analyses(self):
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path) as f:
                    self._analyses = [json.loads(l) for l in f if l.strip()]
            except Exception as e:
                self._analyses = []
    
    def _save_analysis(self, analysis: Dict):
        with open(self.store_path, 'a') as f:
            f.write(json.dumps(analysis) + '\n')
    
    def analyze_session(self, session_id: str, session_data: Dict) -> Optional[Dict]:
        """Analyze a completed session for skill/tool issues.
        
        Args:
            session_id: The session identifier
            session_data: Session data from state.db (tool calls, results, errors)
        
        Returns:
            Analysis dict with findings, or None if no issues found
        """
        tool_calls = session_data.get("tool_calls", [])
        errors = session_data.get("errors", [])
        user_corrections = session_data.get("corrections", [])
        
        if not tool_calls and not errors:
            return None
        
        # Analyze per-tool success rates
        tool_stats = defaultdict(lambda: {"success": 0, "error": 0, "errors": []})
        for call in tool_calls:
            tool_name = call.get("tool", "unknown")
            status = call.get("status", "unknown")
            error = call.get("error")
            
            if status == "success" or not error:
                tool_stats[tool_name]["success"] += 1
            else:
                tool_stats[tool_name]["error"] += 1
                if error:
                    tool_stats[tool_name]["errors"].append(str(error)[:200])
        
        # Identify problematic tools
        tool_issues = []
        for tool, stats in tool_stats.items():
            total = stats["success"] + stats["error"]
            if total >= 2 and stats["error"] > 0:
                error_rate = stats["error"] / total
                if error_rate > 0.3:
                    tool_issues.append({
                        "tool": tool,
                        "error_rate": round(error_rate * 100, 1),
                        "errors": stats["errors"][:3],
                        "total_calls": total,
                    })
        
        # Identify evolution candidates
        evolution_candidates = []
        for issue in tool_issues:
            evolution_candidates.append({
                "type": "FIX" if issue["error_rate"] < 60 else "DERIVED",
                "target": issue["tool"],
                "reason": f"Error rate {issue['error_rate']}% ({issue['total_calls']} calls)",
                "priority": "HIGH" if issue["error_rate"] > 50 else "MEDIUM",
            })
        
        analysis = {
            "task_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_issues": tool_issues,
            "evolution_candidates": evolution_candidates,
            "user_corrections": len(user_corrections),
            "skill_judgments": [],
        }
        
        # Save analysis
        self._save_analysis(analysis)
        
        return analysis
    
    def get_evolution_candidates(self, limit: int = 10) -> List[Dict]:
        """Get top evolution candidates from recent analyses."""
        candidates = []
        for analysis in reversed(self._analyses[-limit:]):
            candidates.extend(analysis.get("evolution_candidates", []))
        return candidates[:limit]
    
    def get_weakest_tools(self, limit: int = 5) -> List[Dict]:
        """Identify weakest tools based on recent analyses."""
        tool_stats = defaultdict(lambda: {"success": 0, "error": 0})
        
        for analysis in self._analyses:
            for issue in analysis.get("tool_issues", []):
                tool = issue.get("tool", "unknown")
                tool_stats[tool]["error"] += issue.get("total_calls", 0)
        
        # Sort by error count
        sorted_tools = sorted(tool_stats.items(), key=lambda x: x[1]["error"], reverse=True)
        return [{"tool": tool, "errors": stats["error"]} for tool, stats in sorted_tools[:limit]]


# Lazy singleton
_analyzer = None

def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        _analyzer = ExecutionAnalyzer()
    return _analyzer

def analyze_session(session_id, session_data):
    return _get_analyzer().analyze_session(session_id, session_data)

def get_evolution_candidates(limit=10):
    return _get_analyzer().get_evolution_candidates(limit)

def get_weakest_tools(limit=5):
    return _get_analyzer().get_weakest_tools(limit)
