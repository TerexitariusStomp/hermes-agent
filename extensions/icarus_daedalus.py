"""Icarus-Daedalus memory protocol adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cron.jobs import create_job, list_jobs
from extensions.base import ensure_dir, run_command


class IcarusDaedalusExtension:
    """Read/write/query markdown-based memory in ~/fabric."""

    def __init__(self, fabric_path: str | Path = "~/fabric"):
        self.fabric_path = Path(fabric_path).expanduser()
        ensure_dir(self.fabric_path)

    def write_memory(self, context: str, content: str) -> dict[str, Any]:
        target_dir = ensure_dir(self.fabric_path / context)
        target_file = target_dir / "memory.md"
        frontmatter = {"context": context}
        body = f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n\n{content.strip()}\n"
        target_file.write_text(body, encoding="utf-8")
        return {"success": True, "path": str(target_file)}

    def query_memory(self, query: str) -> dict[str, Any]:
        hits: list[dict[str, str]] = []
        for md in self.fabric_path.rglob("*.md"):
            text = md.read_text(encoding="utf-8", errors="ignore")
            if query.lower() in text.lower():
                hits.append({"path": str(md), "preview": text[:280]})
        return {"success": True, "query": query, "results": hits}

    def compact(self) -> dict[str, Any]:
        return run_command("python curator.py", cwd=self.fabric_path)

    def ensure_compaction_schedule(self) -> dict[str, Any]:
        existing = list_jobs(include_disabled=True)
        for job in existing:
            if job.get("name") == "icarus-daedalus-compaction":
                return {"success": True, "scheduled": True, "job_id": job["id"]}
        job = create_job(
            prompt="Run Icarus-Daedalus curator compaction in ~/fabric and report summary.",
            schedule="every 12h",
            name="icarus-daedalus-compaction",
            deliver="local",
            skills=[],
        )
        return {"success": True, "scheduled": True, "job_id": job["id"]}

    def update(self) -> dict[str, Any]:
        # Protocol is file-based; update means ensuring compaction automation exists.
        return self.ensure_compaction_schedule()