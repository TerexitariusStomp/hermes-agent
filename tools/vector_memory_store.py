#!/usr/bin/env python3
"""
Cloud Vector Memory Store for Hermes Agent (Revised).

Uses two collections: <name>_curated (working set for system prompt) and
<name>_archive (full historical archive). All operations write to both;
demotion deletes from curated, leaving archive intact.
"""

import json
import os
import uuid
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod
from hermes_constants import get_hermes_home

# Optional dependencies
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Filter as QdrantFilter, PointStruct
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

try:
    import pinecone
    HAS_PINECONE = True
except ImportError:
    HAS_PINECONE = False


# =============================================================================
# Configuration
# =============================================================================

class VectorMemoryConfig:
    def __init__(
        self,
        provider: str = "auto",
        embedding_model: str = "nvidia/llama-nemotron-embed-vl-1b-v2:free",
        embedding_base_url: Optional[str] = None,
        embedding_api_key_env: str = "OPENROUTER_API_KEY",
        collection_base_name: str = "hermes_memory",
        memory_char_limit: int = 2200,
        user_char_limit: int = 1375,
    ):
        self.provider = provider
        self.embedding_model = embedding_model
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key_env = embedding_api_key_env
        self.collection_base_name = collection_base_name
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Derived collection names
        self.curated_collection = f"{collection_base_name}_curated"
        self.archive_collection = f"{collection_base_name}_archive"


DEFAULT_CONFIG = VectorMemoryConfig()


# =============================================================================
# Embedding Service with Free Model Fallback Chain
# =============================================================================

class EmbeddingService:
    """Generates embeddings via OpenRouter using only free models with fallback chain."""
    # Ordered list of free embedding models to try
    EMBEDDING_MODELS = [
        "nvidia/llama-nemotron-embed-vl-1b-v2:free",
        "mistralai/mistral-embed:free",
        "cohere/embed-english-v3.0:free",
        "google/gemini-embedding-exp-03-07:free",
    ]
    
    def __init__(self, config: VectorMemoryConfig):
        self.config = config
        self.api_key = os.getenv(config.embedding_api_key_env)
        if not self.api_key:
            raise ValueError(f"Missing API key env var: {config.embedding_api_key_env}")
        self.base_url = config.embedding_base_url or "https://openrouter.ai/api/v1"
        self._vector_size: Optional[int] = None
        self._working_model: Optional[str] = None
        
    def _detect_vector_size(self) -> int:
        """Determine vector size based on the embedding model we end up using."""
        model = self._working_model or self.config.embedding_model
        # Known dimensions
        if "text-embedding-ada-002" in model or "text-embedding-3-small" in model:
            return 1536
        if "text-embedding-3-large" in model:
            return 3072
        if "embed-english-v3.0" in model or "embed-multilingual-v3.0" in model:
            return 1024
        if "llama-nemotron-embed" in model:
            return 2048  # likely dimension for this model
        if "mistral-embed" in model:
            return 4096  # Mistral embed dimension
        if "gemini-embedding" in model:
            return 768  # Gemini embeddings typically 768
        # Fallback: embed dummy and measure
        try:
            dummy = self.embed(["test"])[0]
            return len(dummy)
        except:
            return 1536  # default fallback
    
    def get_vector_size(self) -> int:
        if self._vector_size is None:
            self._vector_size = self._detect_vector_size()
        return self._vector_size
        
    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # Try models in order until one succeeds
        last_error = None
        models_to_try = [self.config.embedding_model] + [m for m in self.EMBEDDING_MODELS if m != self.config.embedding_model]
        for model in models_to_try:
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                data = {
                    "model": model,
                    "input": texts,
                }
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(
                        f"{self.base_url}/embeddings",
                        json=data,
                        headers=headers,
                    )
                    if response.status_code != 200:
                        last_error = f"Model {model} returned {response.status_code}: {response.text}"
                        continue
                    resp_data = response.json()
                    embeddings = [item["embedding"] for item in resp_data["data"]]
                    self._working_model = model
                    return embeddings
            except Exception as e:
                last_error = str(e)
                continue
        raise RuntimeError(f"All embedding models failed. Last error: {last_error}")


# =============================================================================
# Vector Provider Abstraction (single collection)
# =============================================================================

class BaseVectorProvider(ABC):
    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        self.config = config
        self.collection_name = collection_name

    @abstractmethod
    def ensure_collection(self, vector_size: int):
        pass

    @abstractmethod
    def upsert(self, ids: List[str], vectors: List[List[float]], payloads: List[Dict[str, Any]]):
        pass

    @abstractmethod
    def search(self, vector: List[float], filter: Optional[Dict], top_k: int) -> List[Dict]:
        pass

    @abstractmethod
    def delete(self, filter: Dict):
        pass

    @abstractmethod
    def scroll_all(self, filter: Optional[Dict], batch_size: int = 100) -> List[Dict]:
        pass

    @abstractmethod
    def delete_by_ids(self, ids: List[str]):
        """Delete points by exact ID match."""
        pass


class QdrantProvider(BaseVectorProvider):
    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        super().__init__(config, collection_name)
        if not HAS_QDRANT:
            raise ImportError("qdrant-client not installed")
        url = os.getenv("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY")
        if not url or not api_key:
            raise ValueError("QDRANT_URL and QDRANT_API_KEY must be set")
        # Suppress noisy client compatibility warning; we do our own reachability check.
        import warnings
        warnings.filterwarnings(
            "ignore",
            message=r"Failed to obtain server version.*",
            category=UserWarning,
            module=r"qdrant_client.*",
        )
        self._verify_connection(url, api_key)
        self.client = QdrantClient(url=url, api_key=api_key, timeout=30)

    @staticmethod
    def _verify_connection(url: str, api_key: str) -> None:
        health_url = f"{url.rstrip('/')}/collections"
        headers = {"api-key": api_key}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(health_url, headers=headers)
        except Exception as e:
            raise RuntimeError(
                f"Qdrant is unreachable at {health_url}. Check network, URL, and firewall."
            ) from e
        if resp.status_code == 401:
            raise RuntimeError(
                "Qdrant API key was rejected (401). Verify QDRANT_API_KEY."
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Qdrant reachability check failed: HTTP {resp.status_code}."
            )

    def ensure_collection(self, vector_size: int):
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.recreate_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "default": {
                        "size": vector_size,
                        "distance": "Cosine"
                    }
                },
            )

    def upsert(self, ids, vectors, payloads):
        points = [
            PointStruct(id=pid, vector=vec, payload=pay)
            for pid, vec, pay in zip(ids, vectors, payloads)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def _build_filter(self, filter: Optional[Dict]) -> Optional[QdrantFilter]:
        if not filter:
            return None
        return QdrantFilter(**filter)

    def search(self, vector, filter, top_k):
        q_filter = self._build_filter(filter)
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=vector,
            query_filter=q_filter,
            limit=top_k,
            with_payload=True,
        )
        return [{"id": str(r.id), "score": r.score, "payload": r.payload or {}} for r in results]

    def delete(self, filter):
        q_filter = self._build_filter(filter)
        self.client.delete(collection_name=self.collection_name, points_filter=q_filter)

    def delete_by_ids(self, ids):
        self.client.delete(collection_name=self.collection_name, points_selector={"ids": ids})

    def scroll_all(self, filter=None, batch_size=100):
        next_offset = None
        all_points = []
        while True:
            records, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=batch_size,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
                filter=self._build_filter(filter),
            )
            for r in records:
                all_points.append({"id": str(r.id), "payload": r.payload or {}})
            if next_offset is None:
                break
        return all_points


class PineconeProvider(BaseVectorProvider):
    def __init__(self, config: VectorMemoryConfig, collection_name: str):
        super().__init__(config, collection_name)
        if not HAS_PINECONE:
            raise ImportError("pinecone-client not installed")
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            raise ValueError("PINECONE_API_KEY must be set")
        self.pc = pinecone.Pinecone(api_key=api_key)
        self.index = None

    def ensure_collection(self, vector_size: int):
        try:
            self.pc.describe_index(self.collection_name)
        except Exception:
            self.pc.create_index(
                name=self.collection_name,
                dimension=vector_size,
                metric="cosine",
            )
        self.index = self.pc.Index(self.collection_name)

    def _ensure_index(self):
        if self.index is None:
            self.index = self.pc.Index(self.collection_name)

    def upsert(self, ids, vectors, payloads):
        self._ensure_index()
        self.index.upsert(vectors=[
            {"id": i, "values": v, "metadata": p}
            for i, v, p in zip(ids, vectors, payloads)
        ])

    def search(self, vector, filter, top_k):
        self._ensure_index()
        pine_filter = filter or {}
        result = self.index.query(
            vector=vector,
            filter=pine_filter,
            top_k=top_k,
            include_metadata=True,
        )
        return [{"id": m.id, "score": m.score, "payload": m.metadata or {}} for m in result.matches]

    def delete(self, filter):
        self._ensure_index()
        self.index.delete(filter=filter)

    def delete_by_ids(self, ids):
        self._ensure_index()
        self.index.delete(ids=ids)

    def scroll_all(self, filter=None, batch_size=100):
        self._ensure_index()
        dummy_vec = [0.0] * EmbeddingService(VectorMemoryConfig()).get_vector_size()
        huge_k = 10000
        return self.search(dummy_vec, filter, huge_k)


# =============================================================================
# Vector Memory Store (two-collection design)
# =============================================================================

class VectorMemoryStore:
    """
    Cloud-backed memory with separate curated (working set) and archive (full history) collections.
    Compatible with MemoryStore interface.
    """
    def __init__(self, config: Optional[VectorMemoryConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self.embedder = EmbeddingService(self.config)
        # Initialize two providers
        self.curated_db = self._init_provider(self.config.curated_collection)
        self.archive_db = self._init_provider(self.config.archive_collection)
        vector_size = self.embedder.get_vector_size()
        self.curated_db.ensure_collection(vector_size)
        self.archive_db.ensure_collection(vector_size)
        # Curated entries (in-memory)
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self._entry_meta: Dict[str, Dict] = {}  # entry_id -> metadata
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}
        self.ENTRY_DELIMITER = "\n§\n"

    def _init_provider(self, collection_name: str) -> BaseVectorProvider:
        provider_name = self.config.provider
        if provider_name == "auto":
            if os.getenv("QDRANT_URL") and os.getenv("QDRANT_API_KEY"):
                provider_name = "qdrant"
            elif os.getenv("PINECONE_API_KEY"):
                provider_name = "pinecone"
            elif os.getenv("ZILLIZ_API_KEY"):
                provider_name = "zilliz"
            else:
                raise ValueError("No vector database provider configured")
        if provider_name == "qdrant":
            if not HAS_QDRANT:
                raise ImportError("qdrant-client not installed. pip install 'hermes-agent[vector-memory]'")
            return QdrantProvider(self.config, collection_name)
        elif provider_name == "pinecone":
            if not HAS_PINECONE:
                raise ImportError("pinecone-client not installed. pip install 'hermes-agent[vector-memory]'")
            return PineconeProvider(self.config, collection_name)
        elif provider_name == "zilliz":
            raise NotImplementedError("Zilliz provider not implemented")
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    def load_from_disk(self):
        """Load curated working set from the curated collection."""
        self.memory_entries = []
        self.user_entries = []
        self._entry_meta = {}
        try:
            # Fetch all points from curated collection
            all_points = self.curated_db.scroll_all(batch_size=100)
            # Split by target, sort by timestamp
            mem_entries = []
            user_entries = []
            for p in all_points:
                payload = p["payload"]
                target = payload.get("target")
                if target not in ("memory", "user"):
                    continue
                entry = {
                    "entry_id": p["id"],
                    "content": payload.get("content", ""),
                    "timestamp": payload.get("timestamp", ""),
                    "target": target,
                    "char_count": payload.get("char_count", len(payload.get("content", ""))),
                }
                if target == "memory":
                    mem_entries.append(entry)
                else:
                    user_entries.append(entry)
            mem_entries.sort(key=lambda x: x["timestamp"])
            user_entries.sort(key=lambda x: x["timestamp"])
            self.memory_entries = [e["content"] for e in mem_entries]
            self.user_entries = [e["content"] for e in user_entries]
            # Build metadata map
            for e in mem_entries:
                self._entry_meta[e["entry_id"]] = e
            for e in user_entries:
                self._entry_meta[e["entry_id"]] = e
        except Exception as e:
            logging.warning(f"Failed to load curated memories from cloud: {e}")
        # Migration: if curated is empty and local files exist, migrate them to cloud
        if not self._entry_meta:
            try:
                MEMORY_DIR = get_hermes_home() / "memories"
                mem_file = MEMORY_DIR / "MEMORY.md"
                user_file = MEMORY_DIR / "USER.md"
                if mem_file.exists() or user_file.exists():
                    # Read local entries
                    migrated = []
                    if mem_file.exists():
                        raw = mem_file.read_text(encoding="utf-8")
                        entries = [e.strip() for e in raw.split(self.ENTRY_DELIMITER) if e.strip()]
                        for c in entries:
                            migrated.append((c, "memory"))
                    if user_file.exists():
                        raw = user_file.read_text(encoding="utf-8")
                        entries = [e.strip() for e in raw.split(self.ENTRY_DELIMITER) if e.strip()]
                        for c in entries:
                            migrated.append((c, "user"))
                    # Upload to both archive and curated
                    for content, target in migrated:
                        eid = str(uuid.uuid4())
                        ts = datetime.utcnow().isoformat() + "Z"
                        payload = {
                            "content": content,
                            "target": target,
                            "timestamp": ts,
                            "entry_id": eid,
                            "char_count": len(content),
                        }
                        try:
                            emb = self.embedder.embed([content])[0]
                            self.archive_db.upsert([eid], [emb], [payload])
                            self.curated_db.upsert([eid], [emb], [payload])
                        except Exception as e2:
                            logging.warning(f"Failed to migrate entry: {e2}")
                            continue
                        lst = self.memory_entries if target == "memory" else self.user_entries
                        lst.append(content)
                        self._entry_meta[eid] = payload
                    # Enforce limits after bulk migration
                    self._enforce_limit("memory")
                    self._enforce_limit("user")
            except Exception as e:
                logging.warning(f"Migration from local files failed: {e}")
        self._refresh_snapshot()

    def _refresh_snapshot(self):
        snapshot = {}
        for target in ("memory", "user"):
            entries = self.memory_entries if target == "memory" else self.user_entries
            if entries:
                content = self.ENTRY_DELIMITER.join(entries)
                current = len(content)
                limit = self._char_limit(target)
                pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
                if target == "user":
                    header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
                else:
                    header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"
                separator = "═" * 46
                snapshot[target] = f"{separator}\n{header}\n{separator}\n{content}"
            else:
                snapshot[target] = ""
        self._system_prompt_snapshot = snapshot

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -------------------------------------------------------------------------
    # Mutations
    # -------------------------------------------------------------------------

    def add(self, target: str, content: str) -> Dict[str, Any]:
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}
        entry_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"
        char_count = len(content)
        payload = {
            "content": content,
            "target": target,
            "timestamp": timestamp,
            "entry_id": entry_id,
            "char_count": char_count,
        }
        embedding = self.embedder.embed([content])[0]
        try:
            # Insert into both collections
            self.archive_db.upsert([entry_id], [embedding], [payload])
            self.curated_db.upsert([entry_id], [embedding], [payload])
        except Exception as e:
            return {"success": False, "error": f"Cloud upload failed: {str(e)}"}
        # Add to curated in-memory list
        entries = self.memory_entries if target == "memory" else self.user_entries
        entries.append(content)
        self._entry_meta[entry_id] = payload
        self._enforce_limit(target)
        self._refresh_snapshot()
        return self._success_response(target, "Entry added to cloud (archive+curated).")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty."}
        # Find entry_id in curated entries
        entries = self.memory_entries if target == "memory" else self.user_entries
        entry_id = None
        idx = None
        for i, entry in enumerate(entries):
            if old_text in entry:
                # Verify it's the exact one we want (could be duplicates)
                entry_id = self._find_entry_id_by_content(entry, target)
                if entry_id:
                    idx = i
                    break
        if entry_id is None or idx is None:
            return {"success": False, "error": f"No entry matched '{old_text}' in curated {target}."}
        # Prepare new payload (keep same entry_id, update content, timestamp, embedding)
        new_timestamp = datetime.utcnow().isoformat() + "Z"
        new_char_count = len(new_content)
        new_payload = {
            "content": new_content,
            "target": target,
            "timestamp": new_timestamp,
            "entry_id": entry_id,
            "char_count": new_char_count,
        }
        new_embedding = self.embedder.embed([new_content])[0]
        try:
            # Update both collections
            self.archive_db.upsert([entry_id], [new_embedding], [new_payload])
            self.curated_db.upsert([entry_id], [new_embedding], [new_payload])
        except Exception as e:
            return {"success": False, "error": f"Cloud update failed: {str(e)}"}
        # Update in-memory list
        entries[idx] = new_content
        self._entry_meta[entry_id] = new_payload
        self._enforce_limit(target)
        self._refresh_snapshot()
        return self._success_response(target, "Entry replaced in cloud (archive+curated).")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        entries = self.memory_entries if target == "memory" else self.user_entries
        idx_to_remove = None
        entry_id = None
        for i, entry in enumerate(entries):
            if old_text in entry:
                eid = self._find_entry_id_by_content(entry, target)
                if eid:
                    entry_id = eid
                    idx_to_remove = i
                    break
        if idx_to_remove is None or entry_id is None:
            return {"success": False, "error": f"No entry matched '{old_text}' in curated {target}."}
        # Delete from both collections
        try:
            self.archive_db.delete_by_ids([entry_id])
            self.curated_db.delete_by_ids([entry_id])
        except Exception as e:
            logging.warning(f"Failed to delete entry {entry_id} from cloud: {e}")
        # Remove from in-memory list
        del entries[idx_to_remove]
        if entry_id in self._entry_meta:
            del self._entry_meta[entry_id]
        self._refresh_snapshot()
        return self._success_response(target, "Entry removed from cloud (archive+curated).")

    def _find_entry_id_by_content(self, content: str, target: str) -> Optional[str]:
        for eid, meta in self._entry_meta.items():
            if meta.get("target") == target and meta.get("content") == content:
                return eid
        return None

    def _char_count(self, target: str) -> int:
        entries = self.memory_entries if target == "memory" else self.user_entries
        if not entries:
            return 0
        return len(self.ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        return self.config.memory_char_limit if target == "memory" else self.config.user_char_limit

    def _enforce_limit(self, target: str):
        """Demote oldest curated entries until within char limit."""
        entries = self.memory_entries if target == "memory" else self.user_entries
        limit = self._char_limit(target)
        # Build list of (timestamp, entry_id, index) for current curated entries
        meta_list = []
        for idx, content in enumerate(entries):
            eid = self._find_entry_id_by_content(content, target)
            if eid:
                meta = self._entry_meta.get(eid, {})
                ts = meta.get("timestamp", "")
                meta_list.append((ts, eid, idx))
        meta_list.sort(key=lambda x: x[0])  # oldest first
        while self._char_count(target) > limit and meta_list:
            ts, eid, idx = meta_list.pop(0)
            # Remove from curated collection (archive remains)
            try:
                self.curated_db.delete_by_ids([eid])
            except Exception as e:
                logging.warning(f"Failed to demote entry {eid} from curated: {e}")
            # Remove from in-memory list
            del entries[idx]
            if eid in self._entry_meta:
                del self._entry_meta[eid]
            # After removal, indices changed; recompute meta_list for remaining entries
            meta_list = []
            for i, content in enumerate(entries):
                eid2 = self._find_entry_id_by_content(content, target)
                if eid2:
                    meta2 = self._entry_meta.get(eid2, {})
                    ts2 = meta2.get("timestamp", "")
                    meta_list.append((ts2, eid2, i))
            meta_list.sort(key=lambda x: x[0])

    def search(self, query: str, top_k: int = 10, target: Optional[str] = None) -> List[Dict]:
        """Semantic search over the archive collection."""
        if not query.strip():
            return []
        try:
            query_embedding = self.embedder.embed([query])[0]
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {e}")
        # Build filter if target specified
        filter_dict = None
        if target in ("memory", "user"):
            filter_dict = None  # we'll filter client-side
        raw_results = self.archive_db.search(query_embedding, filter=filter_dict, top_k=top_k * 2)  # fetch more to allow filtering
        results = []
        for r in raw_results:
            payload = r["payload"]
            if target and payload.get("target") != target:
                continue
            results.append({
                "content": payload.get("content", ""),
                "target": payload.get("target"),
                "score": r["score"],
                "timestamp": payload.get("timestamp"),
            })
            if len(results) >= top_k:
                break
        return results

    def _char_count_pct(self, target: str) -> int:
        current = self._char_count(target)
        limit = self._char_limit(target)
        return min(100, int((current / limit) * 100)) if limit > 0 else 0

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self.memory_entries if target == "memory" else self.user_entries
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = self._char_count_pct(target)
        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp


# =============================================================================
# Factory
# =============================================================================

def create_vector_memory_store(config: Optional[Dict] = None) -> Optional[VectorMemoryStore]:
    """
    Factory to create a VectorMemoryStore if configuration enables it.
    """
    try:
        if config is None:
            try:
                from hermes_cli.config import load_config
                hermes_cfg = load_config()
            except ImportError:
                # Fallback to direct file read
                import yaml
                from hermes_constants import get_hermes_home
                config_path = get_hermes_home() / "config.yaml"
                if config_path.exists():
                    with open(config_path) as f:
                        hermes_cfg = yaml.safe_load(f) or {}
                else:
                    hermes_cfg = {}
            vec_mem_cfg = hermes_cfg.get("vector_memory", {})
        else:
            vec_mem_cfg = config
        if not vec_mem_cfg.get("enabled", False):
            return None
        vm_config = VectorMemoryConfig(
            provider=vec_mem_cfg.get("provider", "auto"),
            embedding_model=vec_mem_cfg.get("embedding_model", "nvidia/llama-nemotron-embed-vl-1b-v2:free"),
            embedding_base_url=vec_mem_cfg.get("embedding_base_url"),
            embedding_api_key_env=vec_mem_cfg.get("embedding_api_key_env", "OPENROUTER_API_KEY"),
            collection_base_name=vec_mem_cfg.get("collection_name", "hermes_memory"),
            memory_char_limit=vec_mem_cfg.get("memory_char_limit", 2200),
            user_char_limit=vec_mem_cfg.get("user_char_limit", 1375),
        )
        return VectorMemoryStore(vm_config)
    except ImportError as e:
        logging.warning(f"Vector memory dependencies missing: {e}. Install with 'pip install \"hermes-agent[vector-memory]\"'")
        return None
    except Exception as e:
        logging.warning(f"Failed to initialize vector memory store: {e}")
        return None
