#!/usr/bin/env python3
"""
Context Database - Inspired by OpenViking (volcengine/OpenViking)
AGPL-3.0 License (reference only - this implementation is standalone)

Adapts OpenViking's "Context Database" paradigm for Hermes:
1. L0/L1/L2 tiered context loading (abstract → overview → detail)
2. Unified context model with URI-based addressing
3. Filesystem-like organization of memories, resources, and skills
4. Directory recursive retrieval with semantic search
5. Automatic session management with memory compression
"""

import os
import json
import time
import re
import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger("hermes-context-db")

HERMES_HOME = os.path.expanduser("~/.hermes")
CONTEXT_DB_DIR = os.path.join(HERMES_HOME, "context_db")
os.makedirs(CONTEXT_DB_DIR, exist_ok=True)

# Storage
CONTEXTS_FILE = os.path.join(CONTEXT_DB_DIR, "contexts.jsonl")
DIRECTORY_FILE = os.path.join(CONTEXT_DB_DIR, "directories.json")
SESSIONS_DIR = os.path.join(CONTEXT_DB_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


class ContextType(str, Enum):
    SKILL = "skill"
    MEMORY = "memory"
    RESOURCE = "resource"


class ContextLevel(int, Enum):
    ABSTRACT = 0  # L0: short summary
    OVERVIEW = 1  # L1: brief description
    DETAIL = 2    # L2: full content


class Category(str, Enum):
    PREFERENCES = "preferences"
    ENTITIES = "entities"
    EVENTS = "events"
    PATTERNS = "patterns"
    CASES = "cases"
    PROFILE = "profile"


class VikingContext:
    """
    Unified context object inspired by OpenViking's Context class.
    Every piece of information (memory, skill, resource) is a context with
    URI-based addressing, hierarchical organization, and tiered abstraction.
    """
    def __init__(self, content: str, context_type: str = "memory",
                 category: str = "", level: int = 2, parent_uri: str = "",
                 container_tag: str = "default", importance: float = 1.0,
                 metadata: dict = None):
        self.id = str(uuid4())
        # URI scheme: viking://{scope}/{type}/{category}/{id}
        scope = "user" if container_tag == "default" else container_tag
        self.uri = f"viking://{scope}/{context_type}/{category}/{self.id[:12]}"
        self.parent_uri = parent_uri
        self.is_leaf = True
        self.content = content
        # Tiered content (L0 abstract, L1 overview, L2 detail)
        self.level = level
        self.abstract = content[:200]  # L0
        self.overview = content[:500] if len(content) > 200 else content  # L1
        self.context_type = context_type
        self.category = category
        self.container_tag = container_tag
        self.importance = importance
        self.metadata = metadata or {}
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at
        self.active_count = 0
        self.related_uris = []
        self.contradicted = False
        self.contradicted_by = None
        self.embedding = None

    def access(self):
        """Record access for relevance scoring."""
        self.active_count += 1
        self.updated_at = datetime.now(timezone.utc)

    def get_content_for_level(self, level: int = None) -> str:
        """Get content appropriate for the requested level."""
        lvl = level if level is not None else self.level
        if lvl == ContextLevel.ABSTRACT:
            return self.abstract
        elif lvl == ContextLevel.OVERVIEW:
            return self.overview
        return self.content

    def decay_score(self, days_old: int) -> float:
        """Relevance score with temporal decay."""
        base = self.importance
        decay = max(0.5, 1.0 - (days_old / 365.0) * 0.3)
        access_boost = min(1.2, 1.0 + (self.active_count * 0.05))
        return base * decay * access_boost

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "uri": self.uri,
            "parent_uri": self.parent_uri,
            "content": self.content,
            "abstract": self.abstract,
            "overview": self.overview,
            "level": self.level,
            "context_type": self.context_type,
            "category": self.category,
            "container_tag": self.container_tag,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "active_count": self.active_count,
            "related_uris": self.related_uris,
            "contradicted": self.contradicted,
            "contradicted_by": self.contradicted_by,
            "metadata": self.metadata,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'VikingContext':
        ctx = cls(
            d["content"], d.get("context_type", "memory"),
            d.get("category", ""), d.get("level", 2),
            d.get("parent_uri", ""), d.get("container_tag", "default"),
            d.get("importance", 1.0), d.get("metadata", {})
        )
        ctx.id = d["id"]
        ctx.uri = d["uri"]
        ctx.abstract = d.get("abstract", d["content"][:200])
        ctx.overview = d.get("overview", d["content"][:500])
        ctx.created_at = datetime.fromisoformat(d["created_at"])
        ctx.updated_at = datetime.fromisoformat(d["updated_at"])
        ctx.active_count = d.get("active_count", 0)
        ctx.related_uris = d.get("related_uris", [])
        ctx.contradicted = d.get("contradicted", False)
        ctx.contradicted_by = d.get("contradicted_by")
        ctx.embedding = d.get("embedding")
        return ctx


class DirectoryNode:
    """
    Hierarchical directory node inspired by OpenViking's directory structure.
    Organizes contexts in a filesystem-like tree with L0/L1/L2 descriptions.
    """
    def __init__(self, path: str, abstract: str, overview: str,
                 children: List['DirectoryNode'] = None):
        self.path = path
        self.abstract = abstract  # L0
        self.overview = overview  # L1
        self.children = children or []
        self.context_count = 0
        self.last_accessed = datetime.now(timezone.utc)

    def add_child(self, node: 'DirectoryNode'):
        self.children.append(node)

    def find_node(self, path: str) -> Optional['DirectoryNode']:
        """Find a node by relative path."""
        if self.path == path:
            return self
        for child in self.children:
            result = child.find_node(path)
            if result:
                return result
        return None

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "abstract": self.abstract,
            "overview": self.overview,
            "children": [c.to_dict() for c in self.children],
            "context_count": self.context_count,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'DirectoryNode':
        return cls(
            d["path"], d["abstract"], d["overview"],
            [cls.from_dict(c) for c in d.get("children", [])]
        )


class ContextDatabase:
    """
    Filesystem-inspired context database for Hermes.
    Inspired by OpenViking's "Context Database" paradigm.
    """
    def __init__(self):
        self._contexts: Dict[str, VikingContext] = {}
        self._directory: DirectoryNode = self._build_preset_directories()
        self._load_contexts()
        self._load_directory()

    def _build_preset_directories(self) -> DirectoryNode:
        """Build Hermes-adapted preset directory tree."""
        root = DirectoryNode("", "Hermes context root", "Root of all agent context")
        
        # User scope
        user = DirectoryNode("user", "User-level persistent context", 
                           "Long-term user context: profile, preferences, projects")
        user.add_child(DirectoryNode("user/preferences", 
            "User preferences by topic", 
            "Communication style, code standards, domain interests"))
        user.add_child(DirectoryNode("user/entities",
            "Entity memories from user's world",
            "Projects, people, organizations, concepts"))
        user.add_child(DirectoryNode("user/events",
            "Significant user events",
            "Sessions, milestones, important discussions"))
        user.add_child(DirectoryNode("user/profile",
            "User profile summary",
            "Identity, role, expertise, communication style"))
        root.add_child(user)

        # Session scope
        session = DirectoryNode("session", "Current session context",
                              "Temporary session data, compressed after use")
        session.add_child(DirectoryNode("session/memory",
            "Session-level memory entries",
            "Facts and context learned during current session"))
        session.add_child(DirectoryNode("session/tools",
            "Recently used tools and results",
            "Tool call history and outcomes"))
        root.add_child(session)

        # Agent scope
        agent = DirectoryNode("agent", "Agent-level context",
                            "Agent configuration, skills, and self-knowledge")
        agent.add_child(DirectoryNode("agent/skills",
            "Agent skill library",
            "All installed and active skills"))
        agent.add_child(DirectoryNode("agent/config",
            "Agent configuration",
            "Runtime settings, provider config, routing"))
        agent.add_child(DirectoryNode("agent/health",
            "Agent health metrics",
            "Quality tracking, evolution history, performance"))
        root.add_child(agent)

        # Resources scope
        resources = DirectoryNode("resources", "External resources",
                                "Documentation, references, knowledge base")
        resources.add_child(DirectoryNode("resources/docs",
            "Project documentation",
            "READMEs, SKILL.md files, architecture docs"))
        resources.add_child(DirectoryNode("resources/references",
            "Reference materials",
            "External knowledge, patterns, best practices"))
        root.add_child(resources)

        return root

    def _load_contexts(self):
        """Load all contexts from JSONL file."""
        if os.path.exists(CONTEXTS_FILE):
            try:
                with open(CONTEXTS_FILE) as f:
                    for line in f:
                        if line.strip():
                            try:
                                ctx = VikingContext.from_dict(json.loads(line))
                                self._contexts[ctx.id] = ctx
                            except:
                                continue
                logger.info(f"[context-db] loaded {len(self._contexts)} contexts")
            except Exception as e:
                logger.error(f"[context-db] load failed: {e}")

    def _load_directory(self):
        """Load directory tree from file."""
        if os.path.exists(DIRECTORY_FILE):
            try:
                with open(DIRECTORY_FILE) as f:
                    self._directory = DirectoryNode.from_dict(json.load(f))
                logger.info("[context-db] loaded directory tree")
            except Exception as e:
                logger.debug(f"[context-db] load dir failed: {e}")

    def save_contexts(self):
        """Persist all contexts to JSONL."""
        try:
            with open(CONTEXTS_FILE, 'w') as f:
                for ctx in self._contexts.values():
                    f.write(json.dumps(ctx.to_dict()) + '\n')
        except Exception as e:
            logger.error(f"[context-db] save failed: {e}")

    def save_directory(self):
        """Persist directory tree."""
        try:
            with open(DIRECTORY_FILE, 'w') as f:
                json.dump(self._directory.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"[context-db] save dir failed: {e}")

    def add_context(self, content: str, context_type: str = "memory",
                   category: str = "", parent_uri: str = "",
                   container_tag: str = "default", importance: float = 1.0,
                   metadata: dict = None) -> str:
        """Add a context entry to the database."""
        ctx = VikingContext(content, context_type, category, 
                          ContextLevel.DETAIL, parent_uri,
                          container_tag, importance, metadata)
        self._contexts[ctx.id] = ctx
        self.save_contexts()

        # Update directory counts
        scope = "user" if container_tag == "default" else container_tag
        dir_path = f"{scope}/{context_type}"
        node = self._directory.find_node(dir_path)
        if node:
            node.context_count += 1
            self.save_directory()

        return ctx.id

    def search(self, query: str, top_k: int = 10,
               context_type: str = None, category: str = None,
               container_tag: str = None, min_level: int = 0) -> List[Dict]:
        """
        Hybrid search: text relevance + temporal decay + hierarchical filtering.
        Returns contexts at the minimum requested detail level.
        """
        query_words = set(query.lower().split())
        now = datetime.now(timezone.utc)
        candidates = []

        for ctx_id, ctx in self._contexts.items():
            if ctx.contradicted:
                continue
            if context_type and ctx.context_type != context_type:
                continue
            if category and ctx.category != category:
                continue
            if container_tag and ctx.container_tag != container_tag:
                continue

            # Text relevance
            text_words = set(ctx.content.lower().split())
            overlap = len(query_words & text_words) / max(len(query_words), 1)
            
            # Also search abstract/overview for L0/L1 matches
            abstract_words = set(ctx.abstract.lower().split())
            overview_words = set(ctx.overview.lower().split())
            abs_match = len(query_words & abstract_words) / max(len(query_words), 1)
            ovw_match = len(query_words & overview_words) / max(len(query_words), 1)
            
            # Combined relevance
            text_rel = max(overlap, abs_match * 1.2, ovw_match * 1.1)

            # Temporal decay
            days_old = (now - ctx.created_at).days
            decay = ctx.decay_score(days_old)

            # Combined score
            relevance = text_rel * 0.6 + decay * 0.4
            
            if relevance > 0.1:
                ctx.access()
                result = ctx.to_dict()
                result["content"] = ctx.get_content_for_level(min_level)
                result["relevance_score"] = round(relevance, 3)
                candidates.append((relevance, result))

        candidates.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in candidates[:top_k]]
        
        if candidates:
            self.save_contexts()
        return results

    def inject_context(self, max_chars: int = 2000, min_level: int = 1) -> str:
        """
        Inject context into the system prompt using L0/L1/L2 tiered loading.
        Starts with L0 (abstracts), fills in L1 (overviews) and L2 (details) as needed.
        """
        now = datetime.now(timezone.utc)
        parts = []
        used_chars = 0

        # First: inject directory abstracts (L0) for overview
        parts.append("# Context Overview (L0)")
        lines = self._format_directory_l0()
        directory_text = '\n'.join(lines)
        if used_chars + len(directory_text) < max_chars:
            parts.append(directory_text)
            used_chars += len(lines)

        # Then: inject relevant contexts (L1 → L2 based on remaining space)
        remaining = max_chars - used_chars
        
        # Get all non-contradicted contexts sorted by relevance
        active = []
        for ctx_id, ctx in self._contexts.items():
            if ctx.contradicted:
                continue
            score = ctx.decay_score((now - ctx.created_at).days) * (1 + ctx.active_count * 0.05)
            active.append((score, ctx))
        
        active.sort(key=lambda x: x[0], reverse=True)

        for score, ctx in active[:20]:
            # Try to fit L1 overview first
            content = ctx.overview if ctx.level >= ContextLevel.OVERVIEW else ctx.content
            header = f"### [{ctx.context_type}/{ctx.category}] "
            text = header + content[:200]
            
            if used_chars + len(text) < max_chars:
                parts.append(text)
                used_chars += len(text)
            elif used_chars + len(header) + 50 < max_chars:
                # Fit what we can
                available = max_chars - used_chars - len(header)
                parts.append(header + content[:available])
                break
            else:
                break

        return '\n\n'.join(parts)

    def _format_directory_l0(self) -> List[str]:
        """Format directory tree with L0 abstracts."""
        lines = []
        self._format_node_l0(self._directory, lines, indent="")
        return lines

    def _format_node_l0(self, node: DirectoryNode, lines: List[str], indent: str):
        count_str = f" ({node.context_count})" if node.context_count > 0 else ""
        lines.append(f"{indent}``{node.path}``{count_str}: {node.abstract}")
        for child in node.children:
            self._format_node_l0(child, lines, indent + "  ")

    def check_contradictions(self, new_content: str) -> List[Dict]:
        """Check if new content contradicts existing contexts."""
        contradictions = []
        now = datetime.now(timezone.utc)
        new_words = set(new_content.lower().split())

        for ctx_id, ctx in self._contexts.items():
            if ctx.contradicted:
                continue
            
            # Significant overlap with potential negation
            ctx_words = set(ctx.content.lower().split())
            overlap = len(new_words & ctx_words)
            
            if overlap > 3:
                negation = any(w in new_content.lower() 
                              for w in ["not", "never", "no longer", "now", "changed"])
                if negation:
                    contradictions.append({
                        "existing_uri": ctx.uri,
                        "existing_content": ctx.content,
                        "new_content": new_content,
                        "overlap": overlap,
                        "timestamp": now.isoformat(),
                    })
                    ctx.contradicted = True
                    ctx.contradicted_by = new_content[:200]

        if contradictions:
            self.save_contexts()

        return contradictions

    def compress_session(self, session_id: str, messages: List[Dict]) -> str:
        """
        Compress a session into L0/L1 memory entries.
        Extracts key facts, preferences, and entities from conversation.
        """
        if not messages:
            return ""
        
        # Simple extraction - in production this would use LLM
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        
        compressed = {
            "session_id": session_id,
            "user_turns": len(user_msgs),
            "assistant_turns": len(assistant_msgs),
            "summary": "",
            "facts": [],
            "preferences": [],
            "entities": [],
        }
        
        # Extract key patterns
        for msg in user_msgs[:10]:  # Limit to first 10
            content = msg.get("content", "")
            if len(content) > 100:
                compressed["facts"].append(content[:200])
        
        compressed["summary"] = (
            f"Session {session_id}: {len(user_msgs)} user turns, "
            f"{len(assistant_msgs)} assistant turns"
        )
        
        return json.dumps(compressed)

    def forget_old_contexts(self, older_than_days: int = 180, 
                           min_importance: float = 0.3):
        """Auto-forget old, low-importance contexts."""
        now = datetime.now(timezone.utc)
        before = len(self._contexts)
        
        self._contexts = {
            k: v for k, v in self._contexts.items()
            if v.importance >= min_importance or
            (now - v.created_at).days < older_than_days
        }
        
        removed = before - len(self._contexts)
        if removed > 0:
            logger.info(f"[context-db] forgot {removed} old contexts")
            self.save_contexts()
        
        return removed

    def get_stats(self) -> Dict:
        """Get context database statistics."""
        by_type = {}
        by_category = {}
        by_level = {0: 0, 1: 0, 2: 0}
        contradicted = 0
        
        for ctx in self._contexts.values():
            by_type[ctx.context_type] = by_type.get(ctx.context_type, 0) + 1
            cat = ctx.category or "uncategorized"
            by_category[cat] = by_category.get(cat, 0) + 1
            by_level[ctx.level] = by_level.get(ctx.level, 0) + 1
            if ctx.contradicted:
                contradicted += 1
        
        return {
            "total_contexts": len(self._contexts),
            "by_type": by_type,
            "by_category": by_category,
            "by_level": {f"L{k}": v for k, v in by_level.items()},
            "contradicted": contradicted,
            "directory_nodes": self._count_nodes(self._directory),
        }

    def _count_nodes(self, node: DirectoryNode) -> int:
        count = 1
        for child in node.children:
            count += self._count_nodes(child)
        return count

    def tree(self, uri_prefix: str = "", max_depth: int = 3) -> str:
        """Print directory tree with context counts."""
        lines = []
        node = self._directory
        
        if uri_prefix:
            path = uri_prefix.replace("viking://", "").rstrip("/")
            found = node.find_node(path)
            if found:
                node = found
        
        def _tree(n: DirectoryNode, prefix: str, depth: int):
            if depth > max_depth:
                return
            count = f" ({n.context_count} contexts)" if n.context_count > 0 else ""
            lines.append(f"{prefix}{n.path}{count}")
            for i, child in enumerate(n.children):
                is_last = i == len(n.children) - 1
                connector = "\\-- " if is_last else "|-- "
                extension = "    " if is_last else "|   "
                _tree(child, prefix + connector, depth + 1)
        
        _tree(node, "", 0)
        return '\n'.join(lines)


# Singleton instance with lazy loading
_db = None

def _get_db() -> ContextDatabase:
    global _db
    if _db is None:
        _db = ContextDatabase()
    return _db

def add_context(content, context_type="memory", category="", parent_uri="",
                container_tag="default", importance=1.0, metadata=None):
    return _get_db().add_context(content, context_type, category, parent_uri,
                                 container_tag, importance, metadata)

def search(query, top_k=10, context_type=None, category=None,
           container_tag=None, min_level=0):
    return _get_db().search(query, top_k, context_type, category,
                           container_tag, min_level)

def inject_context(max_chars=2000, min_level=1):
    return _get_db().inject_context(max_chars, min_level)

def check_contradictions(new_content):
    return _get_db().check_contradictions(new_content)

def forget_old_contexts(older_than_days=180, min_importance=0.3):
    return _get_db().forget_old_contexts(older_than_days, min_importance)

def compress_session(session_id, messages):
    return _get_db().compress_session(session_id, messages)

def get_stats():
    return _get_db().get_stats()

def tree(uri_prefix="", max_depth=3):
    return _get_db().tree(uri_prefix, max_depth)
