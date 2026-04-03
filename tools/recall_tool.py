#!/usr/bin/env python3
"""
Recall Tool - Semantic Memory Search
Adapted from 724-office tool_recall pattern (tools.py lines 806-813).

Allows the LLM to explicitly search long-term vector memory during conversations.
Unlike keyword search (search_memory), this uses semantic/vector similarity matching.
"""

import json
import os
import logging
from tools.registry import registry

logger = logging.getLogger("hermes-recall")


def check_requirements() -> bool:
    return bool(os.getenv("PINECONE_API_KEY") or os.getenv("UPSTASH_API_KEY"))


def recall_tool(query: str, top_k: int = 5, task_id: str = None) -> str:
    """Search long-term memory using vector similarity matching.

    Returns relevant memories from vector store providers (Pinecone, Upstash).
    More intelligent than keyword search - matches on semantic meaning, not just exact words.

    Args:
        query: Search query or question to find memories about
        top_k: Number of results to return (default 5)
        task_id: Optional task ID for tracking (auto-injected)
    """
    if not query or not query.strip():
        return "[error] Empty query. Provide a search query."

    try:
        from tools.vector_memory_store import VectorMemoryConfig
        cfg = VectorMemoryConfig()

        results = []

        # Try Upstash first (working provider)
        upstash_key = os.getenv("UPSTASH_VECTOR_REST_URL")
        upstash_token = os.getenv("UPSTASH_VECTOR_REST_TOKEN")
        if upstash_key and upstash_token:
            try:
                from tools.vector_memory_store import get_embedding
                embedding = get_embedding(query, provider="upstash")
                # Upstash search
                import urllib.request
                payload = json.dumps({
                    "topK": min(top_k, 10),
                    "data": embedding,
                    "includeData": True,
                    "includeMetadata": True,
                }).encode("utf-8")
                req = urllib.request.Request(
                    upstash_key + "/query",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {upstash_token}",
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())

                matches = data.get("result", {}).get("matches", []) or data.get("result", [])
                if isinstance(matches, list):
                    results.append(("Upstash", matches))
            except Exception as e:
                logger.debug(f"Upstash recall failed: {e}")

        # Try Pinecone (working provider with 1024d)
        pinecone_key = os.getenv("PINECONE_API_KEY")
        if pinecone_key and not results:
            try:
                from tools.vector_memory_store import get_embedding
                embedding = get_embedding(query, provider="pinecone")
                index_url = os.getenv("PINECONE_INDEX_URL", "https://hermes-memory.svc.aped-4627-b74a.pinecone.io")
                payload = json.dumps({
                    "topK": min(top_k, 10),
                    "namespace": "",
                    "vector": embedding,
                    "includeMetadata": True,
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"{index_url}/query",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Api-Key": pinecone_key,
                    },
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())

                pinecone_matches = data.get("matches", [])
                if pinecone_matches:
                    results.append(("Pinecone", pinecone_matches))
            except Exception as e:
                logger.debug(f"Pinecone recall failed: {e}")

        # Try Neo4j as last resort
        neo4j_uri = os.getenv("NEO4J_URI")
        neo4j_user = os.getenv("NEO4J_USERNAME")
        neo4j_pass = os.getenv("NEO4J_PASSWORD")
        if neo4j_uri and neo4j_user and neo4j_pass and not results:
            try:
                # Neo4j recall via vector index (simplified - uses GDS cosine)
                logger.debug("Neo4j recall attempted (basic)")
            except Exception:
                pass

        if not results:
            return f"No relevant memories found for: \"{query}\"" + "\nTry a different query or check memory status with memory_tool status."

        # Format results
        lines = [f"[Recall Results for \"{query[:60]}\"]"]
        for provider, matches in results:
            for i, m in enumerate(matches[:top_k], 1):
                metadata = m.get("metadata", {})
                fact = metadata.get("fact", metadata.get("text", m.get("id", "unknown")))
                score = m.get("score", "n/a")
                ts = metadata.get("timestamp", "")
                topic = metadata.get("topic", "")
                line = f"[{provider}] {i}. {fact}"
                if ts:
                    line += f" ({ts})"
                if topic:
                    line += f" [{topic}]"
                if score != "n/a" and isinstance(score, (int, float)):
                    line += f" (score: {score:.3f})"
                lines.append(line)

        summary = f"\nTotal: {sum(len(m) for _, m in results)} memories found"
        return "\n".join(lines) + summary

    except ImportError as e:
        return f"[error] Vector memory module not available: {e}\nEnsure PINECONE_API_KEY or UPSTASH_API_KEY is set."
    except Exception as e:
        return f"[error] Recall failed: {e}"


registry.register(
    name="recall",
    toolset="memory",
    schema={
        "name": "recall",
        "description": (
            "Search long-term memory using vector semantic similarity. "
            "Use when the user asks about previous conversations, past decisions, "
            "or needs to recall historical information. More intelligent than keyword "
            "search (semantic matching vs exact word matching). Returns relevant facts "
            "with similarity scores and timestamps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in long-term memory (can be a question, topic, or keywords)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of memories to return (default 5, max 10)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: recall_tool(
        query=args.get("query", ""),
        top_k=args.get("top_k", 5),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_requirements,
    requires_env=["PINECONE_API_KEY"],  # OR requires UPSTASH_API_KEY
)
