"""Extension point lifecycle hooks for Hermes Agent.

A modular hook system that fires at key lifecycle stages across the agent:
  - tool.beforeExecute / tool.afterExecute
  - tool.onError
  - message.beforeSend / message.afterReceive
  - skill.beforeExecute / skill.afterExecute
  - memory.beforeStore / memory.afterStore
  - agent.beforeStart / agent.afterStop
  - cron.beforeRun / cron.afterRun

Design adapted from patterns in ruvnet/ruflo (ExtensionPoint system) and
integrated with Hermes' existing callback architecture.

Import chain (circular-import safe):
    tools/hooks/hook_manager.py  (no imports from run_agent or model_tools)
             ^
    tools/hooks/builtin_*.py     (import hook_manager)
             ^
    model_tools.py / run_agent.py (import hook_manager at runtime)
"""
