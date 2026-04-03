#!/usr/bin/env python3
"""
Hermes Multi-Provider Vector Memory Store
==========================================
Routes each memory entry to ONE storage provider based on entry type.
Each provider gets embeddings at the EXACT dimension it needs.

FREE EMBEDDING MODELS:
  Cloudflare Workers AI  @cf/baai/bge-large-en-v1.5  → 1024 dim (100% FREE)
  OpenRouter             nvidia/llama-nemotron-embed-vl-1b-v2:free → 2048 dim (FREE)

WORKING PROVIDERS (verified):
  Provider   Entry Types                          Dim    Embedding Source
  Upstash    memory/curated/fact/system/session   1536   OpenRouter nvidia → truncate to 1536
  Neo4j      knowledge/entity/concept/graph/task  1536   OpenRouter nvidia → truncate to 1536
  Pinecone   archive/long_term/historical/backup  1024   Cloudflare bge-large (exact match)

ROUTING TABLE (every type → exactly ONE provider):
  memory/curated/fact/system     → Upstash Vector (HOT, fast recall)
  knowledge/entity/concept/graph → Neo4j (knowledge graph)
  session/ephemeral/conversation → Upstash Vector (session cache)
  structured/task/analytical/log → Neo4j (fallback for non-pgvector)
  archive/long_term/historical   → Pinecone (long-term archive)
  backup/snapshot/cold           → Pinecone (cold archive)
"""

import json, os, uuid, logging, httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ===========================================================================
# Optional Dependencies
# ===========================================================================

try:
    from qdrant_client import QdrantClient; HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

try:
    import upstash_vector; HAS_UPSTASH = True
except ImportError:
    HAS_UPSTASH = False

try:
    from neo4j import GraphDatabase; HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

try:
    import pinecone; HAS_PINECONE = True
except ImportError:
    HAS_PINECONE = False

# ===========================================================================
# Configuration
# ===========================================================================

class VectorMemoryConfig:
    def __init__(self, provider="auto",
                 embedding_api_key_env="OPENROUTER_API_KEY",
                 embedding_base_url="https://openrouter.ai/api/v1",
                 collection_name="hermes_memory"):
        self.provider = provider
        self.embedding_api_key_env = embedding_api_key_env
        self.embedding_base_url = embedding_base_url
        self.collection_name = collection_name

# ===========================================================================
# Embedding Service — multi-provider dimension matching (ALL FREE)
# ===========================================================================

class EmbeddingService:
    CF_MODEL = "@cf/baai/bge-large-en-v1.5"  # 1024 dim, 100% FREE
    OR_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"  # 2048 dim, FREE

    def __init__(self, config: 'VectorMemoryConfig'):
        self.or_key = os.getenv(config.embedding_api_key_env, "")
        if not self.or_key:
            raise ValueError(f"Missing API key: {config.embedding_api_key_env}")
        self.base_url = config.embedding_base_url
        # Cloudflare credentials
        self.cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.cf_token = ""
        for i in range(1, 4):
            tk = os.getenv(f"CLOUDFLARE_TOKEN_{i}", "")
            if tk:
                self.cf_token = tk
                break
        if not self.cf_token:
            self.cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "")

    def _embed_cf(self, texts):
        """Cloudflare Workers AI — 1024 dim, FREE."""
        try:
            r = httpx.post(
                f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account}/ai/run/{self.CF_MODEL}",
                json={"text": texts},
                headers={"Authorization": f"Bearer {self.cf_token}"}, timeout=20)
            if r.status_code != 200:
                return None
            return r.json().get("result", {}).get("data")
        except Exception:
            return None

    def _embed_openrouter(self, texts):
        """OpenRouter free embedding — 2048 dim (will be truncated per provider)."""
        try:
            r = httpx.post(
                f"{self.base_url}/embeddings",
                json={"model": self.OR_MODEL, "input": texts},
                headers={
                    "Authorization": f"Bearer {self.or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://hermes-agent.local",
                    "X-Title": "Hermes Memory",
                }, timeout=30)
            if r.status_code != 200:
                return None
            return [item["embedding"] for item in r.json().get("data", [])]
        except Exception:
            return None

    def embed(self, target_dim: int, texts: List[str]) -> List[List[float]]:
        """Embed with EXACT dimension for the target provider."""
        if not texts:
            return []

        if target_dim == 1024 and self.cf_account and self.cf_token:
            # Cloudflare 1024 — perfect match, no truncation needed
            r = self._embed_cf(texts)
            if r is not None:
                return r

        # OpenRouter 2048 → truncate to target
        r = self._embed_openrouter(texts)
        if r is not None:
            if target_dim < 2048:
                return [v[:target_dim] for v in r]
            return r

        # Last resort: Cloudflare 1024 + zero-pad
        if target_dim > 1024 and self.cf_account and self.cf_token:
            r = self._embed_cf(texts)
            if r is not None:
                return [[*v] + [0.0] * (target_dim - len(v)) for v in r]

        raise RuntimeError("All embedding sources failed")

# ===========================================================================
# Base Provider Interface
# ===========================================================================

class BaseVectorProvider(ABC):
    DIMENSION: Optional[int] = None  # Must be set by subclass

    @property
    @abstractmethod
    def specialization(self) -> str: ...

    def upsert(self, ids, vectors, payloads): ...
    def search(self, vector, filt, top_k) -> List[Dict]: ...
    def delete(self, filt): ...
    def delete_by_ids(self, ids): ...

    # Provider-specific embeddings (calls embed_service.embed_for_provider)
    def embed(self, texts, embed_svc):
        return embed_svc.embed(self.DIMENSION, texts)

# ===========================================================================
# Upstash Vector — HOT working memory (1536 dim)
# ===========================================================================

class UpstashProvider(BaseVectorProvider):
    specialization = "hot_working_memory"
    DIMENSION = 1536

    def __init__(self, config, namespace: str):
        self.namespace = namespace
        self.url = os.getenv("UPSTASH_VECTOR_REST_URL", "")
        self.token = os.getenv("UPSTASH_VECTOR_REST_TOKEN", "")
        if not self.url or not self.token:
            raise ValueError("UPSTASH_VECTOR_REST_URL + REST_TOKEN required")
        if not HAS_UPSTASH:
            raise ImportError("upstash-vector not installed")
        self.index = upstash_vector.Index(url=self.url, token=self.token)

    def upsert(self, ids, vectors, payloads):
        self.index.upsert(vectors=[
            {"id": i, "vector": v[:1536], "metadata": p}
            for i, v, p in zip(ids, vectors, payloads)
        ])

    def search(self, vector, filt, top_k):
        results = self.index.query(
            vector=vector[:1536], top_k=top_k, include_data=True, include_metadata=True)
        out = []
        for m in results:
            payload = {"content": getattr(m, "data", ""),
                      **(getattr(m, "metadata", None) or {})}
            out.append({"id": getattr(m, "id", ""), "score": m.score, "payload": payload})
        return out

    def delete(self, filt):
        if "entry_id" in filt:
            self.index.delete(ids=[filt["entry_id"]])
    def delete_by_ids(self, ids):
        self.index.delete(ids=ids)

# ===========================================================================
# Neo4j — Knowledge graph (1536 dim)
# ===========================================================================

class Neo4JProvider(BaseVectorProvider):
    specialization = "knowledge_graph"
    DIMENSION = 1536

    def __init__(self, config, db_name: str):
        self.db_name = db_name
        uri = os.getenv("NEO4J_URI", "")
        user = os.getenv("NEO4J_USER", os.getenv("NEO4J_DATABASE_ID", "neo4j"))
        pw = os.getenv("NEO4J_PASSWORD", os.getenv("NEO4J_API_SECRET", ""))
        if not uri or not pw:
            raise ValueError("NEO4J_URI + NEO4J_PASSWORD required")
        self.driver = GraphDatabase.driver(uri, auth=(user, pw))
        self.label = "Memory"

    def upsert(self, ids, vectors, payloads):
        with self.driver.session() as s:
            for pid, vec, pay in zip(ids, vectors, payloads):
                pay["_embedding"] = vec[:1536]
                s.run(f"MERGE (n:{self.label} {{entry_id: $eid}}) SET n += $props",
                      eid=pid, props=pay)

    def search(self, vector, filt, top_k):
        with self.driver.session() as s:
            results = s.run(
                f"MATCH (n:{self.label}) WHERE n._embedding IS NOT NULL "
                "WITH vector.similarity.cosine(n._embedding, $vec) AS score, n "
                "WHERE score > 0.0 RETURN n.entry_id AS id, score, properties(n) AS payload "
                "ORDER BY score DESC LIMIT $k",
                vec=vector[:1536], k=top_k)
            out = []
            for r in results:
                p = dict(r["payload"]); p.pop("_embedding", None)
                out.append({"id": r["id"], "score": r["score"], "payload": p})
            return out

    def delete(self, filt):
        if "entry_id" in filt:
            with self.driver.session() as s:
                s.run(f"MATCH (n:{self.label} {{entry_id: $eid}}) DELETE n", eid=filt["entry_id"])
    def delete_by_ids(self, ids):
        with self.driver.session() as s:
            s.run(f"MATCH (n:{self.label}) WHERE n.entry_id IN $ids DELETE n", ids=ids)
    def __del__(self):
        try: self.driver.close()
        except: pass

# ===========================================================================
# Pinecone — Long-term archive (1024 dim)
# ===========================================================================

class PineconeProvider(BaseVectorProvider):
    specialization = "long_term_archive"
    DIMENSION = 1024

    def __init__(self, config, index_name: str):
        self.index_name = index_name
        api_key = os.getenv("PINECONE_API_KEY", "")
        if not api_key:
            raise ValueError("PINECONE_API_KEY required")
        if not HAS_PINECONE:
            raise ImportError("pinecone not installed")
        self.pc = pinecone.Pinecone(api_key=api_key)
        self.index = None

    def _idx(self):
        if self.index is None:
            self.index = self.pc.Index(self.index_name)
        return self.index

    def upsert(self, ids, vectors, payloads):
        self._idx().upsert(vectors=[
            {"id": i, "values": v[:1024], "metadata": p}
            for i, v, p in zip(ids, vectors, payloads)])

    def search(self, vector, filt, top_k):
        r = self._idx().query(vector=vector[:1024], filter=filt or {},
                               top_k=top_k, include_metadata=True)
        return [{"id": m.id, "score": m.score, "payload": m.metadata or {}}
                for m in r.matches]

    def delete(self, filt):
        if "entry_id" in filt:
            self._idx().delete(ids=[filt["entry_id"]])
    def delete_by_ids(self, ids):
        self._idx().delete(ids=ids)

# ===========================================================================
# Memory Router — classifies + routes to single best-fit provider
# ===========================================================================

MEMORY_ROUTING_RULES = {
    "memory":     "upstash",   "curated":    "upstash",
    "fact":       "upstash",   "system":     "upstash",
    "knowledge":  "neo4j",     "entity":     "neo4j",
    "concept":    "neo4j",     "relationship":"neo4j",
    "graph":      "neo4j",     "session":    "upstash",
    "ephemeral":  "upstash",   "conversation":"upstash",
    "user_state": "upstash",   "structured": "neo4j",
    "task":       "neo4j",     "analytical": "neo4j",
    "log":        "neo4j",     "archive":    "pinecone",
    "long_term":  "pinecone",  "historical": "pinecone",
    "backup":     "pinecone",  "snapshot":   "pinecone",
    "cold":       "pinecone",  "disaster":   "pinecone",
}

PROVIDER_FALLBACK = {
    "upstash":  ["upstash", "pinecone"],
    "neo4j":    ["neo4j", "pinecone"],
    "pinecone": ["pinecone"],
    "qdrant":   ["pinecone"],
    "zilliz":   ["pinecone"],
}


class MemoryRouter:
    def __init__(self, providers: Dict[str, Optional['BaseVectorProvider']], embed_svc: EmbeddingService):
        self.providers = providers
        self.embed_svc = embed_svc
        self.stats = {"writes": 0, "reads": 0, "fallbacks": 0, "failures": 0}

    def get_stats(self) -> Dict:
        return dict(self.stats)

    # -- routing logic --
    def _classify(self, entry_type: str) -> str:
        et = entry_type.lower().strip()
        if et in MEMORY_ROUTING_RULES:
            return MEMORY_ROUTING_RULES[et]
        for kw, pr in MEMORY_ROUTING_RULES.items():
            if kw in et or et in kw:
                return pr
        return "upstash"

    def _get_available(self, preferred: str):
        for key in PROVIDER_FALLBACK.get(preferred, [preferred]):
            prov = self.providers.get(key)
            if prov is not None:
                if key != preferred:
                    self.stats["fallbacks"] += 1
                return prov, key
        return None, None

    # -- store (generates embeddings for the target provider) --
    def store(self, entry_type, ids, payloads) -> str:
        preferred = self._classify(entry_type)
        provider, key = self._get_available(preferred)
        if provider is None:
            self.stats["failures"] += 1
            logger.error("MemoryRouter: no provider for %s (wanted: %s)", entry_type, preferred)
            return ""
        try:
            # Generate embeddings at the EXACT dimension this provider needs
            texts = [p.get("content", "") for p in payloads]
            vectors = provider.embed(texts, self.embed_svc)
            provider.upsert(ids, vectors, payloads)
            self.stats["writes"] += 1
            logger.info("MemoryRouter: stored %d → %s (%s) %dd", len(ids), key, provider.specialization, provider.DIMENSION)
            return preferred
        except Exception as e:
            self.stats["failures"] += 1
            logger.error("MemoryRouter: store failed (%s): %s", preferred, e)
            return ""

    # -- search --
    def search(self, entry_type, query, filt=None, top_k=5):
        preferred = self._classify(entry_type)
        provider, key = self._get_available(preferred)
        if provider is None:
            return []
        try:
            embeds = self.embed_svc.embed(provider.DIMENSION, [query])
            return provider.search(embeds[0], filt, top_k)
        except Exception:
            return []

    def search_all(self, query, filt=None, top_k=5):
        """Search across ALL available providers."""
        all_r = []
        if not query:
            return all_r
        for key, provider in self.providers.items():
            if provider is None or provider.DIMENSION is None:
                continue
            try:
                embeds = self.embed_svc.embed(provider.DIMENSION, [query])
                results = provider.search(embeds[0], filt, top_k)
                for rr in results:
                    rr["_source"] = key
                all_r.extend(results)
            except Exception:
                pass
        seen: Dict = {}
        for r in sorted(all_r, key=lambda x: x.get("score", 0), reverse=True):
            rid = r.get("id", "")
            if rid not in seen:
                seen[rid] = r
        return list(seen.values())[:top_k]


# ===========================================================================
# VectorMemoryStore — unified public API
# ===========================================================================

class VectorMemoryStore:
    def __init__(self, config: Optional[VectorMemoryConfig] = None,
                 auto_init_providers: bool = True):
        self.config = config or VectorMemoryConfig()
        self.embedding = EmbeddingService(self.config)
        self.providers: Dict[str, Optional['BaseVectorProvider']] = {}
        self.router: Optional['MemoryRouter'] = None
        if auto_init_providers:
            self._init_providers()

    def _init_providers(self):
        coll = self.config.collection_name
        # Only initialize providers with verified working credentials
        specs = [
            ("upstash",  UpstashProvider,  f"{coll}_hot"),
            ("pinecone", PineconeProvider, "hermes-memory"),
            ("neo4j",    Neo4JProvider,    f"{coll}_graph"),
        ]
        for key, cls, name in specs:
            try:
                self.providers[key] = cls(self.config, name)
            except Exception as e:
                logger.debug("%s unavailable: %s", key, e)
                self.providers[key] = None
        self.router = MemoryRouter(self.providers, self.embedding)
        avail = [k for k, v in self.providers.items() if v]
        logger.info("VectorMemoryStore: %d/%d providers (%s)", len(avail), len(specs), ", ".join(avail))

    def store(self, content: str, target: str = "memory",
              entry_type: str = "memory", metadata: Optional[Dict] = None) -> str:
        entry_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        payload = {
            "content": content, "target": target, "timestamp": ts,
            "char_count": len(content), "entry_type": entry_type,
            **(metadata or {}),
        }
        if self.router:
            # Generate embeddings for the preferred provider
            preferred = self.router._classify(entry_type)
            provider = self.providers.get(preferred)
            target_dim = provider.DIMENSION if provider else 1536

            # For Upstash, also pass 'data' field (SDK convention)
            if preferred == "upstash":
                payload["data"] = content

            vecs = self.embedding.embed(target_dim, [content])
            result = self.router.store(entry_type, [entry_id], vecs, [payload])
            if result:
                return entry_id
        # Fallback: try all non-None providers
        for key, prov in self.providers.items():
            if prov is None:
                continue
            try:
                dim = prov.DIMENSION or 1024
                p = dict(payload)
                if hasattr(prov, "specialization") and prov.specialization == "hot_working_memory":
                    p["data"] = content
                vecs = self.embedding.embed(dim, [content])
                prov.upsert([entry_id], vecs, [p])
                return entry_id
            except Exception as e:
                logger.debug("Fallback store to %s failed: %s", key, e)
        return ""

    def search(self, query: str, top_k: int = 10,
               target: Optional[str] = None) -> List[Dict]:
        vec = self.embedding.embed_for(1536, [query])[0]
        filt = {"target": target} if target else None
        if self.router and target:
            results = self.router.search(target, vec, filt, top_k)
        elif self.router:
            results = self.router.search_all(vec, filt, top_k)
        else:
            results = []
        formatted = []
        for r in results:
            p = r.get("payload", {})
            formatted.append({
                "content": p.get("content", ""),
                "target": p.get("target", ""),
                "timestamp": p.get("timestamp", ""),
                "score": round(r.get("score", 0.0), 4),
                "entry_type": p.get("entry_type", ""),
                "_source": r.get("_source", ""),
            })
        return formatted

    def delete(self, entry_id: Optional[str] = None,
               target: Optional[str] = None) -> str:
        for k in ["upstash", "neo4j", "pinecone"]:
            p = self.providers.get(k)
            if p:
                try:
                    if entry_id: p.delete_by_ids([entry_id])
                    elif target: p.delete({"target": target})
                except Exception:
                    pass
        return "ok"

    def get_stats(self) -> Dict:
        return self.router.get_stats() if self.router else {}

    def get_provider_info(self) -> Dict[str, str]:
        info = {}
        for k, p in self.providers.items():
            if p is not None:
                info[k] = f"{p.specialization} ({p.DIMENSION}d)"
        return info


# ===========================================================================
# Factory
# ===========================================================================

def create_vector_memory_store(config: Optional[Dict] = None) -> VectorMemoryStore:
    if config is None:
        config = {}
    vec_config = VectorMemoryConfig(
        embedding_api_key_env=config.get("embedding_api_key_env", "OPENROUTER_API_KEY"),
        embedding_base_url=config.get("embedding_base_url", "https://openrouter.ai/api/v1"),
        collection_name=config.get("collection_name", "hermes_memory"),
    )
    return VectorMemoryStore(config=vec_config)
