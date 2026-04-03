#!/usr/bin/env python3
"""
Hermes Memory Router + Embedding Integration
=============================================
Per-provider dimension matching with 100% FREE embeddings.

PROVIDER DIMENSIONS:
  Upstash  → 1536  (uses OpenRouter nvidia → truncate 2048→1536)
  Neo4j    → 1536  (uses OpenRouter nvidia → truncate 2048→1536)  
  Pinecone → 1024  (uses Cloudflare bge-large → exact 1024 match)
  Postgres → 1536  (uses OpenRouter nvidia → truncate 2048→1536)
  B2       → N/A   (raw JSON, no vectors)

FREE EMBEDDING MODELS:
  1. Cloudflare Workers AI: @cf/baai/bge-large-en-v1.5 (1024 dim, 100% FREE)
  2. OpenRouter: nvidia/llama-nemotron-embed-vl-1b-v2:free (2048 dim, FREE)

ROUTING TABLE:
  memory/curated/fact/system     → Upstash Vector (1536d, hot cache)
  knowledge/entity/concept/graph → Neo4j (1536d, knowledge graph)
  session/ephemeral/conversation → Upstash Vector (1536d, session cache)
  structured/task/analytical/log → Postgres (1536d, pgvector + SQL)
  archive/long_term/historical   → Pinecone (1024d, long-term archive)
  backup/snapshot/cold           → Backblaze B2 (raw JSON, cold storage)
"""

import os, sys

# Ensure hermes-agent tools are on path
sys.path.insert(0, '/home/terexitarius/.hermes/hermes-agent')

from dotenv import load_dotenv
load_dotenv(os.path.expanduser('~/.hermes/.env'), override=True)

if __name__ == "__main__":
    print("=== MEMORY PROVIDER STATUS ===\n")
    
    # Check each provider
    checks = {
        "Upstash Vector (1536d)": [
            "UPSTASH_VECTOR_REST_URL",
            "UPSTASH_VECTOR_REST_TOKEN",
        ],
        "Neo4j (1536d)": [
            "NEO4J_URI",
            "NEO4J_PASSWORD",
        ],
        "Pinecone (1024d)": [
            "PINECONE_API_KEY",
        ],
        "Cloudflare Embedding (1024d)": [
            "CLOUDFLARE_ACCOUNT_ID",
            "CLOUDFLARE_TOKEN_1",
        ],
        "OpenRouter Embedding (2048d)": [
            "OPENROUTER_API_KEY",
        ],
    }
    
    for name, vars in checks.items():
        all_set = all(bool(os.getenv(v, "")) for v in vars)
        icon = "✓" if all_set else "✗"
        print(f"  [{icon}] {name}")
        for v in vars:
            has = "set" if os.getenv(v, "") else "missing"
            print(f"       {v}: {has}")
        print()
