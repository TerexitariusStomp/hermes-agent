"""Long Context Processor — File-System Externalized Context Processing.

Based on paper arXiv 2603.20432: large coding agents can process trillion-
token contexts by externalizing from attention to filesystem manipulation,
using native tools (grep, find, python scripts) instead of semantic search.

Strategy
--------
1. PHASE 1 — FAST FILTERING: use grep (regex, fixed-string, word-match) to
   identify files that mention query terms.
2. PHASE 2 — CHUNK EXTRACTION: split matched files into semantic chunks
   (by function/class/paragraph boundaries), score each chunk against the
   query, and keep only the top-k.
3. PHASE 3 — SYNTHESIS: combine the highest-scoring chunks into a concise,
   sourced answer with full provenance (file paths, line ranges, scores).

Key insight: don't stuff everything into the context window.  Use the
filesystem as external memory and let code execution do the heavy processing.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import os
import re
import time

from tools.registry import registry
import argparse, sys


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    """Manifest entry for a single file."""
    path: str
    size: int
    file_type: str
    mtime: float
    language: str = "unknown"
    match_score: float = 0.0


@dataclass
class Chunk:
    """A semantic chunk of a file."""
    file_path: str
    chunk_id: str
    text: str
    start_line: int
    end_line: int
    chunk_type: str = "default"   # function, class, paragraph, default
    score: float = 0.0


@dataclass
class ProcessingResult:
    output: str
    phase1_files: List[Dict] = field(default_factory=list)
    phase2_chunks: List[Dict] = field(default_factory=list)
    total_files_scanned: int = 0
    total_chunks_examined: int = 0
    processing_time_sec: float = 0.0


# ---------------------------------------------------------------------------
# Language heuristics
# ---------------------------------------------------------------------------

_EXT_LANG: Dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".java": "java", ".cpp": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".md": "markdown", ".txt": "text",
    ".html": "html", ".css": "css", ".sh": "bash",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".xml": "xml",
    ".sql": "sql",
}


def _detect_language(path: str) -> str:
    return _EXT_LANG.get(Path(path).suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# MANIFEST BUILDER
# ---------------------------------------------------------------------------

def build_manifest(directory: str) -> List[FileEntry]:
    index = []
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                st = os.stat(fpath)
                index.append(FileEntry(
                    path=fpath,
                    size=st.st_size,
                    file_type=Path(fname).suffix,
                    mtime=st.st_mtime,
                    language=_detect_language(fname),
                ))
            except OSError:
                continue
    return index


# ---------------------------------------------------------------------------
# PHASE 1 — Fast filtering with grep
# ---------------------------------------------------------------------------

def _extract_query_terms(query: str) -> List[str]:
    """Pull individual keywords from the query."""
    terms = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', query)
    # remove common stop words
    stops = {"the", "and", "for", "are", "but", "not", "you", "all",
             "with", "thee", "that", "this", "from", "what", "when",
             "where", "which", "who", "how", "can", "has", "was",
             "been", "have", "had", "his", "her", "into", "also"}
    return [t for t in terms if t.lower() not in stops]


def phase1_filter(manifest: List[FileEntry], query: str,
                   max_files: int = 50) -> List[FileEntry]:
    """grep-based fast filter."""
    terms = _extract_query_terms(query)
    if not terms:
        terms = query.split()
    scored: Dict[str, Tuple[FileEntry, int]] = {}

    for entry in manifest:
        if entry.size > 10 * 1024 * 1024:  # skip files > 10 MB
            continue
        try:
            with open(entry.path, "r", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue
        hits = 0
        for term in terms:
            hits += len(re.findall(re.escape(term), content, re.IGNORECASE))
        if hits > 0:
            scored[entry.path] = (entry, hits)

    # Sort by hit count descending, then by recent mtime (tie-break)
    ranked = sorted(scored.values(),
                    key=lambda ea: (-ea[1], -ea[0].mtime))[:max_files]
    for entry, hits in ranked:
        entry.match_score = float(hits)
    return [e for e, _ in ranked]


# ---------------------------------------------------------------------------
# PHASE 2 — Chunk extraction with semantic boundaries
# ---------------------------------------------------------------------------

# Patterns for code-aware chunk boundaries
_BOUNDARY_PATTERNS: Dict[str, re.Pattern] = {
    "python":  re.compile(r'^(?:class\s+\w+|def\s+\w+|async\s+def\s+\w+)', re.MULTILINE),
    "javascript": re.compile(r'^(?:class\s+\w+|function\s+\w+|const\s+\w+\s*=\s*(?:async\s*)?\w+\s*=>)', re.MULTILINE),
    "typescript":  re.compile(r'^(?:class\s+\w+|function\s+\w+|const\s+\w+\s*:\s*)', re.MULTILINE),
    "java":     re.compile(r'^(?:public|private|protected|class|interface)\s+\w+.*\{', re.MULTILINE),
    "cpp":      re.compile(r'^(?:class|struct|void|int|double|float)\s+\w+\s*[\(:{]', re.MULTILINE),
    "go":       re.compile(r'^(?:func|type)\s+\w+', re.MULTILINE),
    "rust":     re.compile(r'^(?:fn\s+\w+|struct\s+\w+|impl\s+\w+|enum\s+\w+|trait\s+\w+)', re.MULTILINE),
    "ruby":     re.compile(r'^(?:class|module|def)\s+\w+', re.MULTILINE),
    "default":  re.compile(r'^(?:[A-Z][^\n]{3,}(?:\n|$)|#{1,2}\s+.+)', re.MULTILINE),
}


def _chunk_file(file_path: str, language: str = "unknown",
                max_chunk_lines: int = 200) -> List[Chunk]:
    """Split a file into semantic chunks."""
    try:
        with open(file_path, "r", errors="ignore") as f:
            content = f.read()
    except OSError:
        return []

    lines = content.split("\n")
    pattern = _BOUNDARY_PATTERNS.get(language, _BOUNDARY_PATTERNS["default"])
    chunks: List[Chunk] = []

    # Find boundary line indices
    boundaries = [0]
    for i, line in enumerate(lines):
        if pattern.search(line):
            boundaries.append(i)
    boundaries.append(len(lines))

    # Build chunks between boundaries
    for idx in range(len(boundaries) - 1):
        start = boundaries[idx]
        end = boundaries[idx + 1]
        chunk_lines = lines[start:end]

        # If chunk is too large, subdivide
        sub_chunks = _subdivide_chunk(chunk_lines, start, max_chunk_lines)
        for sc_start, sc_end, sc_text in sub_chunks:
            ctype = _classify_chunk(sc_text, language)
            chunk_id = f"{Path(file_path).name}:L{sc_start + 1}-L{sc_end}"
            chunks.append(Chunk(
                file_path=file_path,
                chunk_id=chunk_id,
                text=sc_text,
                start_line=sc_start + 1,   # 1-based
                end_line=sc_end,
                chunk_type=ctype,
            ))

    return chunks


def _subdivide_chunk(lines: List[str], base_idx: int,
                     max_lines: int) -> List[Tuple[int, int, str]]:
    if len(lines) <= max_lines:
        return [(base_idx, base_idx + len(lines), "\n".join(lines))]
    parts = []
    for i in range(0, len(lines), max_lines):
        segment = lines[i:i + max_lines]
        s = base_idx + i
        e = base_idx + i + len(segment)
        parts.append((s, e, "\n".join(segment)))
    return parts


def _classify_chunk(text: str, language: str) -> str:
    first_line = text.split("\n")[0].strip() if text else ""
    if language == "python":
        if first_line.startswith("class "):
            return "class"
        if first_line.startswith("def ") or first_line.startswith("async def "):
            return "function"
    elif language in ("javascript", "typescript"):
        if first_line.startswith("class "):
            return "class"
        if first_line.startswith("function ") or " =>" in first_line:
            return "function"
    if first_line.startswith("#") or first_line.startswith("//"):
        return "comment"
    return "default"


def _score_chunk(chunk: Chunk, query: str) -> float:
    tf = query.lower().split()
    text_lower = chunk.text.lower()
    hit_count = sum(text_lower.count(t) for t in tf)
    # Weight by match density
    length = max(len(text_lower), 1)
    density = hit_count / length
    # Bonus for type-specific chunks
    type_bonus = 1.5 if chunk.chunk_type in ("class", "function") else 1.0
    # Bonus for exact phrase match
    exact_bonus = 1.0
    if query.lower() in text_lower:
        exact_bonus = 3.0
    chunk.score = density * type_bonus * exact_bonus * 100
    return chunk.score


def phase2_extract(matched_files: List[FileEntry], query: str,
                    top_k: int = 20, max_chunk_lines: int = 200) -> List[Chunk]:
    """Extract and score chunks from matched files."""
    all_chunks: List[Chunk] = []
    for feat in matched_files:
        chunks = _chunk_file(feat.path, feat.language, max_chunk_lines)
        for c in chunks:
            _score_chunk(c, query)
        all_chunks.extend(chunks)

    # Sort by score descending, take top-k
    all_chunks.sort(key=lambda c: -c.score)
    return all_chunks[:top_k]


# ---------------------------------------------------------------------------
# PHASE 3 — Synthesis
# ---------------------------------------------------------------------------

def phase3_synthesize(chunks: List[Chunk], query: str) -> str:
    """Build a sourced answer from scored chunks."""
    if not chunks:
        return "No relevant content found in the given directory."

    lines: List[str] = []
    lines.append(f"=== Analysis of: \"{query}\" ===\n")

    # Group by file for readability
    by_file: Dict[str, List[Chunk]] = {}
    for c in chunks:
        by_file.setdefault(c.file_path, []).append(c)

    for fpath, fchunks in by_file.items():
        lines.append(f"\n--- {fpath} ({len(fchunks)} chunks) ---")
        for c in fchunks:
            score_str = f"[score={c.score:.1f}]"
            lines.append(f"  {score_str} Lines {c.start_line}-{c.end_line} ({c.chunk_type}):")
            # Preview first 3 lines of each chunk
            preview_lines = c.text.split("\n")[:3]
            for pl in preview_lines:
                lines.append(f"    │ {pl}")
            if len(c.text.split("\n")) > 3:
                lines.append("    │ ...")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manifest serialization helpers
# ---------------------------------------------------------------------------

def save_manifest(manifest: List[FileEntry], manifest_path: str):
    """Persist manifest to a JSON file for re-use."""
    data = [asdict(e) for e in manifest]
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)


def load_manifest(manifest_path: str) -> List[FileEntry]:
    with open(manifest_path, "r") as f:
        data = json.load(f)
    return [FileEntry(**d) for d in data]


# ---------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ---------------------------------------------------------------------------

def process_long_context(
    query: str,
    directory: str,
    max_files: int = 50,
    top_k_chunks: int = 20,
    max_chunk_lines: int = 200,
    manifest_path: Optional[str] = None,
    rebuild_manifest: bool = False,
) -> str:
    """Process a query over a directory of text files.

    Args:
        query: Natural-language query or search terms.
        directory: Root directory to search.
        max_files: Maximum files to process in Phase 2.
        top_k_chunks: Number of top-scoring chunks to retain.
        max_chunk_lines: Max lines per chunk before subdividing.
        manifest_path: Optional path to persist/reuse the file manifest.
        rebuild_manifest: If True, rebuild even if manifest exists.

    Returns:
        JSON string containing the synthesis result and full provenance.
    """
    t0 = time.time()
    directory = os.path.abspath(os.path.expanduser(directory))

    if not os.path.isdir(directory):
        return json.dumps({"error": f"Not a directory: {directory}"})

    # Manifest
    manifest: List[FileEntry] = []
    if manifest_path and os.path.exists(manifest_path) and not rebuild_manifest:
        manifest = load_manifest(manifest_path)
    else:
        manifest = build_manifest(directory)
        if manifest_path:
            save_manifest(manifest, manifest_path)

    total_files = len(manifest)

    # Phase 1
    matched = phase1_filter(manifest, query, max_files)

    # Phase 2
    chunks = phase2_extract(matched, query, top_k_chunks, max_chunk_lines)

    # Phase 3
    synthesis = phase3_synthesize(chunks, query)

    elapsed = time.time() - t0

    result = ProcessingResult(
        output=synthesis,
        phase1_files=[asdict(e) for e in matched],
        phase2_chunks=[{
            "chunk_id": c.chunk_id,
            "score": round(c.score, 2),
            "type": c.chunk_type,
            "lines": f"{c.start_line}-{c.end_line}",
        } for c in chunks],
        total_files_scanned=total_files,
        total_chunks_examined=len(chunks),
        processing_time_sec=round(elapsed, 4),
    )

    return json.dumps(asdict(result), indent=2)


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------

def _handler(args: dict, task_id: str = None) -> str:
    return process_long_context(
        query=args.get("query", ""),
        directory=args.get("directory", "."),
        max_files=args.get("max_files", 50),
        top_k_chunks=args.get("top_k_chunks", 20),
        max_chunk_lines=args.get("max_chunk_lines", 200),
        manifest_path=args.get("manifest_path"),
        rebuild_manifest=args.get("rebuild_manifest", False),
    )


registry.register(
    name="long_context_processor",
    toolset="search",
    schema={
        "name": "long_context_processor",
        "description": (
            "Process a natural-language query over a directory of text/code files "
            "using filesystem-externalized context processing (arXiv 2603.20432). "
            "Uses a three-phase approach: (1) fast grep-based file filtering, "
            "(2) semantic chunk extraction with scoring, (3) sourced synthesis. "
            "Returns a structured JSON with the analysis, matched files, top chunks, "
            "and full provenance. Works natively with the filesystem as external "
            "memory instead of stuffing content into the context window."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The natural language query or search terms.",
                },
                "directory": {
                    "type": "string",
                    "description": "Root directory containing files to search.",
                },
                "max_files": {
                    "type": "integer",
                    "description": "Maximum files to process in Phase 2 (default 50).",
                },
                "top_k_chunks": {
                    "type": "integer",
                    "description": "Number of top-scoring chunks to retain (default 20).",
                },
                "max_chunk_lines": {
                    "type": "integer",
                    "description": "Max lines per chunk before subdividing (default 200).",
                },
                "manifest_path": {
                    "type": "string",
                    "description": "Optional path to persist/reuse the file manifest.",
                },
                "rebuild_manifest": {
                    "type": "boolean",
                    "description": "If true, rebuild manifest even if one exists.",
                },
            },
            "required": ["query", "directory"],
        },
    },
    handler=_handler,
)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Long Context Processor — filesystem-externalized search"
    )
    parser.add_argument("query", help="Search query")
    parser.add_argument("directory", help="Root directory to search")
    parser.add_argument("--max-files", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-chunk-lines", type=int, default=200)
    parser.add_argument("--manifest", type=str, default=None)
    parser.add_argument("--rebuild-manifest", action="store_true")
    args = parser.parse_args()

    result_json = process_long_context(
        query=args.query,
        directory=args.directory,
        max_files=args.max_files,
        top_k_chunks=args.top_k,
        max_chunk_lines=args.max_chunk_lines,
        manifest_path=args.manifest,
        rebuild_manifest=args.rebuild_manifest,
    )
    print(result_json)
