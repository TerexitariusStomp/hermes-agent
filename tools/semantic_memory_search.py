#!/usr/bin/env python3
"""
Semantic Memory Search Tool for Hermes Agent.

Searches the cloud vector memory archive for relevant past experiences using
semantic similarity. Works alongside the regular memory tool.

Usage:
  - The agent calls this tool when it needs to recall something specific from its
    long-term memory that may not be in the curated working set.

Requirements:
  - Vector memory must be configured in ~/.hermes/config.yaml under `vector_memory`
  - Dependencies: install with `pip install "hermes-agent[vector-memory]"`
"""

import json
import logging
from typing import Dict, Any, List

from tools.registry import registry

# Try to import vector memory store
try:
    from hermes_cli.config import load_config
    from tools.vector_memory_store import VectorMemoryStore, VectorMemoryConfig
    HAS_VECTOR_MEMORY = True
except ImportError:
    HAS_VECTOR_MEMORY = False


def semantic_memory_search_tool(
    query: str,
    top_k: int = 10,
    target: str = "all",  # "memory", "user", or "all"
) -> str:
    """
    Search memories using semantic similarity over the cloud vector archive.

    Parameters:
      query: The search query (natural language).
      top_k: Number of results to return (default 10, max 100).
      target: Filter by memory type: "memory" (agent notes), "user" (user profile), or "all" (both).

    Returns:
      JSON string with results list containing: content, target, score, timestamp.
    """
    if not HAS_VECTOR_MEMORY:
        return json.dumps({
            "success": False,
            "error": "Vector memory dependencies not installed. Run: pip install 'hermes-agent[vector-memory]'"
        }, ensure_ascii=False)

    try:
        # Load vector memory config from hermes config
        hermes_cfg = load_config()
        vec_mem_cfg = hermes_cfg.get("vector_memory", {})
        if not vec_mem_cfg.get("enabled", False):
            return json.dumps({
                "success": False,
                "error": "Vector memory is not enabled in config. Add 'vector_memory:\n  enabled: true' to ~/.hermes/config.yaml"
            }, ensure_ascii=False)

        # Create a VectorMemoryStore instance (read-only)
        config = VectorMemoryConfig(
            provider=vec_mem_cfg.get("provider", "auto"),
            embedding_model=vec_mem_cfg.get("embedding_model", "openai/text-embedding-ada-002"),
            embedding_base_url=vec_mem_cfg.get("embedding_base_url"),
            embedding_api_key_env=vec_mem_cfg.get("embedding_api_key_env", "OPENROUTER_API_KEY"),
            collection_name=vec_mem_cfg.get("collection_name", "hermes_memory"),
            memory_char_limit=vec_mem_cfg.get("memory_char_limit", 2200),
            user_char_limit=vec_mem_cfg.get("user_char_limit", 1375),
        )
        store = VectorMemoryStore(config)
        # No need to load curated entries for search; but some init may need; skip
        # We'll directly call search without load_from_disk

        # Validate top_k
        if top_k < 1 or top_k > 100:
            top_k = 10

        # Determine target filter
        target_filter = None
        if target in ("memory", "user"):
            target_filter = target
        elif target == "all":
            target_filter = None
        else:
            return json.dumps({"success": False, "error": f"Invalid target '{target}'. Use 'memory', 'user', or 'all'."}, ensure_ascii=False)

        # Perform search
        results = store.search(query=query, top_k=top_k, target=target_filter)

        return json.dumps({
            "success": True,
            "query": query,
            "results": results,
            "count": len(results),
        }, ensure_ascii=False)

    except Exception as e:
        logging.exception("Semantic memory search failed")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def check_semantic_memory_requirements() -> bool:
    """Check if vector memory dependencies are available."""
    if not HAS_VECTOR_MEMORY:
        return False
    try:
        # Also check if config has vector_memory enabled
        from tools.config import load_hermes_config
        hermes_cfg = load_hermes_config()
        vec_mem_cfg = hermes_cfg.get("vector_memory", {})
        if not vec_mem_cfg.get("enabled", False):
            return False
        return True
    except Exception:
        return False


# Register the tool
registry.register(
    name="semantic_memory_search",
    toolset="memory",
    schema={
        "name": "semantic_memory_search",
        "description": (
            "Search long-term memory using semantic similarity over cloud vector archive. "
            "Retrieve relevant past experiences, observations, and learned facts even if they "
            "have been evicted from the curated working set due to capacity limits. "
            "Use this when you need to recall something specific from earlier sessions that you don't currently remember."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query in natural language describing what you're looking for."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 10, max 100).",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 10
                },
                "target": {
                    "type": "string",
                    "enum": ["memory", "user", "all"],
                    "description": "Which memory store to search: 'memory' for agent notes, 'user' for user profile, 'all' for both.",
                    "default": "all"
                }
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: semantic_memory_search_tool(
        query=args.get("query", ""),
        top_k=args.get("top_k", 10),
        target=args.get("target", "all"),
    ),
    check_fn=check_semantic_memory_requirements,
    emoji="🔍",
)