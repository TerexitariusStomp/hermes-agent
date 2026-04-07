"""Extension registry for Hermes ecosystem wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from extensions.aeon import AEONExtension
from extensions.alphaagent import AlphaAgentExtension
from extensions.atlas_gic import AtlasGICExtension
from extensions.autoresearch import AutoResearchExtension
from extensions.icarus_daedalus import IcarusDaedalusExtension
from extensions.mirofish import MiroFishExtension
from extensions.miroshark import MiroSharkExtension
from extensions.oh_my_openagent import OhMyOpenAgentExtension
from extensions.openviking import OpenVikingExtension


def update_extension(module: str, repo_path: str | None = None) -> dict[str, Any]:
    """Run update hooks for a supported extension module."""
    normalized = module.strip().lower()
    base = Path(repo_path).expanduser() if repo_path else None

    if normalized == "aeon":
        if not base:
            return {"success": False, "error": "repo_path is required for aeon updates"}
        return AEONExtension(base).update()
    if normalized == "miroshark":
        if not base:
            return {"success": False, "error": "repo_path is required for miroshark updates"}
        return MiroSharkExtension(base).update()
    if normalized == "mirofish":
        if not base:
            return {"success": False, "error": "repo_path is required for mirofish updates"}
        return MiroFishExtension(base).update()
    if normalized == "icarus-daedalus":
        return IcarusDaedalusExtension().update()
    if normalized == "omo":
        if not base:
            return {"success": False, "error": "repo_path is required for omo updates"}
        return OhMyOpenAgentExtension(base).update()
    if normalized == "autoresearch":
        if not base:
            return {"success": False, "error": "repo_path is required for autoresearch updates"}
        return AutoResearchExtension().update(base)
    if normalized == "alphaagent":
        if not base:
            return {"success": False, "error": "repo_path is required for alphaagent updates"}
        return AlphaAgentExtension(base).update()
    if normalized == "openviking":
        return OpenVikingExtension().update()
    if normalized == "atlas-gic":
        if not base:
            return {"success": False, "error": "repo_path is required for atlas-gic updates"}
        return AtlasGICExtension(base).update()

    return {"success": False, "error": f"Unknown extension module: {module}"}