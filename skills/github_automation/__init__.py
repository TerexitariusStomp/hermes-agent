"""
GitHub Automation Skill for Hermes Agent

Provides tools for autonomous repository management and self-improvement.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Import local implementation
try:
    from .github_automation import (
        get_github_automation,
        GitHubAutomation,
        GitHubAutomationConfig
    )
    GITHUB_AUTOMATION_AVAILABLE = True
except ImportError as e:
    logger.warning(f"GitHub automation dependencies not available: {e}")
    GITHUB_AUTOMATION_AVAILABLE = False


_gh_automation: Optional[GitHubAutomation] = None


def _get_automation() -> Optional[GitHubAutomation]:
    """Get or initialize GitHub automation instance."""
    global _gh_automation
    if not GITHUB_AUTOMATION_AVAILABLE:
        return None
    if _gh_automation is None:
        try:
            _gh_automation = get_github_automation()
        except Exception as e:
            logger.error(f"Failed to initialize GitHub automation: {e}")
            return None
    return _gh_automation


def github_info() -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available. Check GITHUB_TOKEN and dependencies (PyGithub, GitPython).")
    return gh.get_repo_info()


def github_list_issues(state: str = "open", labels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.list_issues(state=state, labels=labels)


def github_create_issue(title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.create_issue(title=title, body=body, labels=labels or [])


def github_create_branch(branch_name: str, source_branch: Optional[str] = None) -> bool:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.create_branch(branch_name, source_branch)


def github_commit_changes(message: str, files: Optional[List[str]] = None, branch: Optional[str] = None) -> str:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.commit_changes(message=message, files=files, branch=branch)


def github_push_branch(branch_name: Optional[str] = None) -> bool:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.push_branch(branch_name)


def github_create_pull(title: str, body: str, head: str, base: Optional[str] = None, draft: bool = True) -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.create_pull_request(title=title, body=body, head=head, base=base, draft=draft)


def github_ci_status(ref: Optional[str] = None) -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.get_ci_status(ref)


def github_add_comment(issue_number: int, body: str) -> bool:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.add_comment(issue_number, body)


def github_bump_version(
    part: str = "patch",
    commit: bool = True,
    push: bool = False,
    create_tag: bool = True,
    changelog: bool = True
) -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.bump_version(part=part, commit=commit, push=push, create_tag=create_tag, changelog=changelog)


def github_run_tests(test_path: Optional[str] = None) -> Dict[str, Any]:
    """Run pytest locally from the repository root."""
    try:
        # Find repo root by climbing from this file
        current = Path(__file__).resolve()
        repo_root = current.parents[2]  # skill/github-automation/__init__.py -> hermes-agent
        # Temporarily add to sys.path to ensure imports work
        import sys
        sys.path.insert(0, str(repo_root))
        try:
            from .github_automation import GitHubAutomation
            temp_gh = GitHubAutomation.__new__(GitHubAutomation)
            temp_gh.local_repo_path = repo_root
            # Use the method directly
            return temp_gh.run_tests(test_path)
        finally:
            sys.path.pop(0)
    except Exception as e:
        logger.error(f"Test execution failed: {e}")
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "passed": False
        }


def github_get_suggestions() -> List[Dict[str, Any]]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.get_self_improvement_suggestions()


def github_improvement_cycle() -> Dict[str, Any]:
    gh = _get_automation()
    if not gh:
        raise RuntimeError("GitHub automation not available")
    return gh.execute_improvement_cycle()


TOOLS = [
    github_info,
    github_list_issues,
    github_create_issue,
    github_create_branch,
    github_commit_changes,
    github_push_branch,
    github_create_pull,
    github_ci_status,
    github_add_comment,
    github_bump_version,
    github_run_tests,
    github_get_suggestions,
    github_improvement_cycle
]


def get_tools():
    """Return list of tools for Hermes skill loading."""
    return TOOLS


def on_load():
    """Called when the skill is loaded."""
    logger.info("GitHub Automation skill loaded")
    gh = _get_automation()
    if gh:
        logger.info(f"GitHub automation initialized for {gh.config.repo_name}")
    else:
        logger.warning("GitHub automation not available (GITHUB_TOKEN missing or invalid)")

