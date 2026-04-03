"""Core Hook Manager for Hermes Agent.

Manages hook registration, execution pipelines, and lifecycle for all
extension points. Inspired by Ruflo's AgenticHookManager and
PluginManager patterns, adapted for Python async/sync interoperability.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

from .hook_types import (
    HookContext, HookResult, HookRegistration, HookPoint, HookMetrics,
    HookHandler,
)

logger = logging.getLogger(__name__)
# Suppress noisy per-hook logs at INFO; most hooks log at DEBUG
HIGHEST_PRIORITY_THRESHOLD = -1  # run hooks with priority >= this


class HookManager:
    """Central registry and pipeline executor for lifecycle hooks.

    Usage:
        mgr = HookManager()
        mgr.register(HookPoint.TOOL_AFTER_EXECUTE, my_handler, "my-hook")
        await mgr.fire(HookPoint.TOOL_AFTER_EXECUTE, ctx)
    """

    def __init__(self, max_hooks_per_point: int = 20):
        self._hooks: Dict[str, List[HookRegistration]] = {}
        self._metrics = HookMetrics()
        self._max_hooks_per_point = max_hooks_per_point
        self._hook_timeout_seconds: float = 5.0  # per-hook timeout
        self._enabled: bool = True
        self._frozen_hook_points: Set[str] = set()

    # ──────────────────────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────────────────────

    def register(
        self,
        hook_point: HookPoint | str,
        handler: HookHandler | Callable,
        name: str,
        description: str = "",
        priority: int = 0,
        enabled: bool = True,
        metadata: Dict = None,
    ) -> None:
        """Register a hook function for a lifecycle extension point.

        Handlers can be sync or async -- the manager handles both.
        """
        point = hook_point if isinstance(hook_point, str) else hook_point.value

        if point in self._frozen_hook_points:
            raise RuntimeError(f"Hook point '{point}' is frozen (already fired)")

        if point not in self._hooks:
            self._hooks[point] = []

        hook_list = self._hooks[point]
        if len(hook_list) >= self._max_hooks_per_point:
            logger.warning(
                "Max hooks (%d) reached for point '%s'. Skipping '%s'.",
                self._max_hooks_per_point, point, name,
            )
            return

        if any(h.name == name for h in hook_list):
            logger.warning("Hook '%s' already registered at '%s'. Replacing.", name, point)
            hook_list = [h for h in hook_list if h.name != name]
            self._hooks[point] = hook_list

        reg = HookRegistration(
            hook_point=point,
            handler=handler,  # type: ignore[arg-type]
            name=name,
            description=description,
            priority=priority,
            enabled=enabled,
            metadata=metadata or {},
        )

        # Insert sorted by priority (highest first)
        hook_list.append(reg)
        # Sort descending by priority
        hook_list.sort(key=lambda h: h.priority, reverse=True)

        logger.debug("Registered hook '%s' at '%s' (priority=%d)", name, point, priority)

    def deregister(self, name: str) -> bool:
        """Remove a hook by name across all hook points."""
        for point, hook_list in self._hooks.items():
            before_len = len(hook_list)
            self._hooks[point] = [h for h in hook_list if h.name != name]
            if len(self._hooks[point]) < before_len:
                logger.debug("Deregistered hook '%s' from '%s'", name, point)
                return True
        return False

    def deregister_by_point(self, hook_point: HookPoint | str) -> int:
        """Remove all hooks for a specific hook point."""
        point = hook_point if isinstance(hook_point, str) else hook_point.value
        count = len(self._hooks.get(point, []))
        self._hooks.pop(point, None)
        logger.debug("Deregistered %d hooks from '%s'", count, point)
        return count

    # ──────────────────────────────────────────────────────────────
    # Discovery
    # ──────────────────────────────────────────────────────────────

    def list_hooks(self, hook_point: Optional[str] = None) -> List[HookRegistration]:
        """List registered hooks, optionally filtered by hook point."""
        if hook_point:
            return list(self._hooks.get(hook_point, []))
        result = []
        for hooks in self._hooks.values():
            result.extend(hooks)
        return result

    def list_hook_points(self) -> List[str]:
        """List hook points that have at least one hook registered."""
        return [k for k, v in self._hooks.items() if v]

    def get_metrics(self) -> HookMetrics:
        """Return aggregate hook metrics."""
        return self._metrics

    # ──────────────────────────────────────────────────────────────
    # Control
    # ──────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_hook_timeout(self, seconds: float) -> None:
        self._hook_timeout_seconds = seconds

    def freeze(self, hook_points: Optional[List[str]] = None) -> None:
        """Prevent further registration on given hook points.

        Call this after agent setup to prevent runtime hook injection.
        If hook_points is None, freeze ALL points.
        """
        if hook_points is None:
            for point in self._hooks:
                self._frozen_hook_points.add(point)
        else:
            self._frozen_hook_points.update(hook_points)

    # ──────────────────────────────────────────────────────────────
    # Pipeline Execution
    # ──────────────────────────────────────────────────────────────

    async def fire(
        self,
        hook_point: HookPoint | str,
        context: HookContext,
    ) -> List[HookResult]:
        """Fire all hooks registered for a given extension point.

        Returns a list of HookResults from hooks that returned one.
        If any hook returns abort=True, remaining hooks are skipped
        and the result list is returned immediately.

        Safe to call even if no hooks are registered (returns []).
        """
        if not self._enabled:
            return []

        point = hook_point if isinstance(hook_point, str) else hook_point.value
        hook_list = self._hooks.get(point, [])
        if not hook_list:
            return []

        results: List[HookResult] = []
        context.hook_point = point

        logger.debug("Firing %d hooks at '%s'", len(hook_list), point)

        for reg in hook_list:
            if not reg.enabled:
                continue

            start = time.monotonic()
            try:
                result = await self._run_handler_with_timeout(
                    reg.handler, context, self._hook_timeout_seconds,
                )

                duration_ms = (time.monotonic() - start) * 1000
                self._metrics.record(reg.name, duration_ms)

                if result is not None:
                    # Coerce non-HookResult values to a safe default, so
                    # custom handlers can return arbitrary values without
                    # breaking the pipeline.
                    if not isinstance(result, HookResult):
                        logger.warning(
                            "Hook '%s' at '%s' returned %s instead of HookResult -- wrapping",
                            reg.name, point, type(result).__name__,
                        )
                        result = HookResult(abort=False, severity="info")

                    if result.is_significant:
                        logger.info(
                            "Hook '%s' at '%s': %s",
                            reg.name, point, result.reason,
                        )
                    results.append(result)

                    if result.abort:
                        self._metrics.record(reg.name, duration_ms, aborted=True)
                        logger.warning(
                            "Hook '%s' aborted '%s': %s",
                            reg.name, point, result.reason,
                        )
                        return results  # short-circuit remaining hooks

            except asyncio.TimeoutError:
                self._metrics.record(reg.name, 0.0, error=True)
                logger.error(
                    "Hook '%s' at '%s' timed out after %.1fs",
                    reg.name, point, self._hook_timeout_seconds,
                )
            except Exception:
                elapsed = (time.monotonic() - start) * 1000
                self._metrics.record(reg.name, elapsed, error=True)
                logger.exception(
                    "Hook '%s' at '%s' raised exception", reg.name, point,
                )

        return results

    def fire_sync(
        self,
        hook_point: HookPoint | str,
        context: HookContext,
    ) -> List[HookResult]:
        """Synchronous variant of fire(). Wraps async hooks with run_until_complete."""
        if not self._enabled:
            return []

        point = hook_point if isinstance(hook_point, str) else hook_point.value
        hook_list = self._hooks.get(point, [])
        if not hook_list:
            return []

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context -- just create a task
                task = asyncio.ensure_future(self.fire(hook_point, context))
                try:
                    return loop.run_until_complete(
                        asyncio.wait_for(asyncio.shield(task), timeout=30)
                    )
                except Exception:
                    logger.error("Sync fire of '%s' failed", point, exc_info=True)
                    return []
            else:
                return loop.run_until_complete(self.fire(hook_point, context))
        except RuntimeError:
            # No event loop -- create one
            try:
                return asyncio.run(asyncio.wait_for(
                    self.fire(hook_point, context), timeout=30
                ))
            except Exception:
                logger.error("Sync fire of '%s' failed", point, exc_info=True)
                return []

    # ──────────────────────────────────────────────────────────────
    # Handler Execution
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _run_handler_with_timeout(
        handler: HookHandler | Callable,
        context: HookContext,
        timeout: float,
    ) -> Optional[HookResult]:
        """Run a single hook handler with timeout protection.

        Handles both async and sync handlers transparently.
        """
        if asyncio.iscoroutinefunction(handler) or (
            hasattr(handler, "__call__") and asyncio.iscoroutinefunction(handler.__call__)
        ):
            return await asyncio.wait_for(handler(context), timeout=timeout)
        else:
            # Sync handler -- run in executor to not block
            loop = asyncio.get_event_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, handler, context),
                timeout=timeout,
            )

    def status(self) -> Dict:
        """Return a status summary of all registered hooks."""
        status = {"enabled": self._enabled, "hook_points": {}}
        for point, hooks in self._hooks.items():
            status["hook_points"][point] = {
                "count": len(hooks),
                "hooks": [
                    {
                        "name": h.name,
                        "priority": h.priority,
                        "enabled": h.enabled,
                        "description": h.description,
                    }
                    for h in hooks
                ],
            }
        return status
