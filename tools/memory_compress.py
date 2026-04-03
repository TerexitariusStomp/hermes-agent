#!/usr/bin/env python3
"""
Memory Compression & Deduplication - Adapted from 724-Office (MIT License)
Three-stage pipeline: Compress -> Deduplicate -> Retrieve
"""

import os
import json
import time
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

from tools.registry import registry

logger = logging.getLogger("hermes-memory-compress")

HERMES_HOME = os.path.expanduser("~/.hermes")
MEMORY_DB_DIR = os.path.join(HERMES_HOME, "memory_db")
os.makedirs(MEMORY_DB_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(MEMORY_DB_DIR, "compressed_memories.json")

SIMILARITY_THRESHOLD = 0.92
_memories = []
_enabled = False
_embedding_api_key = ""  # Set via embedding_cfg["api_key_env"] in init()
_embedding_base_url = ""
_embedding_model = ""


def init(config=None):
    global _enabled, _memories, _embedding_api_key, _embedding_base_url, _embedding_model
    if not config:
        config = {}
    _enabled = config.get("enabled", False)
    if not _enabled:
        logger.info("[memory_compress] disabled")
        return

    embedding_cfg = config.get("embedding_api", {})
    api_key_env = embedding_cfg.get("api_key_env", "OPENROUTER_API_KEY")
    _embedding_api_key = os.getenv(api_key_env, "")
    _embedding_base_url = embedding_cfg.get("api_base", "https://openrouter.ai/api/v1")
    _embedding_model = embedding_cfg.get("model", "nvidia/llama-nemotron-embed-vl-1b-v2:free")

    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                _memories = json.load(f)
            logger.info(f"[memory_compress] loaded {len(_memories)} memories")
        except Exception:
            _memories = []


def _cosine_similarity(v1, v2):
    if not v1 or not v2:
        return 0.0
    dot = sum(a*b for a, b in zip(v1, v2))
    n1 = sum(a*a for a in v1) ** 0.5
    n2 = sum(b*b for b in v2) ** 0.5
    return dot / (n1 * n2) if n1 and n2 else 0.0


def _get_embedding(text):
    if not _embedding_api_key:
        return None
    try:
        import httpx
        r = httpx.post(
            f"{_embedding_base_url}/embeddings",
            headers={"Authorization": f"Bearer {_embedding_api_key}", "Content-Type": "application/json"},
            json={"model": _embedding_model, "input": text},
            timeout=30
        )
        if r.status_code == 200:
            return r.json().get("data", [{}])[0].get("embedding", [])
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
    return None


def deduplicate_and_store(new_memories):
    if not _enabled or not new_memories:
        return {"added": 0, "skipped": 0}

    added = 0
    skipped = 0

    for mem in new_memories:
        vec = _get_embedding(mem.get("fact", ""))
        if not vec:
            _memories.append(mem)
            added += 1
            continue

        is_dup = False
        for existing in _memories:
            if "embedding" not in existing:
                continue
            sim = _cosine_similarity(vec, existing["embedding"])
            if sim > SIMILARITY_THRESHOLD:
                is_dup = True
                skipped += 1
                break

        if not is_dup:
            mem["embedding"] = vec
            _memories.append(mem)
            added += 1

    # Save without embeddings to keep file small
    clean = [{k: v for k, v in m.items() if k != "embedding"} for m in _memories]
    with open(MEMORY_FILE, 'w') as f:
        json.dump(clean, f, indent=2)

    return {"added": added, "skipped": skipped, "total": len(_memories)}


def retrieve(query, top_k=5):
    if not _enabled or not _memories:
        return []
    vec = _get_embedding(query)
    if not vec:
        fallback = _memories[:top_k]
        return fallback

    scored = []
    for m in _memories:
        if "embedding" not in m:
            sim = 0.5
        else:
            sim = _cosine_similarity(vec, m["embedding"])
        if sim > 0.1:
            scored.append((sim, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:top_k]]


# Auto-init
init({"enabled": True, "embedding_api": {"api_key_env": "OPENROUTER_API_KEY"}})


def _memory_compress_handler(args, task_id=None, **kwargs):
    """Handler for the compress_memories LLM tool."""
    facts = args.get("facts", [])
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except json.JSONDecodeError:
            facts = [{"fact": facts}]
    elif isinstance(facts, dict):
        facts = [facts]
    return json.dumps(deduplicate_and_store(facts))


def _memory_search_handler(args, task_id=None, **kwargs):
    """Handler for the search_compressed_memory LLM tool."""
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    try:
        results = retrieve(query, top_k=top_k)
    except (TypeError, AttributeError) as e:
        return json.dumps({"found": 0, "results": [], "error": str(e)})
    if not results:
        return json.dumps({"found": 0, "results": []})
    return json.dumps({"found": len(results), "results": results})


registry.register(
    name="compress_memories",
    toolset="diagnostics",
    schema={
        "name": "compress_memories",
        "description": "Compress and store extracted memories with deduplication. "
                       "Each memory should have a 'fact' field (the core fact), optionally 'keywords', "
                       "'persons', 'timestamp', 'topic'. Duplicates are skipped using cosine similarity.",
        "parameters": {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "description": "Array of memory objects, each with at least 'fact' (string). "
                                   "Optional: keywords (array), persons (array), timestamp (string), topic (string).",
                    "items": {"type": "object"},
                },
            },
            "required": ["facts"],
        },
    },
    handler=_memory_compress_handler,
)

registry.register(
    name="search_compressed_memory",
    toolset="diagnostics",
    schema={
        "name": "search_compressed_memory",
        "description": "Search compressed long-term memories by semantic similarity. "
                       "Returns most relevant stored facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    },
    handler=_memory_search_handler,
)
