#!/usr/bin/env python3
"""
CAID (Centralized Asynchronous Isolated Delegation) Coordination Module.

Based on arXiv 2603.21489 "Effective Strategies for Asynchronous Software Engineering Agents".

Implements the three core SWE primitives from the paper:
1. Centralized task delegation -- manager creates dependency-aware task plans
2. Asynchronous execution -- subtasks run concurrently in isolated workspaces  
3. Isolated workspaces -- git worktree-based isolation prevents concurrent edit interference

The paper shows 26.7% accuracy improvement on PaperBench and 14.3% on Commit0
using this approach vs single-agent baselines.

Usage: Import and use CAIDCoordinator from this module.
       The delegate_task() function will automatically use CAID coordination
       when tasks involve file modifications.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
import time
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


@dataclass
class IsolatedWorkspace:
    """A git worktree-based isolated workspace for a subtask."""
    id: str
    branch_name: str
    worktree_path: str
    task_goal: str
    task_context: str = ""
    created_at: str = ""
    status: str = "pending"  # pending, running, merged, failed
    commit_hash: Optional[str] = None
    merge_conflicts: List[str] = field(default_factory=list)


@dataclass 
class TaskPlan:
    """Dependency-aware task plan from CAID centralized manager."""
    plan_id: str
    tasks: List[Dict[str, Any]]
    dependency_graph: Dict[str, List[str]]  # task_id -> depends_on
    execution_order: List[List[str]]  # stages of parallel execution
    created_at: str = ""
    status: str = "planned"


class CAIDCoordinator:
    """
    Centralized Asynchronous Isolated Delegation coordinator.
    
    Manages concurrent agent tasks with git-based isolation to prevent
    edit conflicts, following the CAID paradigm from arXiv 2603.21489.
    """
    
    def __init__(self, repo_path: str, worktrees_dir: str = None):
        self.repo_path = os.path.abspath(repo_path)
        self.worktrees_dir = worktrees_dir or os.path.join(
            tempfile.gettempdir(), f"hermes_caid_{int(time.time())}"
        )
        os.makedirs(self.worktrees_dir, exist_ok=True)
        self.workspaces: Dict[str, IsolatedWorkspace] = {}
        self.task_history: List[Dict] = []
    
    def create_task_plan(self, tasks: List[Dict[str, Any]]) -> TaskPlan:
        """
        Create a dependency-aware task plan.
        
        Analyzes task goals to detect dependencies and creates
        an execution order that maximizes parallelism while
        respecting dependencies.
        """
        plan_id = hashlib.md5(json.dumps(tasks, sort_keys=True).encode()).hexdigest()[:12]
        
        # Simple dependency detection: if task B mentions output of task A
        dep_graph: Dict[str, List[str]] = {str(i): [] for i in range(len(tasks))}
        
        for i, task_a in enumerate(tasks):
            goal_a = task_a.get("goal", "").lower()
            for j, task_b in enumerate(tasks):
                if i == j:
                    continue
                # Heuristic: if task_b mentions files/concepts that task_a creates
                goal_b = task_b.get("goal", "").lower()
                goal_b_context = task_b.get("context", "").lower()
                
                # Check for file dependencies
                import re
                files_in_a = set(re.findall(r'[\w-]+\.(?:py|js|ts|txt|md|json|yaml|yml)', goal_a))
                for f in files_in_a:
                    if f in goal_b or f in goal_b_context:
                        dep_graph[str(j)].append(str(i))
        
        # Compute execution stages (topological sort with parallelism)
        stages = self._compute_execution_stages(dep_graph)
        
        return TaskPlan(
            plan_id=plan_id,
            tasks=tasks,
            dependency_graph=dep_graph,
            execution_order=stages,
            created_at=datetime.utcnow().isoformat(),
        )
    
    def _compute_execution_stages(self, dep_graph: Dict[str, List[str]]) -> List[List[str]]:
        """Compute parallel execution stages respecting dependencies."""
        stages = []
        completed = set()
        remaining = set(dep_graph.keys())
        
        while remaining:
            # Find all tasks whose dependencies are satisfied
            ready = []
            for task_id in remaining:
                deps = dep_graph.get(task_id, [])
                if all(d in completed for d in deps):
                    ready.append(task_id)
            
            if not ready:
                # Cycle detection: just add remaining
                ready = list(remaining)
            
            stages.append(ready)
            completed.update(ready)
            remaining -= set(ready)
        
        return stages
    
    def create_isolated_workspace(self, task_id: str, goal: str, context: str = "") -> IsolatedWorkspace:
        """
        Create a git worktree-isolated workspace for a subtask.
        
        Per paper 2603.21489: "branch-and-merge is a central coordination
        mechanism for multi-agent collaboration, and SWE primitives such as
        git worktree, git commit, and git merge enable it reliably."
        """
        branch_name = f"caid-{task_id}-{hashlib.md5(goal.encode()).hexdigest()[:8]}"
        worktree_path = os.path.join(self.worktrees_dir, f"workspace-{task_id}")
        
        workspace = IsolatedWorkspace(
            id=task_id,
            branch_name=branch_name,
            worktree_path=worktree_path,
            task_goal=goal,
            task_context=context,
            created_at=datetime.utcnow().isoformat(),
        )
        
        # Create worktree
        try:
            # Ensure base branch exists
            result = subprocess.run(
                ["git", "-C", self.repo_path, "branch", "--show-current"],
                capture_output=True, text=True, timeout=10
            )
            base_branch = result.stdout.strip() or "main"
            
            # Create branch from main
            subprocess.run(
                ["git", "-C", self.repo_path, "branch", branch_name, base_branch],
                capture_output=True, text=True, timeout=10
            )
            
            # Create worktree
            os.makedirs(worktree_path, exist_ok=True)
            result = subprocess.run(
                ["git", "-C", self.repo_path, "worktree", "add", worktree_path, branch_name],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                workspace.status = "ready"
            else:
                # Fallback: just use the directory
                workspace.status = "fallback_directory"
                
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # Git not available, use directory isolation
            workspace.status = "fallback_directory"
        
        self.workspaces[task_id] = workspace
        return workspace
    
    def commit_workspace_changes(self, task_id: str, commit_message: str) -> Optional[str]:
        """Commit changes in an isolated workspace."""
        ws = self.workspaces.get(task_id)
        if not ws:
            return None
        
        work_path = ws.worktree_path if os.path.isdir(ws.worktree_path) else self.repo_path
        
        try:
            # Stage all changes
            subprocess.run(
                ["git", "-C", work_path, "add", "-A"],
                capture_output=True, text=True, timeout=30
            )
            
            # Commit
            result = subprocess.run(
                ["git", "-C", work_path, "commit", "-m", commit_message],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0:
                # Get commit hash
                hash_result = subprocess.run(
                    ["git", "-C", work_path, "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5
                )
                ws.commit_hash = hash_result.stdout.strip()
                ws.status = "committed"
                return ws.commit_hash
            else:
                ws.status = "nothing_to_commit"
                return None
                
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
    
    def merge_workspace(self, task_id: str) -> Dict[str, Any]:
        """
        Merge changes from an isolated workspace back to main branch.
        
        Per paper: structured integration with executable test-based verification.
        """
        ws = self.workspaces.get(task_id)
        if not ws:
            return {"error": "workspace not found", "task_id": task_id}
        
        result = {"task_id": task_id, "status": "unknown"}
        
        try:
            # Try to merge
            merge_result = subprocess.run(
                ["git", "-C", self.repo_path, "merge", "--no-ff", ws.branch_name, "-m", 
                 f"CAID merge: {ws.task_goal[:100]}"],
                capture_output=True, text=True, timeout=30
            )
            
            if merge_result.returncode == 0:
                result["status"] = "merged"
                ws.status = "merged"
            else:
                result["status"] = "merge_conflict"
                ws.status = "merge_conflict"
                # Parse conflict info
                conflicts = []
                for line in merge_result.stderr.split("\n"):
                    if "CONFLICT" in line:
                        conflicts.append(line.strip())
                ws.merge_conflicts = conflicts
                result["conflicts"] = conflicts
                result["stderr"] = merge_result.stderr[:500]
                
                # Auto-abort merge on conflict
                subprocess.run(
                    ["git", "-C", self.repo_path, "merge", "--abort"],
                    capture_output=True, text=True, timeout=10
                )
                
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            result["status"] = "error"
            result["error"] = str(e)
        
        return result
    
    def cleanup_workspace(self, task_id: str):
        """Remove an isolated workspace and its worktree."""
        ws = self.workspaces.get(task_id)
        if not ws:
            return
        
        # Remove worktree
        try:
            subprocess.run(
                ["git", "-C", self.repo_path, "worktree", "remove", "--force", ws.worktree_path],
                capture_output=True, text=True, timeout=10
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Delete branch
        try:
            subprocess.run(
                ["git", "-C", self.repo_path, "branch", "-D", ws.branch_name],
                capture_output=True, text=True, timeout=10
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Remove directory
        if os.path.exists(ws.worktree_path):
            shutil.rmtree(ws.worktree_path, ignore_errors=True)
        
        del self.workspaces[task_id]
    
    def cleanup_all(self):
        """Clean up all workspaces."""
        for task_id in list(self.workspaces.keys()):
            self.cleanup_workspace(task_id)


# ---------------------------------------------------------------------------
# Self-Organization Module (from paper 2603.28990)
# ---------------------------------------------------------------------------

class SelfOrganizingCoordinator:
    """
    Self-organizing multi-agent coordination.
    
    Per arXiv 2603.28990 "Drop the Hierarchy and Roles":
    - Give agents a mission, not pre-assigned roles
    - Agents spontaneously invent specialized roles
    - Agents voluntarily abstain from tasks outside their competence  
    - Sequential protocol outperforms centralized by 14%
    - 5,006 unique roles emerged from just 8 agents
    """
    
    def __init__(self):
        self.emergent_roles: Dict[str, Dict] = {}
        self.competence_records: Dict[str, List[float]] = {}
    
    def assign_tasks_emergent(self, tasks: List[Dict[str, Any]], 
                               num_agents: int = 4) -> Dict[str, Any]:
        """
        Assign tasks without pre-defined roles. 
        Agents self-organize based on task content.
        
        Per paper: "give agents a mission, a protocol, and a capable model -- not a pre-assigned role"
        """
        # Create mission statement from task collection
        domains = self._analyze_task_domains(tasks)
        mission = self._create_mission(domains, tasks)
        
        # Create agent assignments without roles
        # Each agent gets a subset of tasks, but ALL agents see the full mission
        assignments = []
        tasks_per_agent = max(1, len(tasks) // num_agents)
        
        for i in range(num_agents):
            start = i * tasks_per_agent
            end = start + tasks_per_agent if i < num_agents - 1 else len(tasks)
            agent_tasks = tasks[start:end]
            
            if agent_tasks:
                assignments.append({
                    "agent_id": i,
                    "mission": mission,
                    "tasks": agent_tasks,
                    # Per paper: agents should be able to abstain
                    "can_abstain": True,
                    # No pre-assigned role
                    "role": None,
                })
        
        return {
            "mode": "self_organizing",
            "mission": mission,
            "assignments": assignments,
            "num_agents": num_agents,
            "total_tasks": len(tasks),
        }
    
    def _analyze_task_domains(self, tasks: List[Dict]) -> Dict[str, int]:
        """Identify domains present in tasks for mission creation."""
        domains = {}
        domain_keywords = {
            "coding": ["code", "function", "class", "script", "debug", "test", "python", "api"],
            "research": ["research", "paper", "analysis", "study", "find", "search", "review"],
            "devops": ["deploy", "docker", "server", "config", "infrastructure", "database"],
            "data": ["data", "csv", "json", "parse", "extract", "process", "analyze"],
            "creative": ["write", "create", "design", "generate", "art", "image"],
        }
        
        for task in tasks:
            text = f"{task.get('goal', '')} {task.get('context', '')}".lower()
            for domain, keywords in domain_keywords.items():
                if any(kw in text for kw in keywords):
                    domains[domain] = domains.get(domain, 0) + 1
        
        return domains
    
    def _create_mission(self, domains: Dict[str, int], tasks: List[Dict]) -> str:
        """Create a mission statement that enables self-organization."""
        primary_domain = max(domains, key=domains.get) if domains else "general"
        
        mission = (
            f"You are part of a team working on a {primary_domain}-focused project. "
            f"Your team should self-organize: specialize in areas where you can contribute most, "
            f"and defer to teammates on tasks outside your expertise. "
            f"Do NOT claim expertise you don't have. "
            f"If a task is outside your competence, note this and let other team members handle it. "
            f"Avoid duplicating work already done by teammates. "
            f"Coordinate through structured communication."
        )
        
        return mission
    
    def record_competence(self, agent_id: str, task_score: float):
        """Record an agent's performance on a task. Used for emergent role formation."""
        if agent_id not in self.competence_records:
            self.competence_records[agent_id] = []
        self.competence_records[agent_id].append(task_score)
    
    def get_competence_profile(self, agent_id: str) -> Optional[float]:
        """Get average competence score for an agent."""
        scores = self.competence_records.get(agent_id, [])
        if scores:
            return sum(scores) / len(scores)
        return None


# ---------------------------------------------------------------------------
# Integration helper for delegate_tool 
# ---------------------------------------------------------------------------

def should_use_caid(goal: str, context: str = "", num_tasks: int = 1) -> bool:
    """
    Determine if CAID coordination should be used for a delegation.
    
    Per paper 2603.21489: CAID is most beneficial when:
    - Multiple subtasks involve file modifications
    - Tasks have dependencies between them
    - Concurrent writes could conflict
    """
    text = f"{goal} {context}".lower()
    
    # Strong signals for CAID
    file_ops = ["write", "create", "modify", "edit", "patch", "update", "implement"]
    has_file_ops = any(op in text for op in file_ops)
    
    # Multi-task with potential for conflicts
    multi_task = num_tasks > 1
    
    return has_file_ops and multi_task


def should_use_self_organization(goal: str, num_tasks: int = 1) -> bool:
    """
    Determine if self-organizing coordination should be used.
    
    Per paper 2603.28990: Self-organization works best with capable models
    and tasks that benefit from emergent specialization.
    """
    if num_tasks < 2:
        return False
    
    # Self-organization works best for creative/exploratory tasks
    text = f"{goal}".lower()
    exploratory = any(w in text for w in ["explore", "research", "analyze", "investigate", 
                                          "brainstorm", "design", "create"])
    return exploratory or num_tasks >= 4

