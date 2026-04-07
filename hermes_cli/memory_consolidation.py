#!/usr/bin/env python3
"""
Memory Consolidation Engine for Hermes Hierarchical Workflow Memory.

Based on findings from "Agent Workflow Memory" (Chen & Callan, 2026) and
TextGrad-style self-improvement: memory quality degrades when redundant
entries accumulate. The system must periodically consolidate similar
memories, promote high-value ones, and prune noise.

Consolidation Pipeline
======================
1. Deduplication    -- Semantic similarity check removes near-duplicates
2. Merging          -- Related memories in the same tier are merged
3. Promotion        -- High-quality phase memories graduate to workflow/global
4. Pruning          -- Low-relevance, zero-access memories are removed
5. Tagging          -- Memories are tagged with domain labels for retrieval

Triggered:
  - On memory_offload (when MEMORY.md approaches capacity)
  - As a cron job every 6 hours (aligned with evolution cycle)
  - Manually via 'hwm consolidate' command

Usage
=====
  from hermes_cli.memory_consolidation import consolidate_all

  # Full consolidation cycle
  stats = consolidate_all()
  print(f"Merged {stats['merged']}, pruned {stats['pruned']}, promoted {stats['promoted']}")
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger("memory-consolidation")

# ── Configuration ──────────────────────────────────────────────────────

# Similarity threshold for deduplication (0-1, higher = stricter)
DEDUP_SIMILARITY_THRESHOLD = 0.85

# Minimum access count to survive pruning
MIN_ACCESS_SURVIVAL = 0

# Maximum memories per tier before forcing consolidation
MAX_PHASE_MEMORIES = 200
MAX_WORKFLOW_MEMORIES = 500
MAX_GLOBAL_MEMORIES = 300

# Tier promotion thresholds
PROMOTION_ACCESS_THRESHOLD = 3      # Access count needed for promotion
PROMOTION_RELEVANCE_THRESHOLD = 0.5  # Relevance score needed for promotion


# ── Lightweight text similarity (no external deps) ─────────────────────

def jaccard_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts using word-level tokens."""
    tokens1 = set(_tokenize(text1))
    tokens2 = set(_tokenize(text2))
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    return len(intersection) / len(union)


def cosine_similarity_simple(text1: str, text2: str) -> float:
    """Simple character n-gram cosine similarity (good enough for dedup)."""
    ngrams1 = _char_ngrams(text1, n=4)
    ngrams2 = _char_ngrams(text2, n=4)
    if not ngrams1 or not ngrams2:
        return 0.0

    # Dot product
    dot = sum(1 for g in ngrams1 if g in ngrams2)
    # Magnitudes
    mag1 = math.sqrt(len(ngrams1))
    mag2 = math.sqrt(len(ngrams2))

    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def combined_similarity(text1: str, text2: str) -> float:
    """Weighted combination of Jaccard and n-gram cosine."""
    j = jaccard_similarity(text1, text2)
    c = cosine_similarity_simple(text1, text2)
    return 0.4 * j + 0.6 * c


def _tokenize(text: str) -> List[str]:
    """Simple tokenizer: lowercase, strip punctuation, split."""
    return re.findall(r'[a-z0-9]+', text.lower())


def _char_ngrams(text: str, n: int = 4) -> set:
    """Extract character n-grams from text."""
    text = text.lower().strip()
    if len(text) < n:
        return {text}
    return {text[i:i+n] for i in range(len(text) - n + 1)}


# ── Consolidation Engine ──────────────────────────────────────────────

class MemoryConsolidator:
    """Manages memory consolidation using similarity-based deduplication
    and tier promotion."""

    def __init__(self, hwm_store=None, db_path: Optional[Path] = None):
        if hwm_store is not None:
            self.hwm = hwm_store
        else:
            from hermes_cli.hierarchical_memory import HWMStore
            self.hwm = HWMStore(db_path=db_path)

    def consolidate_all(self) -> Dict[str, int]:
        """Run full consolidation cycle. Returns stats dict."""
        stats = {
            "duplicates_found": 0,
            "merged": 0,
            "promoted": 0,
            "pruned": 0,
            "tier_counts": {},
        }

        # Phase 1: Deduplicate within each tier
        stats["duplicates_found"] = self._deduplicate_all()

        # Phase 2: Merge related memories
        stats["merged"] = self._merge_related()

        # Phase 3: Promote high-quality memories up the tier hierarchy
        stats["promoted"] = self._promote_memories()

        # Phase 4: Prune low-value memories
        stats["pruned"] = self._prune_low_value()

        # Phase 5: Report tier counts
        tier_stats = self.hwm.stats()
        stats["tier_counts"] = {
            k: v for k, v in tier_stats.items() if "count" in k
        }

        logger.info("Consolidation complete: %s",
                     json.dumps({k: v for k, v in stats.items()
                                 if k != "tier_counts"}))
        return stats

    def _deduplicate_all(self) -> int:
        """Find and remove duplicate/near-duplicate memories across tiers."""
        duplicates_removed = 0

        for tier in ["phase", "workflow", "global"]:
            duplicates_removed += self._deduplicate_tier(tier)

        return duplicates_removed

    def _deduplicate_tier(self, tier: str) -> int:
        """Remove near-duplicate memories within a single tier."""
        # For efficiency, group by first 2 words (prefix bucketing)
        memories = self.hwm.retrieve(f"*", top_k=500)
        tier_memories = [m for m in memories if m["tier"] == tier]

        if len(tier_memories) < 2:
            return 0

        # Bucket by first 2 words for efficiency
        buckets = defaultdict(list)
        for m in tier_memories:
            prefix = " ".join(_tokenize(m["content"])[:3])
            buckets[prefix].append(m)

        removed = 0
        seen_ids = set()

        for prefix, group in buckets.items():
            if len(group) < 2:
                continue

            for i, m1 in enumerate(group):
                if m1["id"] in seen_ids:
                    continue
                for j, m2 in enumerate(group[i+1:], i+1):
                    if m2["id"] in seen_ids:
                        continue

                    sim = combined_similarity(m1["content"], m2["content"])
                    if sim >= DEDUP_SIMILARITY_THRESHOLD:
                        # Keep the one with higher access count
                        if m2["access_count"] >= m1["access_count"]:
                            self.hwm.delete_memory(m1["id"])
                            seen_ids.add(m1["id"])
                        else:
                            self.hwm.delete_memory(m2["id"])
                            seen_ids.add(m2["id"])
                        removed += 1
                        logger.debug("Dedup[%s]: %s ~ %s (%.2f)",
                                    tier,
                                    m1["content"][:60],
                                    m2["content"][:60],
                                    sim)

        return removed

    def _merge_related(self) -> int:
        """Merge highly related memories within the same tier+workflow."""
        memories = self.hwm.retrieve(f"*", top_k=500)
        merged = 0

        # Group by workflow_id + tier
        groups = defaultdict(list)
        for m in memories:
            key = (m["workflow_id"] or "none", m["tier"])
            groups[key].append(m)

        for (wid, tier), group in groups.items():
            if len(group) < 3:
                continue  # Need at least 3 to meaningfully merge

            # Try to merge related memories that share keywords
            keyword_groups = defaultdict(list)
            for m in group:
                tokens = set(_tokenize(m["content"]))
                # Use most common token as grouping key
                if tokens:
                    key_token = sorted(tokens)[0]  # Simple heuristic
                    keyword_groups[key_token].append(m)

            for ktoken, sub_group in keyword_groups.items():
                if 3 <= len(sub_group) <= 10:
                    # Merge into a single memory
                    contents = [m["content"] for m in sub_group]
                    merged_content = self._merge_texts(contents)

                    if merged_content and len(merged_content) < 500:
                        # Write merged memory
                        self.hwm.write_memory(
                            content=merged_content,
                            tier=tier,
                            workflow_id=wid if wid != "none" else None,
                            source="consolidation",
                        )

                        # Delete the originals
                        for m in sub_group:
                            self.hwm.delete_memory(m["id"])

                        merged += 1

        return merged

    def _promote_memories(self) -> int:
        """Promote high-quality memories from phase->workflow->global."""
        promoted = 0

        # Find phase memories worthy of promotion to workflow
        phase_memories = self.hwm.retrieve(f"*", top_k=200)
        for m in phase_memories:
            if m["tier"] != "phase":
                continue
            if (m["access_count"] >= PROMOTION_ACCESS_THRESHOLD and
                    m.get("score", 0) >= PROMOTION_RELEVANCE_THRESHOLD):
                self.hwm.promote_to_workflow(m["id"])
                promoted += 1
                logger.debug("Promoted to workflow: %s", m["content"][:60])

        # Find workflow memories worthy of promotion to global
        for m in phase_memories:
            if m["tier"] != "workflow":
                continue
            if (m["access_count"] >= PROMOTION_ACCESS_THRESHOLD * 2 and
                    m.get("score", 0) >= PROMOTION_RELEVANCE_THRESHOLD * 1.5):
                self.hwm.promote_to_global(m["id"])
                promoted += 1
                logger.debug("Promoted to global: %s", m["content"][:60])

        return promoted

    def _prune_low_value(self) -> int:
        """Remove memories that are no longer valuable."""
        # Use the built-in consolidation method
        pruned = self.hwm.consolidate_low_relevance(threshold=0.05)
        if pruned:
            logger.info("Pruned %d low-relevance memories", pruned)

        # Also prune old phases
        old_phases = self.hwm.prune_expired_phases(older_than_days=7)
        if old_phases:
            logger.info("Pruned %d memories from expired phases", old_phases)

        # Enforce tier size limits
        stats = self.hwm.stats()
        if stats.get("phase_count", 0) > MAX_PHASE_MEMORIES:
            # Drop oldest phase-tier memories
            self._enforce_tier_limit("phase", MAX_PHASE_MEMORIES)

        return pruned + old_phases

    def _enforce_tier_limit(self, tier: str, max_count: int) -> int:
        """Delete oldest memories in a tier to stay under the limit."""
        # This is a best-effort operation
        memories = self.hwm.retrieve(f"*", top_k=max_count + 50)
        tier_memories = [m for m in memories if m["tier"] == tier]

        if len(tier_memories) <= max_count:
            return 0

        # Sort by access count (keep most accessed), delete rest
        tier_memories.sort(key=lambda m: m["access_count"])
        to_delete = tier_memories[:len(tier_memories) - max_count]

        deleted = 0
        for m in to_delete:
            self.hwm.delete_memory(m["id"])
            deleted += 1

        return deleted

    @staticmethod
    def _merge_texts(texts: List[str]) -> str:
        """Merge multiple related texts into a single coherent memory."""
        if not texts:
            return ""
        if len(texts) == 1:
            return texts[0]

        # Simple merge: take the longest text and append unique info from others
        longest = max(texts, key=len)
        tokens_longest = set(_tokenize(longest))

        additions = []
        for t in texts:
            if t == longest:
                continue
            tokens_t = set(_tokenize(t))
            unique_tokens = tokens_t - tokens_longest
            if len(unique_tokens) > len(tokens_t) * 0.3:
                # Has meaningful unique content
                additions.append(t)

        if additions:
            merged = longest + ". Also: " + ". ".join(additions)
            # Truncate to reasonable length
            if len(merged) > 800:
                merged = merged[:797] + "..."
            return merged

        return longest


# ── Public API ─────────────────────────────────────────────────────────

def consolidate_all() -> Dict[str, int]:
    """Run full consolidation cycle. Returns stats dict."""
    consolidator = MemoryConsolidator()
    return consolidator.consolidate_all()


def check_consolidation_needed() -> bool:
    """Check if consolidation should be triggered based on tier counts."""
    from hermes_cli.hierarchical_memory import HWMStore
    hwm = HWMStore()
    stats = hwm.stats()

    if stats.get("phase_count", 0) > MAX_PHASE_MEMORIES:
        return True
    if stats.get("workflow_count", 0) > MAX_WORKFLOW_MEMORIES:
        return True
    if stats.get("global_count", 0) > MAX_GLOBAL_MEMORIES:
        return True

    return False


# ── CLI entry point ───────────────────────────────────────────────────

def main():
    """CLI: run consolidation and print results."""
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        stream=sys.stdout,
    )

    stats = consolidate_all()
    print("Memory Consolidation Results:")
    print(f"  Duplicates found: {stats['duplicates_found']}")
    print(f"  Merged:           {stats['merged']}")
    print(f"  Promoted:         {stats['promoted']}")
    print(f"  Pruned:           {stats['pruned']}")
    print(f"\nTier counts: {json.dumps(stats['tier_counts'], indent=2)}")


if __name__ == "__main__":
    main()
