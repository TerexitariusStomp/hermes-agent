#!/usr/bin/env python3
"""
Skill Evolver - Adapted from OpenSpace (HKUDS/OpenSpace, MIT License)
Three-tier evolution: FIX (repair broken), DERIVED (enhanced version), CAPTURED (new pattern)
"""

import os
import json
import shutil
import re
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("hermes-skill-evolver")

HERMES_HOME = os.path.expanduser("~/.hermes")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
EVOLUTION_LOG = os.path.join(HERMES_HOME, "evolution_log.jsonl")

class EvolutionType:
    FIX = "FIX"          # Repair broken/outdated instructions in-place
    DERIVED = "DERIVED"  # Create enhanced version from existing skill
    CAPTURED = "CAPTURED" # Capture novel reusable pattern


def _sanitize_skill_name(name: str) -> str:
    """Enforce naming rules: lowercase, hyphens only, max 50 chars."""
    clean = re.sub(r"[^a-z0-9\-]", "-", name.lower().strip())
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    if len(clean) > 50:
        truncated = clean[:50]
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 25:
            truncated = truncated[:last_hyphen]
        clean = truncated.strip("-")
    return clean


class SkillEvolver:
    """Execute skill evolution actions."""
    
    def __init__(self):
        self.evolution_history = []
        self._load_history()
    
    def _load_history(self):
        if os.path.exists(EVOLUTION_LOG):
            try:
                with open(EVOLUTION_LOG) as f:
                    self.evolution_history = [json.loads(l) for l in f if l.strip()]
            except:
                self.evolution_history = []
    
    def _log_evolution(self, entry: Dict):
        self.evolution_history.append(entry)
        with open(EVOLUTION_LOG, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def fix_skill(self, skill_name: str, current_content: str, 
                 fix_content: str, reason: str = "") -> Dict[str, Any]:
        """Fix a skill in-place (same name, updated content)."""
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            return {"status": "error", "message": f"Skill '{skill_name}' not found"}
        
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if not os.path.exists(skill_file):
            return {"status": "error", "message": f"SKILL.md not found in {skill_dir}"}
        
        # Backup current version
        backup = os.path.join(skill_dir, f"SKILL.md.bak.{int(datetime.now().timestamp())}")
        shutil.copy2(skill_file, backup)
        
        # Apply fix
        with open(skill_file, 'w') as f:
            f.write(fix_content)
        
        # Update frontmatter version
        new_content = self._increment_version(fix_content)
        with open(skill_file, 'w') as f:
            f.write(new_content)
        
        entry = {
            "type": EvolutionType.FIX,
            "skill_name": skill_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "backup": backup,
        }
        self._log_evolution(entry)
        
        return {"status": "success", "evolution": entry}
    
    def derive_skill(self, source_skill: str, new_name: str, 
                    new_content: str, reason: str = "") -> Dict[str, Any]:
        """Create a derived/enhanced version of an existing skill."""
        source_dir = self._find_skill_dir(source_skill)
        if not source_dir:
            return {"status": "error", "message": f"Skill '{source_skill}' not found"}
        
        new_name_clean = _sanitize_skill_name(new_name)
        new_dir = os.path.join(SKILLS_DIR, new_name_clean)
        
        # Copy source directory
        if os.path.exists(new_dir):
            return {"status": "error", "message": f"Skill '{new_name_clean}' already exists"}
        
        shutil.copytree(source_dir, new_dir)
        
        # Update SKILL.md with new content
        skill_file = os.path.join(new_dir, "SKILL.md")
        new_content = self._increment_version(new_content)
        with open(skill_file, 'w') as f:
            f.write(new_content)
        
        entry = {
            "type": EvolutionType.DERIVED,
            "source_skill": source_skill,
            "new_skill": new_name_clean,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        self._log_evolution(entry)
        
        return {"status": "success", "evolution": entry}
    
    def capture_skill(self, skill_name: str, skill_content: str,
                     category: str = "", reason: str = "") -> Dict[str, Any]:
        """Capture a novel reusable pattern as a new skill."""
        name_clean = _sanitize_skill_name(skill_name)
        skill_dir = os.path.join(SKILLS_DIR, name_clean)
        
        if os.path.exists(skill_dir):
            return {"status": "error", "message": f"Skill '{name_clean}' already exists"}
        
        os.makedirs(skill_dir, exist_ok=True)
        
        # Write SKILL.md
        skill_file = os.path.join(skill_dir, "SKILL.md")
        with open(skill_file, 'w') as f:
            f.write(skill_content)
        
        entry = {
            "type": EvolutionType.CAPTURED,
            "skill_name": name_clean,
            "category": category,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        self._log_evolution(entry)
        
        return {"status": "success", "evolution": entry}
    
    def _find_skill_dir(self, skill_name: str) -> Optional[str]:
        """Find the directory for a skill by name."""
        if not os.path.exists(SKILLS_DIR):
            return None
        
        for root, dirs, files in os.walk(SKILLS_DIR):
            if "SKILL.md" in files and os.path.basename(root) == skill_name:
                return root
            # Check nested categories
            for d in dirs:
                nested = os.path.join(root, d, "SKILL.md")
                if os.path.exists(nested) and d == skill_name:
                    return os.path.join(root, d)
        
        return None
    
    def _increment_version(self, content: str) -> str:
        """Increment version field in frontmatter."""
        version_pattern = r"(^version:\s*)(\d+\.\d+\.\d+)"
        match = re.search(version_pattern, content, re.MULTILINE)
        if match:
            current = match.group(2)
            parts = current.split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            new_version = ".".join(parts)
            content = re.sub(version_pattern, r"\1" + new_version, content, count=1)
        else:
            # Add version if missing
            if content.startswith("---"):
                content = content.replace("---", f"---\nversion: 1.0.0", 1)
        return content
    
    def get_evolution_history(self, limit: int = 20) -> List[Dict]:
        return self.evolution_history[-limit:] if self.evolution_history else []
    
    def get_evolution_stats(self) -> Dict[str, Any]:
        total = len(self.evolution_history)
        fixes = sum(1 for e in self.evolution_history if e.get("type") == EvolutionType.FIX)
        derived = sum(1 for e in self.evolution_history if e.get("type") == EvolutionType.DERIVED)
        captured = sum(1 for e in self.evolution_history if e.get("type") == EvolutionType.CAPTURED)
        
        return {
            "total_evolutions": total,
            "fixes": fixes,
            "derived": derived,
            "captured": captured,
        }


# Lazy singleton
_evolver = None

def _get_evolver():
    global _evolver
    if _evolver is None:
        _evolver = SkillEvolver()
    return _evolver

def fix_skill(skill_name, current_content, fix_content, reason=""):
    return _get_evolver().fix_skill(skill_name, current_content, fix_content, reason)

def derive_skill(source_skill, new_name, new_content, reason=""):
    return _get_evolver().derive_skill(source_skill, new_name, new_content, reason)

def capture_skill(skill_name, skill_content, category="", reason=""):
    return _get_evolver().capture_skill(skill_name, skill_content, category, reason)

def get_evolution_history(limit=20):
    return _get_evolver().get_evolution_history(limit)

def get_evolution_stats():
    return _get_evolver().get_evolution_stats()
