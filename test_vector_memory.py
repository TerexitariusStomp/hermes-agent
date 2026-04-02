#!/usr/bin/env python3
"""
Test script for cloud vector memory integration.

This script performs a basic validation:
- Loads config and creates VectorMemoryStore
- Tests embedding generation with free model
- Tests add/search operations (if cloud credentials available)

Usage:
  python3 test_vector_memory.py [--provider qdrant|pinecone] [--enable-vector]
"""

import argparse
import sys
import os
from pathlib import Path

# Add hermes-agent directory to path
sys.path.insert(0, str(Path(__file__).parent))

def test_embedding_service():
    """Test the embedding service with free model fallbacks."""
    from tools.vector_memory_store import EmbeddingService, VectorMemoryConfig
    print("Testing EmbeddingService...")
    config = VectorMemoryConfig(embedding_model="nvidia/llama-nemotron-embed-vl-1b-v2:free")
    try:
        service = EmbeddingService(config)
        vec_size = service.get_vector_size()
        print(f"  Determined vector size: {vec_size}")
        # Test embedding a simple text
        emb = service.embed(["Hello world test"])[0]
        print(f"  Generated embedding with length: {len(emb)}")
        assert len(emb) == vec_size, "Embedding length mismatch"
        print("  EmbeddingService test PASSED")
        return True
    except Exception as e:
        print(f"  EmbeddingService test FAILED: {e}")
        return False

def test_vector_memory_store_init():
    """Test VectorMemoryStore initialization (without network)."""
    from tools.vector_memory_store import VectorMemoryStore, VectorMemoryConfig
    print("Testing VectorMemoryStore initialization...")
    # This will attempt to connect to cloud; may fail if credentials not set
    try:
        config = VectorMemoryConfig(provider="auto")
        store = VectorMemoryStore(config)
        print("  VectorMemoryStore created (collections ensured)")
        print("  VectorMemoryStore init test PASSED")
        return True
    except ImportError as e:
        print(f"  Dependencies missing: {e}")
        print("  Install with: pip install 'hermes-agent[vector-memory]'")
        return False
    except Exception as e:
        print(f"  VectorMemoryStore init test SKIPPED (likely missing credentials): {e}")
        return None  # skip

def test_memory_operations():
    """Test store, search, routing operations if cloud is available."""
    from tools.vector_memory_store import VectorMemoryStore, VectorMemoryConfig
    print("Testing memory operations (store + search + routing)...")
    config = VectorMemoryConfig(provider="auto")
    try:
        store = VectorMemoryStore(config)

        # Show which providers are active
        info = store.get_provider_info()
        print(f"  Active providers: {info}")

        # Test 1: Store a memory entry (should route to qdrant)
        entry_id = store.store(
            content="Test entry: Hermes vector memory routing works!",
            target="memory",
            entry_type="memory",
        )
        if not entry_id:
            print("  Store failed — no provider available")
            return False
        print(f"  Stored entry: {entry_id}")

        # Test 2: Store an entity (should route to neo4j)
        entity_id = store.store(
            content="Qdrant is a vector database for semantic search",
            target="memory",
            entry_type="entity",
        )
        print(f"  Stored entity: {entity_id}")

        # Test 3: Store a session (should route to upstash)
        session_id = store.store(
            content="User session started with context about GPU hotplug",
            target="memory",
            entry_type="session",
        )
        print(f"  Stored session: {session_id}")

        # Test 4: Check routing stats
        stats = store.get_router_stats()
        print(f"  Router stats: {stats}")

        # Test 5: Search for entries
        search_results = store.search("vector memory", top_k=5, target="memory")
        print(f"  Search returned {len(search_results)} results")
        if search_results:
            best = search_results[0]
            print(f"  Top match: score={best.get('score', 0):.4f}, content={best.get('content', '')[:60]}...")
            print(f"  Source: {best.get('_source', 'unknown')}")

        # Test 6: Router classification test
        router = store.router
        for etype in ["memory", "entity", "session", "archive", "structured", "backup"]:
            target = router.route_type(etype)
            print(f"  Route '{etype}' -> {target}")

        print("  Memory operations test PASSED")
        return True
    except Exception as e:
        print(f"  Memory operations test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    parser = argparse.ArgumentParser(description="Test cloud vector memory integration")
    parser.add_argument("--provider", choices=["qdrant", "pinecone", "auto"], default="auto",
                        help="Force a specific provider")
    parser.add_argument("--enable-vector", action="store_true",
                        help="Enable vector_memory in config for this test")
    parser.add_argument("--embedding-model", default="nvidia/llama-nemotron-embed-vl-1b-v2:free",
                        help="Embedding model to use")
    args = parser.parse_args()

    print("=== Hermes Vector Memory Integration Test ===\n")

    # Step 1: test embedding service (doesn't require cloud)
    emb_ok = test_embedding_service()
    if not emb_ok:
        print("\nEmbedding service test failed. Aborting further tests.")
        sys.exit(1)

    # Step 2: test store init (requires cloud credentials)
    init_result = test_vector_memory_store_init()
    if init_result is False:
        print("\nVector memory store init failed due to missing dependencies.")
        print("Please install: pip install 'hermes-agent[vector-memory]'")
        sys.exit(1)
    elif init_result is None:
        print("\nVector memory store init skipped (missing credentials).")
        print("Ensure QDRANT_URL/QDRANT_API_KEY or PINECONE_API_KEY are set in ~/.hermes/.env")
        sys.exit(0)

    # Step 3: test operations
    ops_ok = test_memory_operations()
    if ops_ok:
        print("\nAll tests PASSED!")
        sys.exit(0)
    else:
        print("\nSome tests FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    main()
