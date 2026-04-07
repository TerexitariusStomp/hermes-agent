"""Hermes tool wrappers for specialized ecosystem extensions."""

from __future__ import annotations

import json
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
from extensions.registry import update_extension
from tools.registry import registry


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _ok(data: dict[str, Any]) -> str:
    return _json({"success": True, **data})


def _err(error: str) -> str:
    return _json({"success": False, "error": error})


def aeon_manage(
    action: str,
    repo_path: str,
    skill_name: str | None = None,
    skill_payload: dict[str, Any] | None = None,
    workflow: str | None = None,
    run_id: str | None = None,
    ref: str = "main",
) -> str:
    try:
        aeon = AEONExtension(repo_path)
        if action == "create_or_update_skill":
            if not skill_name or skill_payload is None:
                return _err("skill_name and skill_payload are required")
            return _ok(aeon.create_or_update_skill_yaml(skill_name, skill_payload))
        if action == "trigger_run":
            if not workflow:
                return _err("workflow is required")
            return _ok(aeon.trigger_scheduled_run(workflow, ref=ref))
        if action == "fetch_logs":
            return _ok(aeon.fetch_logs(run_id=run_id, workflow=workflow))
        if action == "update":
            return _ok(aeon.update())
        return _err(f"Unknown action: {action}")
    except Exception as e:
        return _err(f"AEON operation failed: {type(e).__name__}: {e}")


def simulate_miroshark(uploaded_docs: list[str], days: int, repo_path: str) -> str:
    try:
        result = MiroSharkExtension(repo_path).simulate(uploaded_docs, days)
        return _ok({"report": result})
    except Exception as e:
        return _err(f"MiroShark simulation failed: {type(e).__name__}: {e}")


def simulate_mirofish(seed_info: dict[str, Any], horizon: int, repo_path: str) -> str:
    try:
        result = MiroFishExtension(repo_path).simulate(seed_info, horizon)
        return _ok({"prediction_report": result})
    except Exception as e:
        return _err(f"MiroFish simulation failed: {type(e).__name__}: {e}")


def write_memory(context: str, content: str, fabric_path: str = "~/fabric") -> str:
    try:
        result = IcarusDaedalusExtension(fabric_path).write_memory(context, content)
        return _ok(result)
    except Exception as e:
        return _err(f"write_memory failed: {type(e).__name__}: {e}")


def query_memory(query: str, fabric_path: str = "~/fabric") -> str:
    try:
        result = IcarusDaedalusExtension(fabric_path).query_memory(query)
        return _ok(result)
    except Exception as e:
        return _err(f"query_memory failed: {type(e).__name__}: {e}")


def omo_execute(task_description: str, models: list[str], repo_path: str) -> str:
    try:
        result = OhMyOpenAgentExtension(repo_path).execute(task_description, models=models)
        return _ok(result)
    except Exception as e:
        return _err(f"omo_execute failed: {type(e).__name__}: {e}")


def autores_run(repo_path: str, minutes: int = 5) -> str:
    try:
        result = AutoResearchExtension().autores_run(repo_path, minutes=minutes)
        return _ok({"experiment_result": result})
    except Exception as e:
        return _err(f"autores_run failed: {type(e).__name__}: {e}")


def alpha_mine(data_config: dict[str, Any], repo_path: str) -> str:
    try:
        result = AlphaAgentExtension(repo_path).alpha_mine(data_config)
        return _ok(result)
    except Exception as e:
        return _err(f"alpha_mine failed: {type(e).__name__}: {e}")


def alpha_backtest(factors: list[dict[str, Any]], portfolio: dict[str, Any], repo_path: str) -> str:
    try:
        result = AlphaAgentExtension(repo_path).alpha_backtest(factors, portfolio)
        return _ok(result)
    except Exception as e:
        return _err(f"alpha_backtest failed: {type(e).__name__}: {e}")


def openviking_index(context_files: list[str]) -> str:
    try:
        result = OpenVikingExtension().openviking_index(context_files)
        return _ok({"retrieval_result": result})
    except Exception as e:
        return _err(f"openviking_index failed: {type(e).__name__}: {e}")


def openviking_search(query: str) -> str:
    try:
        result = OpenVikingExtension().openviking_search(query)
        return _ok({"retrieval_result": result})
    except Exception as e:
        return _err(f"openviking_search failed: {type(e).__name__}: {e}")


def atlas_run_generation(num_days: int, initial_agents: list[dict[str, Any]], repo_path: str) -> str:
    try:
        result = AtlasGICExtension(repo_path).run_generation(num_days, initial_agents)
        return _ok({"agent_performance": result})
    except Exception as e:
        return _err(f"atlas_run_generation failed: {type(e).__name__}: {e}")


def ecosystem_module_update(module: str, repo_path: str | None = None) -> str:
    try:
        return _ok(update_extension(module, repo_path=repo_path))
    except Exception as e:
        return _err(f"ecosystem_module_update failed: {type(e).__name__}: {e}")


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


def _register() -> None:
    toolset = "ecosystem_extensions"
    registry.register(
        name="aeon_manage",
        toolset=toolset,
        schema=_schema(
            "aeon_manage",
            "Create/update AEON skill YAML, trigger workflow runs, fetch logs, and run AEON update merge flow.",
            {
                "action": {"type": "string"},
                "repo_path": {"type": "string"},
                "skill_name": {"type": "string"},
                "skill_payload": {"type": "object"},
                "workflow": {"type": "string"},
                "run_id": {"type": "string"},
                "ref": {"type": "string"},
            },
            ["action", "repo_path"],
        ),
        handler=lambda args, **kw: aeon_manage(
            action=args.get("action", ""),
            repo_path=args.get("repo_path", ""),
            skill_name=args.get("skill_name"),
            skill_payload=args.get("skill_payload"),
            workflow=args.get("workflow"),
            run_id=args.get("run_id"),
            ref=args.get("ref", "main"),
        ),
        emoji="🕒",
    )

    registry.register(
        name="simulate_miroshark",
        toolset=toolset,
        schema=_schema(
            "simulate_miroshark",
            "Build MiroShark graph, generate personas, run simulation, and return report metadata.",
            {"uploaded_docs": {"type": "array", "items": {"type": "string"}}, "days": {"type": "integer"}, "repo_path": {"type": "string"}},
            ["uploaded_docs", "days", "repo_path"],
        ),
        handler=lambda args, **kw: simulate_miroshark(args.get("uploaded_docs", []), int(args.get("days", 1)), args.get("repo_path", "")),
        emoji="🦈",
    )

    registry.register(
        name="simulate_mirofish",
        toolset=toolset,
        schema=_schema(
            "simulate_mirofish",
            "Run MiroFish swarm-intelligence simulation and prediction reporting.",
            {"seed_info": {"type": "object"}, "horizon": {"type": "integer"}, "repo_path": {"type": "string"}},
            ["seed_info", "horizon", "repo_path"],
        ),
        handler=lambda args, **kw: simulate_mirofish(args.get("seed_info", {}), int(args.get("horizon", 1)), args.get("repo_path", "")),
        emoji="🐟",
    )

    registry.register(
        name="write_memory",
        toolset=toolset,
        schema=_schema(
            "write_memory",
            "Write memory entries into Icarus-Daedalus markdown memory protocol store.",
            {"context": {"type": "string"}, "content": {"type": "string"}, "fabric_path": {"type": "string"}},
            ["context", "content"],
        ),
        handler=lambda args, **kw: write_memory(args.get("context", ""), args.get("content", ""), args.get("fabric_path", "~/fabric")),
        emoji="🧠",
    )

    registry.register(
        name="query_memory",
        toolset=toolset,
        schema=_schema(
            "query_memory",
            "Query Icarus-Daedalus markdown memory store.",
            {"query": {"type": "string"}, "fabric_path": {"type": "string"}},
            ["query"],
        ),
        handler=lambda args, **kw: query_memory(args.get("query", ""), args.get("fabric_path", "~/fabric")),
        emoji="🔎",
    )

    registry.register(
        name="omo_execute",
        toolset=toolset,
        schema=_schema(
            "omo_execute",
            "Delegate task execution to Oh-My-OpenAgent orchestrator with model selection.",
            {"task_description": {"type": "string"}, "models": {"type": "array", "items": {"type": "string"}}, "repo_path": {"type": "string"}},
            ["task_description", "models", "repo_path"],
        ),
        handler=lambda args, **kw: omo_execute(args.get("task_description", ""), args.get("models", []), args.get("repo_path", "")),
        emoji="🪝",
    )

    registry.register(
        name="autores_run",
        toolset=toolset,
        schema=_schema(
            "autores_run",
            "Run auto-research self-evolving experiment loop with accept/revert outcome.",
            {"repo_path": {"type": "string"}, "minutes": {"type": "integer"}},
            ["repo_path"],
        ),
        handler=lambda args, **kw: autores_run(args.get("repo_path", ""), int(args.get("minutes", 5))),
        emoji="🧪",
    )

    registry.register(
        name="alpha_mine",
        toolset=toolset,
        schema=_schema(
            "alpha_mine",
            "Mine interpretable alpha factors using AlphaAgent CLI.",
            {"data_config": {"type": "object"}, "repo_path": {"type": "string"}},
            ["data_config", "repo_path"],
        ),
        handler=lambda args, **kw: alpha_mine(args.get("data_config", {}), args.get("repo_path", "")),
        emoji="📈",
    )

    registry.register(
        name="alpha_backtest",
        toolset=toolset,
        schema=_schema(
            "alpha_backtest",
            "Backtest factors with AlphaAgent CLI.",
            {"factors": {"type": "array", "items": {"type": "object"}}, "portfolio": {"type": "object"}, "repo_path": {"type": "string"}},
            ["factors", "portfolio", "repo_path"],
        ),
        handler=lambda args, **kw: alpha_backtest(args.get("factors", []), args.get("portfolio", {}), args.get("repo_path", "")),
        emoji="📊",
    )

    registry.register(
        name="openviking_index",
        toolset=toolset,
        schema=_schema(
            "openviking_index",
            "Index context files in OpenViking.",
            {"context_files": {"type": "array", "items": {"type": "string"}}},
            ["context_files"],
        ),
        handler=lambda args, **kw: openviking_index(args.get("context_files", [])),
        emoji="🧭",
    )

    registry.register(
        name="openviking_search",
        toolset=toolset,
        schema=_schema(
            "openviking_search",
            "Search OpenViking tiered context index.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        handler=lambda args, **kw: openviking_search(args.get("query", "")),
        emoji="🗺️",
    )

    registry.register(
        name="atlas_run_generation",
        toolset=toolset,
        schema=_schema(
            "atlas_run_generation",
            "Run Atlas-GIC trading-agent prompt-evolution generation.",
            {"num_days": {"type": "integer"}, "initial_agents": {"type": "array", "items": {"type": "object"}}, "repo_path": {"type": "string"}},
            ["num_days", "initial_agents", "repo_path"],
        ),
        handler=lambda args, **kw: atlas_run_generation(int(args.get("num_days", 1)), args.get("initial_agents", []), args.get("repo_path", "")),
        emoji="🧬",
    )

    registry.register(
        name="ecosystem_module_update",
        toolset=toolset,
        schema=_schema(
            "ecosystem_module_update",
            "Run module-specific update hooks for ecosystem extensions.",
            {"module": {"type": "string"}, "repo_path": {"type": "string"}},
            ["module"],
        ),
        handler=lambda args, **kw: ecosystem_module_update(args.get("module", ""), args.get("repo_path")),
        emoji="♻️",
    )


_register()