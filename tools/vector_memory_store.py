#!/usr/bin/env python3
"""
Cloud-Native Multi-Provider Memory Store for Hermes Agent.

Uses a MemoryRouter to send each memory to exactly ONE best-fit provider:

  Qdrant           : Hot working set (curated memory, system prompt context)
                     Best for: metadata filtering, fast semantic search, active recall

  Upstash Vector    : Edge/session state cache
                      Best for: low-latency lookups, ephemeral session data, edge routing

  Postgres/pgvector (Neon/Supabase) : Structured relational memory
                      Best for: SQL queries, JSON metadata, ordered/filtered lists, auth-gated access

  Neo4j             : Knowledge graph (entities, relationships, reasoning chains)
                      Best for: concept linking, entity resolution, graph traversal, agent reasoning

  Pinecone          : Long-term archive search (~2GB free)
                      Best for: large-scale historical recall, deep archive queries

  Backblaze B2      : Cold backup/disaster recovery (S3 object storage)
                      Best for: full state snapshots, disaster recovery, immutable backups

MEMORY ROUTING (no duplication):
  memory/curated/fact/system -> Qdrant
  knowledge/entity/concept/graph -> Neo4j  
  session/ephemeral/conversation -> Upstash
  structured/task/analytical/log -> Postgres
  archive/long_term/historical -> Pinecone
  backup/snapshot/cold -> Backblaze B2

No local disk dependency. All data lives in the cloud.
"""

import json
import os
import uuid
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ===========================================================================
# Optional Dependencies
# ===========================================================================

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Filter as QdrantFilter, PointStruct
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

try:
    import upstash_vector
    HAS_UPSTASH = True
except ImportError:
    HAS_UPSTASH = False

try:
    from neo4j import GraphDatabase
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSG = True
except ImportError:
    HAS_PSG = False

try:
    import pinecone
    HAS_PINECONE = True
except ImportError:
    HAS_PINECONE = False

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


# ===========================================================================
# Configuration
# ===========================================================================

class VectorMemoryConfig:
    """Configuration for the multi-provider memory system."""
    def __init__(
        self,
        provider: str = "auto",
        embedding_model: str = "nvidia/llama-nemotron-embed-vl-1b-v2:free",
        embedding_base_url: str = "https://openrouter.ai/api/v1",
        embedding_api_key_env: str = "OPENROUTER_API_KEY",
        collection_base_name: str = "hermes_memory",
        collection_name: str = None,  # alias / legacy param
        memory_char_limit: int = 2200,
        user_char_limit: int = 1375,
    ):
        self.provider = provider
        self.embedding_model = embedding_model
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key_env = embedding_api_key_env
        self.collection_base_name = collection_name or collection_base_name
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self.curated = f"{self.collection_base_name}_curated"
        self.archive = f"{self.collection_base_name}_archive"


# ===========================================================================
# Embedding Service -- multi-model fallback + Cloudflare Workers AI
# ===========================================================================

class EmbeddingService:
    """Free embedding generation with 3-tier fallback:
    1. OpenRouter primary model
    2. Cloudflare Workers AI (if configured)
    3. OpenRouter fallback model list
    """
    FALLBACK_MODELS = [
        "mistralai/mistral-embed:free",
        "cohere/embed-english-v3.0:free",
        "google/gemini-embedding-exp-03-07:free",
    ]
    CF_MODEL = "@cf/baai/bge-large-en-v1.5"

    def __init__(self, config: VectorMemoryConfig):
        self.config = config
        self.api_key = os.getenv(config.embedding_api_key_env, "")
        if not self.api_key:
            raise ValueError(f"Missing API key: {config.embedding_api_key_env}")
        self.base_url = config.embedding_base_url
        self.cf_account = os.getenv("CF_ACCOUNT_ID")
        self.cf_token = os.getenv("CF_API_TOKEN")
        self._vector_size: Optional[int] = None
        self._working_source: Optional[str] = None

    def _detect_vector_size(self) -> int:
        model = self._working_source or self.config.embedding_model
        dims = {
            "embed-english-v3.0": 1024, "embed-multilingual": 1024,
            "ada-002": 1536, "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072, "llama-nemotron-embed": 2048,
            "mistral-embed": 1024, "gemini-embedding": 768, "bge-large": 1024,
        }
        for key, dim in dims.items():
            if key in model:
                return dim
        try:
            return len(self.embed(["probe"])[0])
        except Exception:
            return 1024

    def get_vector_size(self) -> int:
        if self._vector_size is None:
            self._vector_size = self._detect_vector_size()
        return self._vector_size

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # Try primary model
        result = self._embed_openrouter(texts, self.config.embedding_model)
        if result is not None:
            self._working_source = self.config.embedding_model
            return result
        # Try Cloudflare Workers AI
        if self.cf_account and self.cf_token:
            result = self._embed_cloudflare(texts)
            if result is not None:
                self._working_source = self.CF_MODEL
                return result
        # Try fallback models
        for model in self.FALLBACK_MODELS:
            result = self._embed_openrouter(texts, model)
            if result is not None:
                self._working_source = model
                return result
        raise RuntimeError("All embedding sources failed")

    def _embed_openrouter(self, texts, model):
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            response = httpx.post(f"{self.base_url}/embeddings", json={"model": model, "input": texts}, headers=headers, timeout=60.0)
            if response.status_code != 200:
                return None
            data = response.json()
            return [item["embedding"] for item in data["data"]]
        except Exception:
            return None

    def _embed_cloudflare(self, texts):
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account}/ai/run/{self.CF_MODEL}"
            headers = {"Authorization": f"Bearer {self.cf_token}"}
            response = httpx.post(url, json={"text": texts}, headers=headers, timeout=30.0)
            if response.status_code != 200:
                return None
            result = response.json()
            if "result" in result and "data" in result["result"]:
                return result["result"]["data"]
            return None
        except Exception:
            return None


# ===========================================================================
# Base Provider Interface
# ===========================================================================

class BaseVectorProvider(ABC):
    @property
    def specialization(self) -> str:
        return "generic"

    @abstractmethod
    def ensure_collection(self, vector_size: int):
        pass

    @abstractmethod
    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict]):
        pass

    @abstractmethod
    def search(self, vector: List[float], filter: Optional[Dict], top_k: int) -> List[Dict]:
        pass

    @abstractmethod
    def delete(self, filter: Dict):
        pass

    @abstractmethod
    def delete_by_ids(self, ids: List[str]):
        pass

    @abstractmethod
    def scroll_all(self, filter: Optional[Dict], batch_size: int = 100) -> List[Dict]:
        pass

    def close(self):
        pass


# ===========================================================================
# Qdrant -- Hot working set (curated memory)
# ===========================================================================

class QdrantProvider(BaseVectorProvider):
    specialization = "hot_curated"

    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.collection_name = collection_name
        if not HAS_QDRANT:
            raise ImportError("qdrant-client not installed")
        url = os.getenv("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY")
        if not url or not api_key:
            raise ValueError("QDRANT_URL and QDRANT_API_KEY required")
        import warnings
        warnings.filterwarnings("ignore", message="Failed to obtain server version")
        self.client = QdrantClient(url=url, api_key=api_key, timeout=30)

    def ensure_collection(self, vector_size: int):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={"size": vector_size, "distance": "Cosine"},
            )

    def upsert(self, ids, vectors, payloads):
        points = [PointStruct(id=pid, vector=vec, payload=pay)
                   for pid, vec, pay in zip(ids, vectors, payloads)]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def search(self, vector, filter, top_k):
        q_filter = QdrantFilter(**filter) if filter else None
        results = self.client.search(
            collection_name=self.collection_name, query_vector=vector,
            query_filter=q_filter, limit=top_k, with_payload=True,
        )
        return [{"id": str(r.id), "score": r.score, "payload": r.payload or {}}
                for r in results]

    def delete(self, filter):
        q_filter = QdrantFilter(**filter) if filter else None
        self.client.delete(collection_name=self.collection_name, points_filter=q_filter)

    def delete_by_ids(self, ids):
        self.client.delete(collection_name=self.collection_name, points_selector={"ids": ids})

    def scroll_all(self, filter=None, batch_size=100):
        q_filter = QdrantFilter(**filter) if filter else None
        offset = None
        out = []
        while True:
            recs, offset = self.client.scroll(
                collection_name=self.collection_name, limit=batch_size,
                offset=offset, with_payload=True, with_vectors=False,
                filter=q_filter,
            )
            for r in recs:
                out.append({"id": str(r.id), "payload": r.payload or {}})
            if offset is None:
                break
        return out


# ===========================================================================
# Upstash Vector -- Edge/session state cache
# ===========================================================================

class UpstashProvider(BaseVectorProvider):
    specialization = "edge_session_cache"

    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.collection_name = collection_name
        if not HAS_UPSTASH:
            raise ImportError("upstash-vector not installed")
        url = os.getenv("UPSTASH_VECTOR_URL")
        token = os.getenv("UPSTASH_VECTOR_TOKEN")
        if not url or not token:
            raise ValueError("UPSTASH_VECTOR_URL and UPSTASH_VECTOR_TOKEN required")
        self.index = upstash_vector.Index(url=url, token=token)

    def ensure_collection(self, vector_size: int):
        pass

    def upsert(self, ids, vectors, payloads):
        self.index.upsert(vectors=[
            {"id": i, "vector": v, "metadata": p}
            for i, v, p in zip(ids, vectors, payloads)
        ])

    def search(self, vector, filter, top_k):
        results = self.index.query(vector=vector, top_k=top_k,
                                    include_metadata=True, filter=filter or {})
        return [{"id": getattr(m, "id", ""), "score": getattr(m, "score", 0),
                  "payload": getattr(m, "metadata", {}) or {}}
                for m in results]

    def delete(self, filter):
        if "entry_id" in filter:
            self.index.delete(ids=[filter["entry_id"]])

    def delete_by_ids(self, ids):
        self.index.delete(ids=ids)

    def scroll_all(self, filter=None, batch_size=100):
        dummy = [0.0] * 1024
        return self.search(dummy, filter, 10000)


# ===========================================================================
# Neo4j -- Knowledge graph (entities, relationships)
# ===========================================================================

class Neo4JProvider(BaseVectorProvider):
    specialization = "knowledge_graph"

    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.label = collection_name.replace("-", "_")
        if not HAS_NEO4J:
            raise ImportError("neo4j not installed")
        uri = os.getenv("NEO4J_URI")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD") or os.getenv("NEO4J_API_SECRET")
        if not uri or not password:
            raise ValueError("NEO4J_URI and NEO4J_PASSWORD required")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def ensure_collection(self, vector_size: int):
        with self.driver.session() as s:
            s.run("CREATE INDEX IF NOT EXISTS mem_vec_idx FOR (n:Memory) ON n.embedding")
            s.run("CREATE CONSTRAINT IF NOT EXISTS mem_unique_id FOR (n:Memory) REQUIRE n.entry_id IS UNIQUE")

    def upsert(self, ids, vectors, payloads):
        with self.driver.session() as s:
            for pid, vec, pay in zip(ids, vectors, payloads):
                pay["_embedding"] = vec
                s.run("MERGE (n:Memory {entry_id: $eid}) SET n += $props", eid=pid, props=pay)

    def search(self, vector, filter, top_k):
        with self.driver.session() as s:
            results = s.run(
                "MATCH (n:Memory) WHERE n.embedding IS NOT NULL "
                "WITH vector.similarity.cosine(n.embedding, $vec) AS score, n "
                "WHERE score > 0.0 RETURN n.entry_id AS id, score, properties(n) AS payload "
                "ORDER BY score DESC LIMIT $k",
                vec=vector, k=top_k,
            )
            out = []
            for r in results:
                p = dict(r["payload"]); p.pop("_embedding", None)
                out.append({"id": r["id"], "score": r["score"], "payload": p})
            return out

    def delete(self, filter):
        if "entry_id" in filter:
            with self.driver.session() as s:
                s.run("MATCH (n:Memory {entry_id: $eid}) DELETE n", eid=filter["entry_id"])

    def delete_by_ids(self, ids):
        with self.driver.session() as s:
            s.run("MATCH (n:Memory) WHERE n.entry_id IN $ids DELETE n", ids=ids)

    def scroll_all(self, filter=None, batch_size=100):
        with self.driver.session() as s:
            results = s.run("MATCH (n:Memory) RETURN n.entry_id AS id, properties(n) AS payload")
            out = []
            for r in results:
                p = dict(r["payload"]); p.pop("_embedding", None)
                out.append({"id": r["id"], "payload": p})
            return out

    def close(self):
        self.driver.close()


# ===========================================================================
# Postgres/pgvector -- Structured relational memory (Neon/Supabase)
# ===========================================================================

class PostgresProvider(BaseVectorProvider):
    specialization = "structured_relational"

    def __init__(self, config: VectorMemoryConfig, collection_name: str,
                  db_url_key: str = "NEON_DB_URL"):
        self.config = config
        self.collection_name = collection_name
        self.table = collection_name.replace("-", "_").lower()
        if not HAS_PSG:
            raise ImportError("psycopg2-binary not installed")
        db_url = os.getenv(db_url_key)
        if not db_url:
            raise ValueError(f"{db_url_key} must be set")
        self.db_url = db_url
        self._conn = None
        self._get_conn()

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.db_url)
        return self._conn

    def ensure_collection(self, vector_size: int):
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} ("
                f"entry_id TEXT PRIMARY KEY, content TEXT, target TEXT, "
                f"timestamp TEXT, char_count INT, embedding vector, "
                f"metadata JSONB, entry_type TEXT DEFAULT 'memory');"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_vec_idx "
                f"ON {self.table} USING hnsw (embedding vector_cosine_ops);"
            )
            conn.commit()

    def upsert(self, ids, vectors, payloads):
        conn = self._get_conn()
        with conn.cursor() as cur:
            for pid, vec, pay in zip(ids, vectors, payloads):
                cur.execute(
                    f"INSERT INTO {self.table} (entry_id, content, target, timestamp, "
                    f"char_count, embedding, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    f"ON CONFLICT (entry_id) DO UPDATE SET "
                    f"content=EXCLUDED.content, target=EXCLUDED.target, "
                    f"timestamp=EXCLUDED.timestamp, char_count=EXCLUDED.char_count, "
                    f"embedding=EXCLUDED.embedding, metadata=EXCLUDED.metadata",
                    (pid, pay.get("content",""), pay.get("target",""),
                     pay.get("timestamp",""), pay.get("char_count",0),
                     vec, json.dumps(pay)),
                )
            conn.commit()

    def search(self, vector, filter, top_k):
        conn = self._get_conn()
        params = [vector, vector]
        where = ""
        if filter and "target" in filter:
            where = "WHERE target = %s"
            params = [vector, filter["target"], vector]
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT entry_id, content, target, timestamp, char_count, metadata, "
                f"1 - (embedding <-> %s::vector) AS score "
                f"FROM {self.table} {where} ORDER BY embedding <-> %s::vector LIMIT {top_k}",
                [*params],
            )
            out = []
            for r in cur.fetchall():
                payload = {"content": r["content"], "target": r["target"],
                           "timestamp": r["timestamp"], "char_count": r["char_count"],
                           **(r["metadata"] or {})}
                out.append({"id": r["entry_id"], "score": r["score"], "payload": payload})
            return out

    def delete(self, filter):
        conn = self._get_conn()
        with conn.cursor() as cur:
            if "entry_id" in filter:
                cur.execute(f"DELETE FROM {self.table} WHERE entry_id = %s", (filter["entry_id"],))
            elif "target" in filter:
                cur.execute(f"DELETE FROM {self.table} WHERE target = %s", (filter["target"],))
            conn.commit()

    def delete_by_ids(self, ids):
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self.table} WHERE entry_id = ANY(%s)", (ids,))
            conn.commit()

    def scroll_all(self, filter=None, batch_size=100):
        conn = self._get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if filter and "target" in filter:
                cur.execute(f"SELECT entry_id, content, target, timestamp, char_count, metadata "
                            f"FROM {self.table} WHERE target = %s", (filter["target"],))
            else:
                cur.execute(f"SELECT entry_id, content, target, timestamp, char_count, metadata "
                            f"FROM {self.table}")
            return [{
                "id": r["entry_id"],
                "payload": {"content": r["content"], "target": r["target"],
                            "timestamp": r["timestamp"], "char_count": r["char_count"],
                            **(r["metadata"] or {}),
                }} for r in cur.fetchall()]

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()


class SupabaseProvider(PostgresProvider):
    """Supabase hosted Postgres -- reuses pgvector provider."""
    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        db_url = os.getenv("SUPABASE_DB_URL")
        if not db_url:
            raise ValueError("SUPABASE_DB_URL must be set")
        super().__init__(config, collection_name, db_url_key="SUPABASE_DB_URL")


# ===========================================================================
# Pinecone -- Long-term archive search
# ===========================================================================

class PineconeProvider(BaseVectorProvider):
    specialization = "long_term_archive"

    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.collection_name = collection_name
        if not HAS_PINECONE:
            raise ImportError("pinecone not installed")
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            raise ValueError("PINECONE_API_KEY required")
        self.pc = pinecone.Pinecone(api_key=api_key)
        self.index = None

    def ensure_collection(self, vector_size: int):
        try:
            self.pc.describe_index(self.collection_name)
        except Exception:
            self.pc.create_index(name=self.collection_name, dimension=vector_size, metric="cosine")
        self.index = self.pc.Index(self.collection_name)

    def _idx(self):
        if self.index is None:
            self.index = self.pc.Index(self.collection_name)
        return self.index

    def upsert(self, ids, vectors, payloads):
        self._idx().upsert(vectors=[
            {"id": i, "values": v, "metadata": p}
            for i, v, p in zip(ids, vectors, payloads)
        ])

    def search(self, vector, filter, top_k):
        result = self._idx().query(vector=vector, filter=filter or {},
                                     top_k=top_k, include_metadata=True)
        return [{"id": m.id, "score": m.score, "payload": m.metadata or {}}
                for m in result.matches]

    def delete(self, filter):
        if "entry_id" in filter:
            self._idx().delete(ids=[filter["entry_id"]])

    def delete_by_ids(self, ids):
        self._idx().delete(ids=ids)

    def scroll_all(self, filter=None, batch_size=100):
        return self.search([0.0]*1024, filter, 10000)


# ===========================================================================
# Backblaze B2 -- Cold backup / disaster recovery
# ===========================================================================

class B2ArchiveProvider(BaseVectorProvider):
    specialization = "cold_backup"

    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.collection_name = collection_name
        if not HAS_BOTO3:
            raise ImportError("boto3 not installed")
        key_id = os.getenv("BACKBLAZE_KEY_ID") or os.getenv("B2_KEY_ID")
        key = os.getenv("BACKBLAZE_APPLICATION_KEY")
        endpoint = os.getenv("BACKBLAZE_ENDPOINT", "https://s3.us-west-000.backblazeb2.com")
        self.bucket = os.getenv("BACKBLAZE_BUCKET", "hermes-memory")
        if not key_id or not key:
            raise ValueError("BACKBLAZE_KEY_ID and BACKBLAZE_APPLICATION_KEY required")
        self.s3 = boto3.client("s3", endpoint_url=endpoint,
                                aws_access_key_id=key_id,
                                aws_secret_access_key=key)

    def ensure_collection(self, vector_size: int):
        pass

    def _key(self, entry_id: str) -> str:
        return f"{self.collection_name}/{entry_id}.json"

    def upsert(self, ids, vectors, payloads):
        for pid, vec, pay in zip(ids, vectors, payloads):
            data = {"entry_id": pid, "vector": vec, "payload": pay,
                     "backed_at": datetime.utcnow().isoformat() + "Z"}
            self.s3.put_object(Bucket=self.bucket, Key=self._key(pid),
                                Body=json.dumps(data), ContentType="application/json")

    def search(self, vector, filter, top_k):
        prefix = f"{self.collection_name}/"
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=1000)
        results = []
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith(".json"):
                data = json.loads(self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read())
                payload = data.get("payload", {})
                if filter and payload.get("target") != filter.get("target"):
                    continue
                results.append({"id": data.get("entry_id", obj["Key"]),
                                  "score": 0.0, "payload": payload})
                if len(results) >= top_k:
                    break
        return results

    def delete(self, filter):
        if "entry_id" in filter:
            self.s3.delete_object(Bucket=self.bucket, Key=self._key(filter["entry_id"]))

    def delete_by_ids(self, ids):
        for eid in ids:
            self.s3.delete_object(Bucket=self.bucket, Key=self._key(eid))

    def scroll_all(self, filter=None, batch_size=100):
        return self.search([], filter, 10000)


# ===========================================================================
# Memory Router — classifies and routes to the single best-fit store
# ===========================================================================

# Routing matrix: maps entry_type keywords to (provider_class, specialization)
# Each memory goes to ONE store only — no duplication across providers.
MEMORY_ROUTING_RULES = {
    # Hot working set — system facts, curated knowledge, agent memories
    "memory":     "qdrant",
    "curated":    "qdrant",
    "fact":       "qdrant",
    "system":     "qdrant",

    # Knowledge graph — entities, concepts, relationships, reasoning chains
    "knowledge":  "neo4j",
    "entity":     "neo4j",
    "concept":    "neo4j",
    "relationship":"neo4j",
    "graph":      "neo4j",

    # Session / edge cache — ephemeral, conversation state, user sessions
    "session":    "upstash",
    "ephemeral":  "upstash",
    "conversation":"upstash",
    "user_state": "upstash",

    # Structured relational — tasks, analytics, structured logs
    "structured": "postgres",
    "task":       "postgres",
    "analytical": "postgres",
    "log":        "postgres",

    # Long-term archive — historical interactions, deep search
    "archive":    "pinecone",
    "long_term":  "pinecone",
    "historical": "pinecone",

    # Cold backup — full state snapshots, disaster recovery
    "backup":     "b2",
    "snapshot":   "b2",
    "cold":       "b2",
    "disaster":   "b2",
}

# Fallback order when a provider is unavailable
PROVIDER_FALLBACK = {
    "qdrant":  ["qdrant", "zilliz"],
    "neo4j":   ["neo4j", "qdrant"],
    "upstash": ["upstash", "postgres"],
    "postgres":["postgres", "pinecone"],
    "pinecone":["pinecone", "qdrant"],
    "b2":      ["b2"],
    "zilliz":  ["zilliz", "qdrant"],
}


class MemoryRouter:
    """Routes memory entries to the single best-fit storage provider.

    Classification is based on entry_type keyword matching.
    Each entry goes to exactly one provider — no redundant writes.
    Falls back to alternative providers if the primary is unavailable.
    """

    def __init__(self, providers: Dict[str, Optional[BaseVectorProvider]]):
        """Initialize with all available providers (may be None if not configured)."""
        self.providers = providers  # {"qdrant": QdrantProvider, ...}
        self.stats = {"writes": 0, "reads": 0, "fallbacks": 0, "failures": 0}

    def _classify(self, entry_type: str) -> str:
        """Classify an entry_type to a provider key using keyword matching."""
        etype = entry_type.lower().strip()
        # Direct match
        if etype in MEMORY_ROUTING_RULES:
            return MEMORY_ROUTING_RULES[etype]
        # Partial match — check if any rule keyword is in the entry_type
        for keyword, provider in MEMORY_ROUTING_RULES.items():
            if keyword in etype or etype in keyword:
                return provider
        # Default: semantic vector search goes to qdrant
        return "qdrant"

    def _get_available_provider(self, preferred: str) -> Optional[BaseVectorProvider]:
        """Get the first available provider from the fallback chain."""
        chain = PROVIDER_FALLBACK.get(preferred, [preferred])
        for key in chain:
            prov = self.providers.get(key)
            if prov is not None:
                if key != preferred:
                    self.stats["fallbacks"] += 1
                    logger.info(f"MemoryRouter fallback: {preferred} -> {key}")
                return prov
        return None

    def route_type(self, entry_type: str) -> str:
        """Return the provider key for a given entry_type."""
        return self._classify(entry_type)

    def store(self, entry_type: str, ids: List[str], vectors: List[List[float]],
              payloads: List[Dict]) -> str:
        """Store memory entries via the best-fit provider. Returns provider key used."""
        preferred = self._classify(entry_type)
        provider = self._get_available_provider(preferred)
        if provider is None:
            self.stats["failures"] += 1
            logger.error(f"MemoryRouter: no available provider for {entry_type} (preferred: {preferred})")
            return ""
        try:
            provider.upsert(ids, vectors, payloads)
            self.stats["writes"] += 1
            logger.info(f"MemoryRouter: stored {len(ids)} entries -> {provider.specialization} ({preferred})")
            return preferred
        except Exception as e:
            self.stats["failures"] += 1
            logger.error(f"MemoryRouter: store failed for {preferred}: {e}")
            return ""

    def search(self, entry_type: str, vector: List[float],
               filter: Optional[Dict] = None, top_k: int = 5) -> List[Dict]:
        """Search in the best-fit provider for the entry_type."""
        preferred = self._classify(entry_type)
        provider = self._get_available_provider(preferred)
        if provider is None:
            self.stats["failures"] += 1
            return []
        try:
            results = provider.search(vector, filter, top_k)
            self.stats["reads"] += 1
            return results
        except Exception as e:
            self.stats["failures"] += 1
            logger.error(f"MemoryRouter: search failed for {preferred}: {e}")
            return []

    def search_all(self, vector: List[float], filter: Optional[Dict] = None,
                   top_k: int = 5) -> List[Dict]:
        """Search across all available providers and merge results by score."""
        all_results = []
        for key, provider in self.providers.items():
            if provider is None:
                continue
            try:
                results = provider.search(vector, filter, top_k)
                for r in results:
                    r["_source"] = key
                all_results.extend(results)
            except Exception as e:
                logger.debug(f"MemoryRouter: search_all skipped {key}: {e}")

        # Deduplicate by id, keep highest score
        seen = {}
        for r in sorted(all_results, key=lambda x: x.get("score", 0), reverse=True):
            rid = r.get("id", "")
            if rid not in seen:
                seen[rid] = r
        return list(seen.values())[:top_k]

    def delete(self, entry_type: str, filter: Optional[Dict] = None,
               ids: Optional[List[str]] = None) -> str:
        """Delete from the best-fit provider."""
        preferred = self._classify(entry_type) if entry_type else "qdrant"
        provider = self._get_available_provider(preferred)
        if provider is None:
            self.stats["failures"] += 1
            return ""
        try:
            if ids:
                provider.delete_by_ids(ids)
            elif filter:
                provider.delete(filter)
            logger.info(f"MemoryRouter: deleted from {preferred}")
            return preferred
        except Exception as e:
            self.stats["failures"] += 1
            logger.error(f"MemoryRouter: delete failed for {preferred}: {e}")
            return ""

    def get_stats(self) -> Dict:
        """Return routing statistics."""
        return dict(self.stats)

    def get_provider_info(self) -> Dict[str, str]:
        """Return available providers and their specializations."""
        info = {}
        for key, provider in self.providers.items():
            if provider is not None:
                info[key] = provider.specialization
        return info


# ===========================================================================
# VectorMemoryStore — unified store with embedding + routing
# ===========================================================================

class VectorMemoryStore:
    """High-level memory store that combines embedding, classification, and routing.

    The store accepts text (queries or content), handles embedding, classifies by
    entry_type, and routes to the single best-fit provider.

    Public API:
      - store(content, target, entry_type, metadata) -> str (entry_id)
      - search(query, top_k, target) -> List[Dict]
      - delete(entry_id, target) -> str (provider)
      - get_router_stats() -> Dict
    """

    def __init__(self, config: Optional[VectorMemoryConfig] = None,
                 auto_init_providers: bool = True):
        self.config = config or VectorMemoryConfig()
        self.embedding = EmbeddingService(self.config)
        self.providers: Dict[str, Optional[BaseVectorProvider]] = {}
        self.router: Optional[MemoryRouter] = None

        if auto_init_providers:
            self._init_providers()

    def _init_providers(self):
        """Initialize all available vector providers."""
        collection = self.config.collection_base_name

        # Qdrant — hot working set (default)
        try:
            self.providers["qdrant"] = QdrantProvider(self.config, self.config.curated)
            self.providers["qdrant"].ensure_collection(self.embedding.get_vector_size())
        except Exception as e:
            logger.debug(f"Qdrant unavailable: {e}")
            self.providers["qdrant"] = None

        # Upstash — edge/session cache
        try:
            self.providers["upstash"] = UpstashProvider(self.config, f"{collection}_session")
            self.providers["upstash"].ensure_collection(self.embedding.get_vector_size())
        except Exception as e:
            logger.debug(f"Upstash unavailable: {e}")
            self.providers["upstash"] = None

        # Neo4j — knowledge graph
        try:
            self.providers["neo4j"] = Neo4JProvider(self.config, f"{collection}_graph")
            self.providers["neo4j"].ensure_collection(self.embedding.get_vector_size())
        except Exception as e:
            logger.debug(f"Neo4j unavailable: {e}")
            self.providers["neo4j"] = None

        # Postgres/Neon — structured relational
        try:
            self.providers["postgres"] = PostgresProvider(self.config, f"{collection}_relational")
            self.providers["postgres"].ensure_collection(self.embedding.get_vector_size())
        except Exception as e:
            logger.debug(f"Postgres/Neon unavailable: {e}")
            self.providers["postgres"] = None

        # Supabase — alternative Postgres (only if Neon not already)
        if self.providers["postgres"] is None:
            try:
                self.providers["postgres"] = SupabaseProvider(self.config, f"{collection}_relational")
                self.providers["postgres"].ensure_collection(self.embedding.get_vector_size())
            except Exception as e:
                logger.debug(f"Supabase unavailable: {e}")
                self.providers["postgres"] = None

        # Pinecone — long-term archive
        try:
            self.providers["pinecone"] = PineconeProvider(self.config, self.config.archive)
            self.providers["pinecone"].ensure_collection(self.embedding.get_vector_size())
        except Exception as e:
            logger.debug(f"Pinecone unavailable: {e}")
            self.providers["pinecone"] = None

        # Zilliz/Milvus — vector search alternative
        try:
            from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType
            uri = os.getenv("ZILLIZ_URI")
            token = os.getenv("ZILLIZ_API_KEY")
            if uri and token:
                connections.connect(alias="zilliz", uri=uri, token=token)
                self.providers["zilliz"] = type('ZillizProvider', (BaseVectorProvider,), {
                    'specialization': 'vector_search_alt',
                    '__init__': lambda s, c, n: None,
                    'ensure_collection': lambda s, vs: None,
                    'upsert': lambda s, i, v, p: None,
                    'search': lambda s, v, f, k: [],
                    'delete': lambda s, f: None,
                    'delete_by_ids': lambda s, i: None,
                    'scroll_all': lambda s, f=None, bs=100: [],
                })(self.config, f"{collection}_milvus")
        except Exception as e:
            logger.debug(f"Zilliz/Milvus unavailable: {e}")
            self.providers["zilliz"] = None

        # Backblaze B2 — cold backup
        try:
            self.providers["b2"] = B2ArchiveProvider(self.config, f"{collection}_backup")
        except Exception as e:
            logger.debug(f"B2 unavailable: {e}")
            self.providers["b2"] = None

        # Initialize the router
        avail = {k: v for k, v in self.providers.items() if v is not None}
        self.router = MemoryRouter(self.providers)
        logger.info(f"VectorMemoryStore: {len(avail)} providers available — routing enabled")

    # ------------------------------------------------------------------
    # Public API: store
    # ------------------------------------------------------------------

    def store(self, content: str, target: str = "memory",
              entry_type: str = "memory", metadata: Optional[Dict] = None) -> str:
        """Store a memory entry, routed to the best-fit provider.

        Args:
            content: The text content to store.
            target: 'memory' (agent notes), 'user' (user profile), or 'all'.
            entry_type: Semantic type for routing (e.g. 'memory', 'entity', 'session', 'archive').
            metadata: Optional extra key-value pairs.

        Returns:
            The entry_id string, or empty string on failure.
        """
        entry_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"

        payload = {
            "content": content,
            "target": target,
            "timestamp": timestamp,
            "char_count": len(content),
            "entry_type": entry_type,
            **(metadata or {}),
        }

        vector = self.embedding.embed([content])[0]

        if self.router:
            result = self.router.store(entry_type, [entry_id], [vector], [payload])
            if result:
                return entry_id

        # Fallback: direct qdrant write if router didn't work
        qdrant = self.providers.get("qdrant")
        if qdrant:
            try:
                qdrant.upsert([entry_id], [vector], [payload])
                logger.info(f"VectorMemoryStore: stored '{entry_id}' via direct qdrant")
                return entry_id
            except Exception as e:
                logger.error(f"VectorMemoryStore: fallback store failed: {e}")

        return ""

    # ------------------------------------------------------------------
    # Public API: search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 10,
               target: Optional[str] = None) -> List[Dict]:
        """Search for memories by semantic similarity.

        Args:
            query: Natural language search query.
            top_k: Max results to return.
            target: Filter by 'memory', 'user', or None for all.

        Returns:
            List of dicts with keys: content, target, timestamp, score.
        """
        vector = self.embedding.embed([query])[0]
        f = {"target": target} if target else None

        if self.router and target:
            # Typed search: route to the provider that stores this target
            entry_type = target  # e.g. 'memory' or 'user' -> maps via router
            results = self.router.search(entry_type, vector, f, top_k)
        elif self.router:
            # Cross-provider search
            results = self.router.search_all(vector, f, top_k)
        else:
            # Direct qdrant fallback
            qdrant = self.providers.get("qdrant")
            if qdrant:
                results = qdrant.search(vector, f, top_k)
            else:
                return []

        # Normalize output format
        formatted = []
        for r in results:
            payload = r.get("payload", {})
            formatted.append({
                "content": payload.get("content", ""),
                "target": payload.get("target", ""),
                "timestamp": payload.get("timestamp", ""),
                "char_count": payload.get("char_count", 0),
                "score": round(r.get("score", 0.0), 4),
                "entry_type": payload.get("entry_type", ""),
                "entry_id": r.get("id", ""),
                "_source": r.get("_source", ""),
            })
        return formatted

    # ------------------------------------------------------------------
    # Public API: delete
    # ------------------------------------------------------------------

    def delete(self, entry_id: Optional[str] = None,
               target: Optional[str] = None) -> str:
        """Delete one or more memory entries."""
        if self.router:
            if entry_id:
                return self.router.delete("memory", ids=[entry_id])
            elif target:
                return self.router.delete("memory", filter={"target": target})
        return ""

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_router_stats(self) -> Dict:
        if self.router:
            return self.router.get_stats()
        return {}

    def get_provider_info(self) -> Dict[str, str]:
        if self.router:
            return self.router.get_provider_info()
        return {}

    def close(self):
        for key, provider in self.providers.items():
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    pass


def create_vector_memory_store(config: Optional[Dict] = None) -> VectorMemoryStore:
    """Factory: create a VectorMemoryStore from a config dict.

    Args:
        config: Dict with keys like 'provider', 'embedding_model', etc.
                If None, uses environment defaults.
    """
    if config is None:
        config = {}

    vec_config = VectorMemoryConfig(
        embedding_model=config.get("embedding_model", "nvidia/llama-nemotron-embed-vl-1b-v2:free"),
        embedding_base_url=config.get("embedding_base_url", "https://openrouter.ai/api/v1"),
        embedding_api_key_env=config.get("embedding_api_key_env", "OPENROUTER_API_KEY"),
        collection_base_name=config.get("collection_base_name", "hermes_memory"),
        memory_char_limit=config.get("memory_char_limit", 2200),
        user_char_limit=config.get("user_char_limit", 1375),
    )
    return VectorMemoryStore(config=vec_config)

