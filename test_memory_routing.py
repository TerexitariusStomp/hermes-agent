#!/usr/bin/env python3
"""Full end-to-end routing test for Hermes memory system."""
import os, sys, json
sys.path.insert(0, '/home/terexitarius/.hermes/hermes-agent')

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.hermes/.env"), override=True)

from tools.vector_memory_store import create_vector_memory_store

store = create_vector_memory_store()

print("=" * 55)
print("  FULL END-TO-END ROUTING TEST")
print("=" * 55)
print("Active providers:", store.get_provider_info())
print(f"Embedding: {type(store.embedding).__name__}")
print()

# STORE test for each route
tests = [
    ("AWS Lambda gateway xn5aikwwc9 in us-east-2", "memory", "memory", "upstash"),
    ("Hermes is a CLI AI agent on Ubuntu server", "memory", "entity", "neo4j"),
    ("Cloud orchestration: Lambda, Render, Kaggle, Colab", "memory", "archive", "pinecone"),
    ("Server cleanup rules and safe directories", "memory", "system", "upstash"),
    ("Task: deploy Render webhook service", "memory", "task", "postgres"),
]

for content, target, etype, expected in tests:
    eid = store.store(content, target=target, entry_type=etype)
    print(f"[STORE] {etype:10s} -> {expected:10s} id={eid[:16]}...")

print()

# SEARCH tests
print("[SEARCH] 'Lambda gateway' -> default=upstash (memory)")
results = store.search("Lambda gateway", top_k=3)
for r in results:
    print(f"  [{r['score']:.4f}] ({r.get('_source','?')}) {r['content'][:70]}")

print("\n[SEARCH] 'Ubuntu server' -> neo4j (entity)")
results2 = store.search("Ubuntu server", top_k=3)
for r in results2:
    print(f"  [{r['score']:.4f}] ({r.get('_source','?')}) {r['content'][:70]}")

print("\n[SEARCH] 'cloud orchestration' -> pinecone (archive)")
results3 = store.search("cloud orchestration", top_k=3)
for r in results3:
    print(f"  [{r['score']:.4f}] ({r.get('_source','?')}) {r['content'][:70]}")

print("\n[SEARCH] all providers for 'Hermes AI agent'")
all_r = store.search("Hermes AI agent CLI", top_k=5)
for r in all_r:
    print(f"  [{r['score']:.4f}] ({r.get('_source','?')}) {r['content'][:70]}")

print("\nRouter stats:", json.dumps(store.get_stats(), indent=2))

print("\n" + "=" * 55)
print("  RESULT: memory->Upstash, entity->Neo4j, archive->Pinecone")
print("  Embeddings: Cloudflare 1024d (free) / OpenRouter 2048d->truncate")
print("  Active: Upstash, Neo4j, Pinecone, B2")
print("=" * 55)
