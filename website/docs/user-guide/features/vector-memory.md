# Cloud Vector Memory Enhancement

This enhancement adds cloud-backed unlimited memory with semantic search to Hermes Agent.

## Overview

- **Vector Databases**: Qdrant or Pinecone (using existing .env keys)
- **Embeddings**: Free-tier OpenRouter models (NVIDIA Nemotron, Mistral, Cohere, Gemini)
- **Collections**: Two-collection design (`hermes_memory_curated` + `hermes_memory_archive`)
- **Migration**: Automatic migration from local `~/ .hermes/memories/*.md` on first load

## Configuration

Enable in `~/.hermes/config.yaml`:

```yaml
vector_memory:
  enabled: true                # Switch to cloud memory
  provider: auto               # qdrant, pinecone, or auto-detect from env
  embedding_model: nvidia/llama-nemotron-embed-vl-1b-v2:free  # free default
  embedding_api_key_env: OPENROUTER_API_KEY
  collection_name: hermes_memory
  memory_char_limit: 2200      # curated working set size (system prompt)
  user_char_limit: 1375        # user profile working set size
```

The existing `memory` section remains for character limits and flush behavior.

## Requirements

Install dependencies:

```bash
pip install "hermes-agent[vector-memory]"
```

This installs: `qdrant-client`, `pinecone-client`, `pymilvus` (optional), `numpy`, `httpx`.

## Cloud Credentials

Ensure your `~/.hermes/.env` has one of:

- **Qdrant**: `QDRANT_URL` + `QDRANT_API_KEY` (already present)
- **Pinecone**: `PINECONE_API_KEY` (already present)
- **Zilliz**: `ZILLIZ_API_KEY` (support coming)

## How It Works

1. **Dual Collections**:
   - `<collection>_curated`: small working set (fits in system prompt, ~2200 chars)
   - `<collection>_archive`: full historical memory (unlimited)

2. **Operations**:
   - `memory add/replace/remove` → writes to **both** collections
   - Curated in-memory list enforces char limits; oldest entries are demoted (removed from curated, kept in archive)
   - Semantic search (`semantic_memory_search`) queries the **archive** collection

3. **Migration**:
   On first load with `enabled: true`, if cloud curated collection is empty but local files exist, entries are automatically uploaded to both collections.

4. **Embedding Models**:
   Free model fallback chain (via OpenRouter):
   - `nvidia/llama-nemotron-embed-v1-1b-v2:free` (default, 2048 dim)
   - `mistralai/mistral-embed:free` (4096 dim)
   - `cohere/embed-english-v3.0:free` (1024 dim)
   - `google/gemini-embedding-exp-03-07:free` (768 dim)

   The service automatically tries each until one succeeds.

## Usage

### Standard Memory Operations (unchanged)

The agent still uses the `memory` tool:

```
memory(action="add", target="memory", content="...")
memory(action="replace", target="user", old_text="old", new_content="new")
memory(action="remove", target="memory", old_text="...")
```

These now automatically archive to the cloud.

### Semantic Search (new)

When the agent needs to recall something from long-term memory:

```
semantic_memory_search(query="What did the user say about their GPU setup?", top_k=10)
```

Returns JSON with results:
```json
{
  "success": true,
  "query": "...",
  "results": [
    {"content": "...", "target": "memory", "score": 0.87, "timestamp": "..."},
    ...
  ],
  "count": 5
}
```

- `target`: `"memory"` or `"user"` to filter by store
- `score`: similarity score (0-1, higher = more relevant)
- `top_k`: max results (default 10)

## Behavior Differences from File-Based Memory

- **Unlimited capacity**: Archive stores everything; curated subset is still character-limited.
- **Automatic demotion**: When curated set is full, oldest entries are demoted to archive and removed from the working set (still searchable).
- **Semantic retrieval**: Find memories by meaning, not just keywords.
- **Migration**: Original `MEMORY.md` and `USER.md` are uploaded once on first cloud enable; after that the agent uses cloud exclusively. Local files remain unchanged but are no longer used.

## Fallback Behavior

If `vector_memory.enabled: true` but dependencies are missing or cloud connection fails, the agent falls back to file-based memory automatically.

## Advanced

### Provider-specific collection settings

- **Qdrant**: Ensure collections exist with `Cosine` distance; the `ensure_collection` call creates them with correct vector size.
- **Pinecone**: Serverless indexes are created automatically with the determined vector dimension.

### Changing embedding model

Edit `config.yaml`:

```yaml
vector_memory:
  embedding_model: mistralai/mistral-embed:free
```

The vector size is auto-detected.

### Manual collection creation (optional)

The `ensure_collection` method is called on startup; manual creation is not required.

## Troubleshooting

1. **Import errors**: Install `hermes-agent[vector-memory]`.
2. **Authentication**: Verify `QDRANT_URL`/`QDRANT_API_KEY` or `PINECONE_API_KEY` in `.env`.
3. **Dimension mismatch**: If changing models, ensure old collection matches new vector size or delete and recreate.
4. **Embedding failures**: Check OpenRouter API key (`OPENROUTER_API_KEY`) and network connectivity.
5. **Migration stuck**: Large local memories are embedded sequentially; watch logs for warnings.

## Performance Notes

- Embedding generation is asynchronous per add; the tool call waits for embedding before returning.
- Cloud latency: add/replace/remove operations make at least 2 network calls (curated + archive). Consider batching if needed (currently not implemented).
- Demotion may be slightly O(n²) but n ≤ ~15 (curated count) so fine.
- Vector search is O(1) with approximate nearest neighbor from cloud provider.
