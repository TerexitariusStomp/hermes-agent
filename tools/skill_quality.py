#!/usr/bin/env python3
"""
Skill Quality Tracker - Adapted from OpenSpace (HKUDS/OpenSpace, MIT License)
Tracks skill performance, error rates, execution success across all tasks.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict

logger = logging.getLogger("hermes-skill-quality")

HERMES_HOME = os.path.expanduser("~/.hermes")
SKILL_QUALITY_DIR = os.path.join(HERMES_HOME, "skill_quality")
os.makedirs(SKILL_QUALITY_DIR, exist_ok=True)

# Metrics file paths
METRICS_FILE = os.path.join(SKILL_QUALITY_DIR, "metrics.json")
TOOL_QUALITY_FILE = os.path.join(SKILL_QUALITY_DIR, "tool_quality.json")

class SkillQualityRecord:
    """Track quality metrics for a single skill."""
    def __init__(self, skill_id: str, skill_name: str):
        self.skill_id = skill_id
        self.skill_name = skill_name
        self.total_selections = 0
        self.total_applied = 0
        self.total_completions = 0
        self.total_fallbacks = 0
        self.total_evolution_suggestions = 0
        self.last_used = None
        self.last_evolved = None
        self.error_types = defaultdict(int)
        self.usage_history = []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "total_selections": self.total_selections,
            "total_applied": self.total_applied,
            "total_completions": self.total_completions,
            "total_fallbacks": self.total_fallbacks,
            "total_evolution_suggestions": self.total_evolution_suggestions,
            "last_used": self.last_used,
            "last_evolved": self.last_evolved,
            "error_types": dict(self.error_types),
            "usage_history": self.usage_history[-100:],  # Keep last 100
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillQualityRecord':
        rec = cls(data["skill_id"], data["skill_name"])
        rec.total_selections = data.get("total_selections", 0)
        rec.total_applied = data.get("total_applied", 0)
        rec.total_completions = data.get("total_completions", 0)
        rec.total_fallbacks = data.get("total_fallbacks", 0)
        rec.total_evolution_suggestions = data.get("total_evolution_suggestions", 0)
        rec.last_used = data.get("last_used")
        rec.last_evolved = data.get("last_evolved")
        rec.error_types = defaultdict(int, data.get("error_types", {}))
        rec.usage_history = data.get("usage_history", [])
        return rec


class SkillQualityTracker:
    """Track and analyze skill quality across all tasks."""
    
    def __init__(self, metrics_file: str = None):
        self._metrics_file = metrics_file or METRICS_FILE
        self._records: Dict[str, SkillQualityRecord] = {}
        self._load_metrics()
    
    def _load_metrics(self):
        """Load metrics from file."""
        if os.path.exists(self._metrics_file):
            try:
                with open(self._metrics_file) as f:
                    data = json.load(f)
                for item in data:
                    rec = SkillQualityRecord.from_dict(item)
                    self._records[rec.skill_id] = rec
                logger.info(f"[skill_quality] loaded {len(self._records)} skill records")
            except Exception as e:
                logger.error(f"[skill_quality] failed to load metrics: {e}")
    
    def save_metrics(self):
        """Save metrics to file."""
        try:
            data = [rec.to_dict() for rec in self._records.values()]
            with open(self._metrics_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[skill_quality] failed to save metrics: {e}")
    
    def record_skill_usage(self, skill_id: str, skill_name: str, 
                          success: bool = True, error: str = None,
                          context: str = None):
        """Record a skill usage event."""
        if skill_id not in self._records:
            self._records[skill_id] = SkillQualityRecord(skill_id, skill_name)
        
        rec = self._records[skill_id]
        rec.total_selections += 1
        rec.last_used = datetime.now(timezone.utc).isoformat()
        
        if success:
            rec.total_applied += 1
            rec.total_completions += 1
        else:
            rec.total_fallbacks += 1
            if error:
                error_type = error.split(":")[0].strip() if ":" in error else error[:50]
                rec.error_types[error_type] += 1
        
        # Record usage
        rec.usage_history.append({
            "timestamp": rec.last_used,
            "success": success,
            "error": error,
            "context": context,
        })
        
        # Keep history manageable
        if len(rec.usage_history) > 1000:
            rec.usage_history = rec.usage_history[-500:]
        
        self.save_metrics()
    
    def record_evolution(self, skill_id: str, skill_name: str, evolution_type: str):
        """Record a skill evolution event."""
        if skill_id not in self._records:
            self._records[skill_id] = SkillQualityRecord(skill_id, skill_name)
        
        rec = self._records[skill_id]
        rec.total_evolution_suggestions += 1
        rec.last_evolved = datetime.now(timezone.utc).isoformat()
        rec.usage_history.append({
            "timestamp": rec.last_evolved,
            "event": "evolution",
            "evolution_type": evolution_type,
        })
        
        self.save_metrics()
    
    def get_skill_health(self, skill_id: str) -> Dict[str, Any]:
        """Get health metrics for a specific skill."""
        rec = self._records.get(skill_id)
        if not rec:
            return {"error": f"Skill {skill_id} not found"}
        
        total = rec.total_selections
        success_rate = (rec.total_completions / total * 100) if total > 0 else 0
        error_rate = (rec.total_fallbacks / total * 100) if total > 0 else 0
        
        return {
            "skill_id": skill_id,
            "skill_name": rec.skill_name,
            "total_uses": total,
            "success_rate": round(success_rate, 1),
            "error_rate": round(error_rate, 1),
            "fallbacks": rec.total_fallbacks,
            "evolutions": rec.total_evolution_suggestions,
            "last_used": rec.last_used,
            "last_evolved": rec.last_evolved,
            "error_types": dict(rec.error_types),
        }
    
    def get_weakest_skills(self, min_uses: int = 3) -> List[Dict[str, Any]]:
        """Get skills with highest error rates (for prioritized improvement)."""
        health_list = []
        for skill_id in self._records:
            health = self.get_skill_health(skill_id)
            if health.get("total_uses", 0) >= min_uses:
                health["priority"] = health["error_rate"]
                health_list.append(health)
        
        # Sort by error rate (highest first)
        health_list.sort(key=lambda x: x["priority"], reverse=True)
        return health_list
    
    def get_summary(self) -> Dict[str, Any]:
        """Get overall quality summary."""
        total = len(self._records)
        total_uses = sum(r.total_selections for r in self._records.values())
        total_success = sum(r.total_completions for r in self._records.values())
        total_errors = sum(r.total_fallbacks for r in self._records.values())
        
        return {
            "total_skills_tracked": total,
            "total_uses": total_uses,
            "overall_success_rate": round((total_success / total_uses * 100) if total_uses > 0 else 0, 1),
            "overall_error_rate": round((total_errors / total_uses * 100) if total_uses > 0 else 0, 1),
            "weakest_skills": self.get_weakest_skills(3),
        }
    
    def analyze_for_evolution(self) -> List[Dict[str, Any]]:
        """Analyze which skills need evolution based on quality metrics."""
        candidates = []
        
        for skill_id, rec in self._records.items():
            total = rec.total_selections
            if total < 3:
                continue
            
            error_rate = rec.total_fallbacks / total if total > 0 else 0
            
            # High error rate
            if error_rate > 0.2:
                candidates.append({
                    "skill_id": skill_id,
                    "skill_name": rec.skill_name,
                    "error_rate": round(error_rate * 100, 1),
                    "total_uses": total,
                    "evolution_type": "FIX",
                    "reason": f"High error rate ({error_rate * 100:.1f}%)",
                })
            
            # Frequent evolution suggestions
            if rec.total_evolution_suggestions > 2:
                candidates.append({
                    "skill_id": skill_id,
                    "skill_name": rec.skill_name,
                    "evolution_count": rec.total_evolution_suggestions,
                    "evolution_type": "DERIVED",
                    "reason": f"Repeated evolution suggestions ({rec.total_evolution_suggestions})",
                })
        
        return candidates


# Lazy singleton instance
_tracker = None

def _get_tracker():
    global _tracker
    if _tracker is None:
        _tracker = SkillQualityTracker()
    return _tracker

def record_skill_usage(skill_id, skill_name, success=True, error=None, context=None):
    """Convenience function to record skill usage."""
    _get_tracker().record_skill_usage(skill_id, skill_name, success, error, context)

def record_evolution(skill_id, skill_name, evolution_type):
    """Convenience function to record skill evolution."""
    _get_tracker().record_evolution(skill_id, skill_name, evolution_type)

def get_skill_health(skill_id):
    return _get_tracker().get_skill_health(skill_id)

def get_weakest_skills(min_uses=3):
    return _get_tracker().get_weakest_skills(min_uses)

def get_summary():
    return _get_tracker().get_summary()

def analyze_for_evolution():
    return _get_tracker().analyze_for_evolution()
