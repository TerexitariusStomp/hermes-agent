#!/usr/bin/env python3
"""
Hermes Observability Gateway
=============================
Unified tracing for ALL LLM calls through Portkey → 5 cloud observability APIs.

Architecture:
  Agent code
    └→ LLM call (via Portkey local proxy)
         ├→ Portkey headers (x-portkey-trace-id, x-portkey-provider)
         ├→ Lunary          (HTTP POST to https://app.lunary.ai/api/v1/trace)
         ├→ Langfuse        (HTTP POST to https://us.cloud.langfuse.com)
         ├→ LangSmith       (HTTP POST to https://api.smith.langchain.com)
         ├→ Opik            (HTTP POST to https://www.comet.com/api)
         └→ W&B             (HTTP POST to https://api.wandb.ai)

NO SDK installations needed — all platforms use HTTP APIs directly.
Only Portkey Gateway runs locally as the LLM routing proxy.

Usage:
  from tools.observability import trace_llm_call
  
  @trace_llm_call
  def my_llm_function(messages, model):
      # makes LLM call
      return response
"""

import os
import json
import time
import uuid
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from functools import wraps

logger = logging.getLogger("hermes-observability")

# ===========================================================================
# Configuration — loaded from ~/.hermes/.env
# ===========================================================================

class ObservatoryConfig:
    def __init__(self, env_path: str = None):
        self.env_path = env_path or os.path.expanduser("~/.hermes/.env")
        self.env = self._load()

    def _load(self) -> Dict:
        env = {}
        if os.path.exists(self.env_path):
            with open(self.env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip()
        return env

    @property
    def lunary_public_key(self): return self.env.get("LUNARY_PUBLIC_KEY", "")
    @property
    def lunary_private_key(self): return self.env.get("LUNARY_PRIVATE_KEY", "")
    @property
    def lunary_enabled(self): return bool(self.lunary_public_key and self.lunary_private_key)

    @property
    def langfuse_public_key(self): return self.env.get("LANGFUSE_PUBLIC_KEY", "")
    @property
    def langfuse_secret_key(self): return self.env.get("LANGFUSE_SECRET_KEY", "")
    @property
    def langfuse_host(self): return self.env.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    @property
    def langfuse_enabled(self): return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def langsmith_api_key(self): return self.env.get("LANGSMITH_API_KEY", "")
    @property
    def langsmith_project(self): return self.env.get("LANGSMITH_PROJECT", "hermes-agent")
    @property
    def langsmith_enabled(self): return bool(self.langsmith_api_key)

    @property
    def opik_api_key(self): return self.env.get("OPIK_API_KEY", "")
    @property
    def opik_project(self): return self.env.get("OPIK_PROJECT", "hermes-agent")
    @property
    def opik_enabled(self): return bool(self.opik_api_key)

    @property
    def wandb_api_key(self): return self.env.get("WANDB_API_KEY", "")
    @property
    def wandb_project(self): return self.env.get("WANDB_PROJECT", "hermes-agent")
    @property
    def wandb_enabled(self): return bool(self.wandb_api_key)

    @property
    def portkey_api_key(self): return self.env.get("PORTKEY_API_KEY", "")
    @property
    def portkey_enabled(self): return bool(self.portkey_api_key)

    @property
    def session_id(self): return self.env.get("SESSION_ID", "")

    def status(self) -> Dict[str, Any]:
        return {
            "lunary":     {"enabled": self.lunary_enabled},
            "langfuse":   {"enabled": self.langfuse_enabled, "host": self.langfuse_host},
            "langsmith":  {"enabled": self.langsmith_enabled, "project": self.langsmith_project},
            "opik":       {"enabled": self.opik_enabled, "project": self.opik_project},
            "wandb":      {"enabled": self.wandb_enabled, "project": self.wandb_project},
            "portkey":    {"enabled": self.portkey_enabled},
        }


_config = ObservatoryConfig()

# ===========================================================================
# HTTP API clients (NO SDKs — all direct HTTP)
# ===========================================================================

def _http_post(url: str, headers: Dict, json_body: Dict, timeout: int = 10):
    """Safe HTTP POST that never crashes the agent."""
    try:
        import httpx
        r = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
        return r
    except Exception as e:
        logger.debug("HTTP POST %s failed: %s", url.split("//")[1].split("/")[0], e)
        return None


# ---------------------------------------------------------------------------
# Lunary — HTTP POST to /api/v1/trace
# ---------------------------------------------------------------------------

def trace_lunary(call_id: str, messages: List[Dict], response: str,
                  model: str, provider: str, usage: Dict, latency_ms: float,
                  error: str = None):
    if not _config.lunary_enabled:
        return None
    try:
        payload = {
            "type": "llm_call",
            "callId": call_id,
            "sessionId": _config.session_id or "hermes",
            "input": messages,
            "output": response,
            "error": error,
            "model": model,
            "provider": provider,
            "latency": latency_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "usage": usage,
        }
        r = _http_post(
            "https://app.lunary.ai/api/v1/trace",
            headers={
                "x-lunary-public-key": _config.lunary_public_key,
                "x-lunary-private-key": _config.lunary_private_key,
                "Content-Type": "application/json",
            },
            json_body=payload,
        )
        if r and r.status_code == 200:
            logger.info("Lunary trace: OK (call_id=%s)", call_id[:12])
            return r.json()
    except Exception as e:
        logger.debug("Lunary trace failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Langfuse — REST API (HTTP POST to /api/public/traces + /api/public/scores)
# ---------------------------------------------------------------------------

def trace_langfuse(call_id: str, messages: List[Dict], response: str,
                    model: str, provider: str, usage: Dict, latency_ms: float,
                    error: str = None):
    if not _config.langfuse_enabled:
        return None
    try:
        import base64
        auth = base64.b64encode(
            f"{_config.langfuse_public_key}:{_config.langfuse_secret_key}".encode()
        ).decode()
        host = _config.langfuse_host.rstrip("/")
        trace_id = str(uuid.uuid4())

        payload = {
            "id": trace_id,
            "name": "llm_call",
            "input": messages,
            "output": response if not error else None,
            "metadata": {
                "model": model,
                "provider": provider,
                "latency_ms": round(latency_ms, 1),
                "usage": usage,
                "error": error,
            },
        }
        r = _http_post(
            f"{host}/api/public/traces",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            json_body=payload,
        )
        if r and r.status_code in (200, 201):
            logger.info("Langfuse trace: OK (trace_id=%s)", trace_id[:12])
            return trace_id
    except Exception as e:
        logger.debug("Langfuse trace failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# LangSmith — Run API (HTTP POST to /runs)
# ---------------------------------------------------------------------------

def trace_langsmith(messages: List[Dict], response: str,
                     model: str, provider: str, usage: Dict, latency_ms: float,
                     error: str = None):
    if not _config.langsmith_enabled:
        return None
    try:
        run_id = str(uuid.uuid4())
        project = _config.langsmith_project
        payload = {
            "id": run_id,
            "name": "llm_call",
            "run_type": "llm",
            "inputs": {"messages": messages},
            "outputs": {"response": response} if not error else None,
            "extra": {
                "metadata": {
                    "model": model,
                    "provider": provider,
                    "usage": usage,
                    "latency_ms": round(latency_ms, 1),
                }
            },
            "error": error,
        }
        r = _http_post(
            f"https://api.smith.langchain.com/runs",
            headers={
                "x-api-key": _config.langsmith_api_key,
                "Content-Type": "application/json",
            },
            json_body=payload,
        )
        if r and r.status_code in (200, 201):
            logger.info("LangSmith run: OK (run_id=%s)", run_id[:12])
            return run_id
    except Exception as e:
        logger.debug("LangSmith trace failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Opik — REST API (HTTP POST to /api/rest/v1/experiments)
# ---------------------------------------------------------------------------

def trace_opik(messages: List[Dict], response: str,
                model: str, provider: str, usage: Dict, latency_ms: float,
                error: str = None):
    if not _config.opik_enabled:
        return None
    try:
        project = _config.opik_project
        trace_id = str(uuid.uuid4())
        payload = {
            "project_name": project,
            "trace_id": trace_id,
            "input": {"messages": messages},
            "output": {"response": response} if not error else None,
            "metadata": {
                "model": model,
                "provider": provider,
                "usage": usage,
                "latency_ms": round(latency_ms, 1),
                "error": error,
            },
        }
        r = _http_post(
            "https://www.comet.com/api/rest/v1/experiments",
            headers={
                "Authorization": _config.opik_api_key,
                "Content-Type": "application/json",
            },
            json_body=payload,
        )
        if r and r.status_code in (200, 201):
            logger.info("Opik trace: OK (trace_id=%s)", trace_id[:12])
            return trace_id
    except Exception as e:
        logger.debug("Opik trace failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# W&B — GraphQL API (HTTP POST to /graphql)
# ---------------------------------------------------------------------------

def trace_wandb(messages: List[Dict], response: str,
                 model: str, provider: str, usage: Dict, latency_ms: float,
                 error: str = None):
    if not _config.wandb_enabled:
        return None
    try:
        # First get entity
        entity_query = {"query": "{ viewer { entity } }"}
        r = _http_post(
            "https://api.wandb.ai/graphql",
            headers={"Authorization": _config.wandb_api_key},
            json_body=entity_query,
            timeout=15,
        )
        if not r or r.status_code != 200:
            return None
        try:
            entity = r.json().get("data", {}).get("viewer", {}).get("entity", "")
        except Exception:
            entity = ""

        # Now create a run via GraphQL mutation
        project = _config.wandb_project
        run_name = f"hermes-llm-call-{int(time.time())}"
        mutation = {
            "query": """
            mutation CreateRun($entity: String!, $project: String!, $name: String, $config: JSONString) {
              upsertBucket(input: {entityName: $entity, projectName: $project, name: $name, config: $config}) {
                bucket { id name displayName project { name } }
              }
            }
            """,
            "variables": {
                "entity": entity,
                "project": project,
                "name": run_name,
                "config": json.dumps({
                    "model": model,
                    "provider": provider,
                    "latency_ms": round(latency_ms, 1),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "error": error,
                }),
            },
        }
        r2 = _http_post(
            "https://api.wandb.ai/graphql",
            headers={"Authorization": _config.wandb_api_key, "Content-Type": "application/json"},
            json_body=mutation,
            timeout=15,
        )
        if r2 and r2.status_code in (200, 201):
            logger.info("W&B run: OK (%s/%s)", entity or "?", run_name)
            return f"{entity}/{project}/{run_name}"
    except Exception as e:
        logger.debug("W&B trace failed: %s", e)
    return None


# ===========================================================================
# Unified tracer decorator / wrapper
# ===========================================================================

def trace_llm_call(name: str = None, tags: List[str] = None):
    """
    Decorator to trace any function that makes LLM calls.
    Sends traces to ALL enabled observability platforms simultaneously.
    
    Usage:
      @trace_llm_call(name="hermes.main_turn")
      def call_llm(messages, model):
          return client.chat.completions.create(...)
    
      Or inline:
      from tools.observability import trace_llm_call
      trace_llm_call(name="test")(lambda: ...)()
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            call_id = str(uuid.uuid4())
            start = time.monotonic()
            func_name = name or func.__name__
            
            # Extract messages/model if passed as kwargs
            messages = kwargs.get("messages", [])
            model = kwargs.get("model", "")
            if not messages and args:
                messages = args[0] if isinstance(args[0], list) else []
            if not model and "model" in kwargs:
                model = kwargs["model"]
            
            try:
                result = func(*args, **kwargs)
                
                # Extract response data
                response_text = ""
                usage = {}
                error = None
                
                if hasattr(result, "choices"):
                    response_text = result.choices[0].message.content
                    if result.usage:
                        usage = {
                            "prompt_tokens": getattr(result.usage, "prompt_tokens", 0),
                            "completion_tokens": getattr(result.usage, "completion_tokens", 0),
                            "total_tokens": getattr(result.usage, "total_tokens", 0),
                        }
                elif isinstance(result, dict):
                    response_text = result.get("content", result.get("response", ""))
                    usage = result.get("usage", {})
                else:
                    response_text = str(result)
                    
            except Exception as e:
                error = str(e)
                response_text = ""
                usage = {}
                raise
            finally:
                latency_ms = (time.monotonic() - start) * 1000
                provider = "openrouter"  # default
                
                # Send to all platforms in parallel
                trace_lunary(call_id, messages, response_text, model, provider, usage, latency_ms, error)
                trace_langfuse(call_id, messages, response_text, model, provider, usage, latency_ms, error)
                trace_langsmith(messages, response_text, model, provider, usage, latency_ms, error)
                trace_opik(messages, response_text, model, provider, usage, latency_ms, error)
                trace_wandb(messages, response_text, model, provider, usage, latency_ms, error)

            return result
        return wrapper
    return decorator


def trace_llm_call_sync(messages, response_text, model, provider, usage, latency_ms, error=None):
    """Trace a completed LLM call to all platforms (non-decorator usage)."""
    call_id = str(uuid.uuid4())
    trace_lunary(call_id, messages, response_text, model, provider, usage, latency_ms, error)
    trace_langfuse(call_id, messages, response_text, model, provider, usage, latency_ms, error)
    trace_langsmith(messages, response_text, model, provider, usage, latency_ms, error)
    trace_opik(messages, response_text, model, provider, usage, latency_ms, error)
    trace_wandb(messages, response_text, model, provider, usage, latency_ms, error)
    return call_id


# ===========================================================================
# Portkey proxy headers builder
# ===========================================================================

def portkey_headers(provider: str = "openrouter", model: str = "") -> Dict[str, str]:
    """Get Portkey Gateway proxy headers for LLM routing."""
    if not _config.portkey_enabled:
        return {}
    return {
        "x-portkey-api-key": _config.portkey_api_key,
        "x-portkey-provider": provider,
        "x-portkey-trace-id": str(uuid.uuid4()),
        "x-portkey-mode": "fallback",
        "x-portkey-retries": "2",  # retry on failure
    }


# ===========================================================================
# Init / status
# ===========================================================================

def init_all() -> Dict[str, Any]:
    """Initialize (validate config) and return status."""
    return _config.status()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermes Observability CLI")
    parser.add_argument("--status", action="store_true", help="Show platform status")
    parser.add_argument("--test", action="store_true", help="Test all APIs with a dummy trace")
    args = parser.parse_args()

    if args.status:
        status = _config
        print("=== Observability Status ===")
        for name, info in init_all().items():
            icon = "✓" if info.get("enabled") else "✗"
            print(f"  [{icon}] {name}: {json.dumps(info)}")
    elif args.test:
        print("=== Testing all observability APIs ===")
        call_id = trace_llm_call_sync(
            messages=[{"role": "user", "content": "Hello from Hermes test"}],
            response_text="Test response — observability tracing active",
            model="qwen/qwen3.6-plus:free",
            provider="openrouter",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            latency_ms=234.5,
        )
        print(f"Trace ID: {call_id[:16]}...")
    else:
        parser.print_help()
