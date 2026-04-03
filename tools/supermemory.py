#!/usr/bin/env python3
"""
Supermemory-Inspired Memory System - Adapted for Hermes
Based on: https://github.com/supermemoryai/supermemory (MIT License)

Enhances Hermes memory with:
1. Fact extraction from sessions
2. User profile auto-maintenance
3. Hybrid search (RAG + personal memory)
4. Temporal memory handling (contradictions, forgetting)
5. Memory relevance scoring
"""

import os
import json
import time
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("hermes-supermemory")

HERMES_HOME = os.path.expanduser("~/.hermes")
MEMORY_DIR = os.path.join(HERMES_HOME, "supermemory_db")
os.makedirs(MEMORY_DIR, exist_ok=True)

# Storage files
FACTS_FILE = os.path.join(MEMORY_DIR, "facts.json")
PROFILE_FILE = os.path.join(MEMORY_DIR, "user_profile.json")
MEMORY_FILE = os.path.join(MEMORY_DIR, "memories.jsonl")
CONTRADICTIONS_FILE = os.path.join(MEMORY_DIR, "contradictions.json")

class MemoryEntry:
    """Single memory entry with temporal awareness."""
    def __init__(self, content: str, category: str = "", 
                 source: str = "", container_tag: str = "default",
                 importance: float = 1.0):
        self.id = str(int(time.time() * 1000))  # Unique ID
        self.content = content
        self.category = category  # preference, fact, project, context, tool
        self.source = source  # session, user, inferred
        self.container_tag = container_tag
        self.importance = importance  # 0.0-1.0
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.last_accessed = None
        self.access_count = 0
        self.contradicted = False
        self.contradicted_by = None
    
    def access(self):
        """Record access for relevance scoring."""
        self.last_accessed = datetime.now(timezone.utc).isoformat()
        self.access_count += 1
    
    def decay_score(self, days_since_creation: int) -> float:
        """Calculate current relevance score with temporal decay."""
        base = self.importance
        # Temporal decay: 5% per month
        decay = max(0.5, 1.0 - (days_since_creation / 365.0) * 0.3)
        # Access boost: accessed memories are more relevant
        access_boost = min(1.2, 1.0 + (self.access_count * 0.05))
        return base * decay * access_boost
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "source": self.source,
            "container_tag": self.container_tag,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "contradicted": self.contradicted,
            "contradicted_by": self.contradicted_by,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'MemoryEntry':
        entry = cls(d["content"], d.get("category", ""), d.get("source", ""),
                   d.get("container_tag", "default"), d.get("importance", 1.0))
        entry.id = d["id"]
        entry.created_at = d["created_at"]
        entry.updated_at = d.get("updated_at", d["created_at"])
        entry.last_accessed = d.get("last_accessed")
        entry.access_count = d.get("access_count", 0)
        entry.contradicted = d.get("contradicted", False)
        entry.contradicted_by = d.get("contradicted_by")
        return entry


class UserProfile:
    """Auto-maintained user profile - stable facts + recent activity."""
    def __init__(self):
        self.stable_facts: List[str] = []  # Long-term facts about user
        self.recent_activity: List[Dict] = []  # Recent context (last 30 days)
        self.preferences: Dict[str, str] = {}  # Key-value preferences
        self.projects: Dict[str, Dict] = {}  # Active projects and contexts
        self.updated_at = None
    
    def add_preference(self, key: str, value: str):
        """Add or update a user preference."""
        old = self.preferences.get(key)
        self.preferences[key] = value
        self.updated_at = datetime.now(timezone.utc).isoformat()
        
        # Check for contradiction
        if old and old != value:
            logger.info(f"Preference changed: {key}: '{old}' -> '{value}'")
    
    def add_stable_fact(self, fact: str):
        """Add a stable fact about the user."""
        if fact not in self.stable_facts:
            self.stable_facts.append(fact)
            self.updated_at = datetime.now(timezone.utc).isoformat()
    
    def add_recent_activity(self, activity: Dict):
        """Add recent activity (auto-aged out after 30 days)."""
        activity["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.recent_activity.append(activity)
        
        # Keep only last 30 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.recent_activity = [
            a for a in self.recent_activity 
            if a.get("timestamp", "") > cutoff
        ]
        self.updated_at = datetime.now(timezone.utc).isoformat()
    
    def get_summary(self) -> str:
        """Get a formatted profile summary."""
        parts = []
        if self.preferences:
            parts.append("## Preferences\n" + "\n".join(
                f"- {k}: {v}" for k, v in self.preferences.items()))
        if self.stable_facts:
            parts.append("## Stable Facts\n" + "\n".join(
                f"- {f}" for f in self.stable_facts))
        if self.projects:
            parts.append("## Projects\n" + "\n".join(
                f"- {name}: {info}" for name, info in self.projects.items()))
        if self.recent_activity:
            parts.append("## Recent Activity (last 30 days)\n" + "\n".join(
                f"- {a['timestamp'][:16]}: {a.get('summary', a.get('content', ''))[:100]}"
                for a in self.recent_activity[-10:]))
        return "\n---\n".join(parts) if parts else ""
    
    def to_dict(self) -> Dict:
        return {
            "stable_facts": self.stable_facts,
            "recent_activity": self.recent_activity[-50:],
            "preferences": self.preferences,
            "projects": self.projects,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'UserProfile':
        profile = cls()
        profile.stable_facts = d.get("stable_facts", [])
        profile.recent_activity = d.get("recent_activity", [])
        profile.preferences = d.get("preferences", {})
        profile.projects = d.get("projects", {})
        profile.updated_at = d.get("updated_at")
        return profile


class SuperMemory:
    """Supermemory-enhanced memory system for Hermes."""
    
    def __init__(self):
        self._facts: List[MemoryEntry] = []
        self._profile = UserProfile()
        self._memory_log = []
        self._contradictions: List[Dict] = []
        self._load_all()
    
    def _load_all(self):
        """Load all memory data from disk."""
        # Load facts
        if os.path.exists(FACTS_FILE):
            try:
                with open(FACTS_FILE) as f:
                    data = json.load(f)
                self._facts = [MemoryEntry.from_dict(d) for d in data]
                logger.info(f"[supermemory] loaded {len(self._facts)} facts")
            except Exception as e:
                logger.error(f"[supermemory] failed to load facts: {e}")
        
        # Load profile
        if os.path.exists(PROFILE_FILE):
            try:
                with open(PROFILE_FILE) as f:
                    self._profile = UserProfile.from_dict(json.load(f))
                logger.info("[supermemory] loaded user profile")
            except Exception as e:
                logger.error(f"[supermemory] failed to load profile: {e}")
        
        # Load contradictions
        if os.path.exists(CONTRADICTIONS_FILE):
            try:
                with open(CONTRADICTIONS_FILE) as f:
                    self._contradictions = json.load(f)
            except:
                self._contradictions = []
        
        # Load memory log (last 1000 entries)
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE) as f:
                    lines = [json.loads(l) for l in f if l.strip()]
                self._memory_log = lines[-1000:]
            except:
                self._memory_log = []
    
    def save_facts(self):
        """Persist facts to disk."""
        try:
            data = [f.to_dict() for f in self._facts]
            with open(FACTS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[supermemory] save failed: {e}")
    
    def save_profile(self):
        """Persist user profile to disk."""
        try:
            with open(PROFILE_FILE, 'w') as f:
                json.dump(self._profile.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"[supermemory] profile save failed: {e}")
    
    def add_memory(self, content: str, category: str = "fact",
                   source: str = "session", container_tag: str = "default",
                   importance: float = 1.0) -> str:
        """Add a memory entry (fact extraction)."""
        entry = MemoryEntry(content, category, source, container_tag, importance)
        self._facts.append(entry)
        self.save_facts()
        
        # Log
        self._memory_log.append({
            "action": "add",
            "entry": entry.to_dict(),
            "timestamp": entry.created_at,
        })
        with open(MEMORY_FILE, 'a') as f:
            f.write(json.dumps({"action": "add", "content": content[:200]}) + '\n')
        
        # Auto-extract preference if pattern matches
        self._extract_preference(content)
        
        return entry.id
    
    def _extract_preference(self, text: str):
        """Auto-extract preferences from text."""
        # Pattern: "X prefers Y" or "X likes Y" or "X uses Y"
        patterns = [
            (r"\b(prefer|like|love|use|need|want)s?\s+(?:to\s+)?(?:the\s+)?(\w+(?:\s+\w+){0,5})", "preference"),
            (r"\b(always|never|usually|often)\s+(\w+(?:\s+\w+){0,5})", "habit"),
            (r"\b(my|the)\s+(?:favorite|preferred|default)\s+(\w+)\s+(?:is|was)\s+(\w+(?:\s+\w+){0,5})", "favorite"),
        ]
        for pattern, ptype in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                key = f"{ptype}_{match.group(1).lower().strip()}"
                value = " ".join(g for g in match.groups() if g).lower().strip()
                self._profile.add_preference(key, value)
                self.save_profile()
                break
    
    def search_memories(self, query: str, top_k: int = 10,
                       category: str = None, container_tag: str = None) -> List[Dict]:
        """Hybrid search: relevance scoring + temporal awareness + filtering."""
        candidates = []
        now = datetime.now(timezone.utc)
        
        # Text relevance scoring (TF-IDF-like)
        query_words = set(query.lower().split())
        
        for fact in self._facts:
            if fact.contradicted:
                continue
            if category and fact.category != category:
                continue
            if container_tag and fact.container_tag != container_tag:
                continue
            
            # Text relevance
            text_words = set(fact.content.lower().split())
            overlap = len(query_words & text_words) / max(len(query_words), 1)
            
            # Temporal decay
            created = datetime.fromisoformat(fact.created_at)
            days_old = (now - created).days
            decay_score = fact.decay_score(days_old)
            
            # Combined score
            relevance = (overlap * 0.6 + decay_score * 0.4)
            
            if relevance > 0.1:  # Minimum relevance threshold
                fact.access()
                candidates.append((relevance, fact))
        
        # Sort by relevance
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Return top_k
        results = []
        for score, fact in candidates[:top_k]:
            result = fact.to_dict()
            result["relevance_score"] = round(score, 3)
            results.append(result)
        
        self.save_facts()  # Save access counts
        return results
    
    def inject_context(self, max_chars: int = 2000) -> str:
        """Inject full profile + relevant memories into context."""
        parts = []
        
        # User profile summary
        profile = self._profile.get_summary()
        if profile:
            parts.append(f"# User Profile\n{profile}")
        
        # Top memories
        top_memories = self.search_memories("active current ongoing", top_k=5)
        if top_memories:
            parts.append("# Recent Relevant Context")
            for m in top_memories[:5]:
                parts.append(f"- [{m.get('category', 'fact')}] {m['content']} (score: {m.get('relevance_score', 0):.2f})")
        
        result = "\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... [context truncated]"
        
        return result
    
    def check_contradictions(self, new_content: str) -> List[Dict]:
        """Check if new content contradicts existing facts."""
        contradictions = []
        now = datetime.now(timezone.utc)
        
        for fact in self._facts:
            if fact.contradicted:
                continue
            # Simple contradiction detection: similar text with different meaning
            new_words = set(new_content.lower().split())
            fact_words = set(fact.content.lower().split())
            
            # If significant overlap but contains negation
            overlap = len(new_words & fact_words)
            if overlap > 3:
                has_negation = any(w in new_content.lower() for w in ["not", "never", "changed", "no longer", "now"])
                if has_negation:
                    contradictions.append({
                        "existing": fact.content,
                        "new": new_content,
                        "overlap": overlap,
                        "timestamp": now.isoformat(),
                    })
                    fact.contradicted = True
                    fact.contradicted_by = new_content[:200]
                    self._contradictions.append({
                        "existing": fact.to_dict(),
                        "new": new_content,
                        "timestamp": now.isoformat(),
                    })
        
        if contradictions:
            self.save_facts()
            with open(CONTRADICTIONS_FILE, 'w') as f:
                json.dump(self._contradictions, f, indent=2)
        
        return contradictions
    
    def forget_old_memories(self, older_than_days: int = 180, min_importance: float = 0.3):
        """Automatic forgetting of old, low-importance memories."""
        now = datetime.now(timezone.utc)
        before = len(self._facts)
        
        self._facts = [
            f for f in self._facts
            if f.importance < min_importance and
            (now - datetime.fromisoformat(f.created_at)).days < older_than_days
        ]
        
        removed = before - len(self._facts)
        if removed > 0:
            logger.info(f"[supermemory] forgot {removed} old memories")
            self.save_facts()
        
        return removed
    
    def get_stats(self) -> Dict:
        """Get memory system statistics."""
        now = datetime.now(timezone.utc)
        categories = {}
        for f in self._facts:
            cat = f.category or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1
        
        return {
            "total_facts": len(self._facts),
            "contradictions": sum(1 for f in self._facts if f.contradicted),
            "categories": categories,
            "profile_preferences": len(self._profile.preferences),
            "profile_facts": len(self._profile.stable_facts),
            "memory_log_entries": len(self._memory_log),
            "recent_activity": len(self._profile.recent_activity),
        }


# Singleton instance
_memory = None

def _get_memory() -> SuperMemory:
    global _memory
    if _memory is None:
        _memory = SuperMemory()
    return _memory

def add_memory(content, category="fact", source="session", container_tag="default", importance=1.0):
    return _get_memory().add_memory(content, category, source, container_tag, importance)

def search_memories(query, top_k=10, category=None, container_tag=None):
    return _get_memory().search_memories(query, top_k, category, container_tag)

def inject_context(max_chars=2000):
    return _get_memory().inject_context(max_chars)

def check_contradictions(new_content):
    return _get_memory().check_contradictions(new_content)

def forget_old_memories(older_than_days=180, min_importance=0.3):
    return _get_memory().forget_old_memories(older_than_days, min_importance)

def get_stats():
    return _get_memory().get_stats()

def get_profile():
    return _get_memory()._profile
