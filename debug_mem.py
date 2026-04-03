#!/usr/bin/env python3
import os, sys
sys.path.insert(0, '/home/terexitarius/.hermes/hermes-agent')
from dotenv import load_dotenv
load_dotenv(os.path.expanduser('~/.hermes/.env'), override=True)

from tools.vector_memory_store import (
    VectorMemoryStore, VectorMemoryConfig, EmbeddingService,
    HAS_UPSTASH, UpstashProvider, B2Provider
)

print("=== DEBUG Upstash ===")
print(f"HAS_UPSTASH: {HAS_UPSTASH}")
print(f"UPSTASH_VECTOR_REST_URL: {os.getenv('UPSTASH_VECTOR_REST_URL', 'NOT SET')}")
print(f"UPSTASH_VECTOR_REST_TOKEN: {'set' if os.getenv('UPSTASH_VECTOR_REST_TOKEN') else 'NOT SET'}")

try:
    config = VectorMemoryConfig()
    provider = UpstashProvider(config, None, "test_ns")
    print("UpstashProvider initialized OK")
except Exception as e:
    print(f"UpstashProvider failed: {type(e).__name__}: {e}")

print(f"\n=== DEBUG B2 ===")
print(f"BACKBLAZE_KEY_ID: {repr(os.getenv('BACKBLAZE_KEY_ID', 'NOT SET'))[:40]}")
print(f"B2_KEY_ID: {repr(os.getenv('B2_KEY_ID', 'NOT SET'))[:40]}")
app_key = os.getenv('BACKBLAZE_APPLICATION_KEY', '')
print(f"BACKBLAZE_APPLICATION_KEY len: {len(app_key)}")

try:
    b2 = B2Provider(config, None, "test_bucket")
    print("B2Provider initialized OK")
    buckets = b2.s3.list_buckets()
    print(f"Buckets: {[b['Name'] for b in buckets['Buckets']]}")
except Exception as e:
    print(f"B2Provider failed: {type(e).__name__}: {e}")

print(f"\n=== DEBUG Embedding ===")
emb = EmbeddingService(config)
print(f"cf_account: {emb.cf_account}")
print(f"cf_token: {'set' if emb.cf_token else 'NOT SET'}")
print(f"api_key len: {len(emb.api_key)}")
print(f"has _embed_method attr: {hasattr(emb, '_embed_method')}")

try:
    vecs = emb.embed(["test test test"])
    print(f"Embedding OK: dim={len(vecs[0])}")
except Exception as e:
    print(f"Embedding failed: {e}")
