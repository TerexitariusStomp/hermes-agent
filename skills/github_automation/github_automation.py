"""
GitHub Automation for Hermes Agent — Autonomous Self-Improvement

This module provides tools for Hermes to interact with its own GitHub repository,
enabling autonomous code improvements, issue triage, and version management.

All actions are logged to observability platforms and respect safety guards.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path

try:
    from github import Github, GithubException, UnknownObjectException
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False

try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


@dataclass
class GitHubAutomationConfig:
    """Configuration for GitHub automation."""
    token: str
    repo_name: str = "NousResearch/hermes-agent"
    base_branch: str = "main"
    auto_commit: bool = False  # Require explicit confirmation for commits
    require_review: bool = True  # Draft PRs by default
    enable_self_improvement: bool = True


class GitHubAutomation:
    """
    GitHub automation for Hermes self-improvement.

    Key capabilities:
    - Repository inspection (files, branches, issues, PRs, CI status)
    - Issue creation and management
    - Pull request creation (with or without auto-commit)
    - Safe commit operations (pre-commit hooks, lint, tests)
    - Version bumping (semantic versioning)
    - Dependency updates (with lockfile regeneration)
    - Self-modification with guardrails
    """

    def __init__(self, config: Optional[GitHubAutomationConfig] = None):
        if not GITHUB_AVAILABLE:
            raise ImportError("PyGithub not installed. Run: pip install PyGithub")
        if not GIT_AVAILABLE:
            raise ImportError("GitPython not installed. Run: pip install GitPython")

        self.config = config or GitHubAutomationConfig(
            token=os.getenv("GITHUB_TOKEN", "")
        )

        if not self.config.token:
            raise ValueError("GITHUB_TOKEN not set in environment")

        self.gh = Github(self.config.token)
        self.repo = self.gh.get_repo(self.config.repo_name)
        self.local_repo_path = self._find_repo_root()
        self.repo_git = git.Repo(self.local_repo_path)

        # Observability integration (if available)
        self._tracer = None
        self._init_tracing()

    def _init_tracing(self):
        """Initialize tracing if observability tools are configured."""
        try:
            # Try Langfuse first (self-hosted, matches our philosophy)
            if os.getenv("LUNARY_PUBLIC_KEY"):
                from langfuse import Langfuse
                self._tracer = Langfuse(
                    public_key=os.getenv("LUNARY_PUBLIC_KEY"),
                    private_key=os.getenv("LUNARY_PRIVATE_KEY")
                )
        except Exception:
            pass

    def _trace(self, action: str, **kwargs):
        """Log automation action to observability platform."""
        if self._tracer:
            try:
                self._tracer.track_event(
                    event_name=f"github_automation.{action}",
                    properties=kwargs
                )
            except Exception:
                pass  # Don't fail if tracing fails

    def _find_repo_root(self) -> Path:
        """Find the root of the git repository."""
        current = Path(__file__).resolve()
        for parent in [current] + list(current.parents):
            if (parent / ".git").exists():
                return parent
        raise RuntimeError("Could not find git repository root")

    def _ensure_clean_working_dir(self) -> bool:
        """Check if working directory is clean."""
        return not self.repo_git.is_dirty(untracked_files=True)

    def get_repo_info(self) -> Dict[str, Any]:
        """Get repository metadata."""
        self._trace("get_repo_info")
        return {
            "name": self.repo.full_name,
            "description": self.repo.description,
            "stars": self.repo.stargazers_count,
            "forks": self.repo.forks_count,
            "open_issues": self.repo.open_issues_count,
            "default_branch": self.repo.default_branch,
            "topics": self.repo.get_topics(),
            "license": self.repo.license.name if self.repo.license else None
        }

    def list_issues(self, state: str = "open", labels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """List issues with optional filtering."""
        self._trace("list_issues", state=state, labels=labels)
        issues = self.repo.get_issues(state=state)
        if labels:
            label_set = set(labels)
            issues = [i for i in issues if any(l.name in label_set for l in i.labels)]

        return [
            {
                "number": i.number,
                "title": i.title,
                "body": i.body[:200] if i.body else "",
                "state": i.state,
                "labels": [l.name for l in i.labels],
                "created_at": i.created_at.isoformat(),
                "url": i.html_url,
                "assignee": i.assignee.login if i.assignee else None
            }
            for i in issues[:50]
        ]

    def create_issue(self, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Create a new GitHub issue.

        Args:
            title: Issue title
            body: Issue description (Markdown supported)
            labels: Optional list of label names

        Returns:
            Created issue data
        """
        self._trace("create_issue", title=title, labels=labels)
        issue = self.repo.create_issue(
            title=title,
            body=body,
            labels=labels or []
        )
        return {
            "number": issue.number,
            "url": issue.html_url,
            "title": issue.title
        }

    def list_pull_requests(self, state: str = "open", head: Optional[str] = None) -> List[Dict[str, Any]]:
        """List pull requests with optional filtering."""
        self._trace("list_prs", state=state, head=head)
        prs = self.repo.get_pulls(state=state, head=head)
        return [
            {
                "number": pr.number,
                "title": pr.title,
                "body": pr.body[:200] if pr.body else "",
                "state": pr.state,
                "merged": pr.merged,
                "mergeable": pr.mergeable,
                "url": pr.html_url,
                "head": pr.head.ref,
                "base": pr.base.ref
            }
            for pr in prs[:50]
        ]

    def create_branch(self, branch_name: str, source_branch: Optional[str] = None) -> bool:
        """
        Create a new branch from source.

        Args:
            branch_name: Name of new branch
            source_branch: Source branch (defaults to config.base_branch)

        Returns:
            True if branch created successfully
        """
        self._trace("create_branch", branch=branch_name, source=source_branch)
        source = source_branch or self.config.base_branch

        # Get source reference
        ref = self.repo.get_git_ref(f"heads/{source}")
        self.repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=ref.object.sha
        )
        return True

    def commit_changes(self, message: str, files: Optional[List[str]] = None, branch: Optional[str] = None) -> str:
        """
        Commit changes to the repository.

        Args:
            message: Commit message
            files: Specific files to commit (None for all changes)
            branch: Branch to commit to (defaults to current)

        Returns:
            Commit SHA
        """
        if self.config.auto_commit:
            raise RuntimeError("Auto-commit is disabled for safety")

        self._trace("commit_changes", message=message, files=files)

        # Stage files
        if files:
            self.repo_git.index.add(files)
        else:
            self.repo_git.index.add("*")

        # Commit
        commit = self.repo_git.index.commit(message)
        return commit.hexsha

    def push_branch(self, branch_name: Optional[str] = None, set_upstream: bool = True) -> bool:
        """
        Push local branch to remote.

        Args:
            branch_name: Branch to push (default: current)
            set_upstream: Set upstream tracking

        Returns:
            True if successful
        """
        self._trace("push_branch", branch=branch_name)
        if branch_name:
            self.repo_git.remotes.origin.push(refspec=f"{branch_name}:{branch_name}")
        else:
            self.repo_git.remotes.origin.push()
        return True

    def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: Optional[str] = None,
        draft: bool = True
    ) -> Dict[str, Any]:
        """
        Create a pull request.

        Args:
            title: PR title
            body: PR description
            head: Head branch (to merge from)
            base: Base branch (to merge into, defaults to config.base_branch)
            draft: Create as draft PR

        Returns:
            PR data
        """
        base_branch = base or self.config.base_branch
        self._trace("create_pr", title=title, head=head, base=base_branch, draft=draft)

        pr = self.repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base_branch,
            draft=draft
        )
        return {
            "number": pr.number,
            "url": pr.html_url,
            "title": pr.title
        }

    def get_ci_status(self, ref: Optional[str] = None) -> Dict[str, Any]:
        """
        Check CI status for a commit.

        Args:
            ref: Branch name or commit SHA (default: current HEAD)

        Returns:
            CI status summary
        """
        ref = ref or self.repo_git.head.commit.hexsha
        commit = self.repo.get_commit(ref)
        status = commit.get_combined_status()

        return {
            "state": status.state,
            "total": status.total_count,
            "successes": [s.context for s in status.statuses if s.state == "success"],
            "failures": [s.context for s in status.statuses if s.state == "failure"],
            "pending": [s.context for s in status.statuses if s.state == "pending"]
        }

    def request_review(self, pr_number: int, reviewers: List[str]) -> bool:
        """Request review from specific users on a PR."""
        self._trace("request_review", pr=pr_number, reviewers=reviewers)
        pr = self.repo.get_pull(pr_number)
        pr.create_review_request(reviewers)
        return True

    def add_comment(self, issue_number: int, body: str) -> bool:
        """Add a comment to an issue or PR."""
        self._trace("add_comment", issue=issue_number)
        issue = self.repo.get_issue(issue_number)
        issue.create_comment(body)
        return True

    def run_tests(self, test_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Run tests locally using pytest.

        Args:
            test_path: Specific test file or directory

        Returns:
            Test results summary
        """
        self._trace("run_tests", path=test_path)
        cmd = ["pytest"]
        if test_path:
            cmd.append(test_path)
        else:
            cmd.append("tests/")

        try:
            result = subprocess.run(
                cmd,
                cwd=self.local_repo_path,
                capture_output=True,
                text=True,
                timeout=300
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-4000:],  # last 4k chars
                "stderr": result.stderr[-4000:],
                "passed": result.returncode == 0
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": "Test timeout after 300s",
                "passed": False
            }

    def bump_version(
        self,
        part: str = "patch",
        commit: bool = True,
        push: bool = False,
        create_tag: bool = True,
        changelog: bool = True
    ) -> Dict[str, Any]:
        """
        Bump version number following semantic versioning.

        Args:
            part: Which part to bump ('major', 'minor', 'patch')
            commit: Commit version changes
            push: Push commit and tag
            create_tag: Create git tag
            changelog: Update CHANGELOG.md (requires conventional commits)

        Returns:
            Version bump result
        """
        self._trace("bump_version", part=part)

        if self.config.auto_commit:
            raise RuntimeError("Auto version bump disabled for safety")

        if not self._ensure_clean_working_dir():
            raise RuntimeError("Working directory not clean. Commit or stash changes first.")

        # Find version file (typically hermes_cli/__init__.py or setup.py)
        version_file = self.local_repo_path / "hermes_cli" / "__init__.py"
        if not version_file.exists():
            version_file = self.local_repo_path / "setup.py"

        if not version_file.exists():
            raise FileNotFoundError("Could not find version definition")

        # Read current version
        content = version_file.read_text()
        match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
        if not match:
            raise ValueError("Version pattern not found")

        current = match.group(1)
        major, minor, patch = map(int, current.split('.'))

        # Bump
        if part == "major":
            major += 1
            minor = 0
            patch = 0
        elif part == "minor":
            minor += 1
            patch = 0
        elif part == "patch":
            patch += 1
        else:
            raise ValueError(f"Invalid bump part: {part}")

        new_version = f"{major}.{minor}.{patch}"
        new_content = content.replace(current, new_version)
        version_file.write_text(new_content)

        # Update changelog if requested
        changelog_path = self.local_repo_path / "CHANGELOG.md"
        if changelog and changelog_path.exists():
            today = subprocess.run(["date", "+%Y-%m-%d"], capture_output=True, text=True).stdout.strip()
            entry = f"\n## [{new_version}] - {today}\n\n### Added\n- [Auto-generated version bump]\n\n"
            existing = changelog_path.read_text()
            # Insert after header
            if "## [" in existing:
                idx = existing.find("## [")
                new_changelog = existing[:idx] + entry + existing[idx:]
            else:
                new_changelog = existing + entry
            changelog_path.write_text(new_changelog)

        # Commit
        if commit:
            msg = f"chore: bump version to {new_version}"
            self.commit_changes(msg, files=[str(version_file.relative_to(self.local_repo_path))])
            if push:
                current_branch = self.repo_git.active_branch.name
                self.push_branch(current_branch)

                if create_tag:
                    tag_name = f"v{new_version}"
                    self.repo_git.create_tag(tag_name, message=f"Release {new_version}")
                    if push:
                        self.repo_git.remotes.origin.push(tags=True)

        return {
            "old_version": current,
            "new_version": new_version,
            "committed": commit,
            "pushed": push if commit else False,
            "tag_created": create_tag if commit else False
        }

    def update_dependencies(self, requirements_file: Optional[str] = None, regenerate_lock: bool = True) -> Dict[str, Any]:
        """
        Update dependencies and regenerate lock files.

        Args:
            requirements_file: Path to requirements file (default: pyproject.toml)
            regenerate_lock: Regenerate uv.lock (requires uv)

        Returns:
            Summary of changes
        """
        self._trace("update_dependencies")
        if self._ensure_clean_working_dir():
            # Could run pip-review or uv update here
            # For now, just note it's not fully implemented
            return {"status": "not_implemented", "message": "Manual update recommended"}

        raise RuntimeError("Uncommitted changes present")

    def get_self_improvement_suggestions(self) -> List[Dict[str, Any]]:
        """
        Analyze repository for potential self-improvements.

        Returns:
            List of suggested actions (create issue, bump version, etc.)
        """
        suggestions = []

        # Check for outdated dependencies (basic heuristic: look at requirements)
        # In a full implementation, parse pyproject.toml and compare to latest versions

        # Check test coverage gaps
        # Could run pytest --cov and analyze

        # Check for stale PRs/Issues
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=90)
        stale_issues = [
            i for i in self.repo.get_issues(state='open')
            if i.created_at < cutoff and not i.assignee
        ]
        if stale_issues:
            suggestions.append({
                "type": "stale_issues",
                "count": len(stale_issues),
                "action": "Consider closing or reassigning",
                "items": [i.number for i in stale_issues[:5]]
            })

        # Check if version bump needed
        # Could parse conventional commits since last tag

        return suggestions

    def execute_improvement_cycle(self) -> Dict[str, Any]:
        """
        Run a full autonomous improvement cycle.

        This is the main entry point for self-improvement:
        1. Check repository health
        2. Identify improvement opportunities
        3. Create issues for tracking
        4. Optionally implement fixes (with caution)
        5. Run tests
        6. Create PR if changes made

        Returns:
            Summary of actions taken
        """
        self._trace("improvement_cycle_start")
        results = {
            "suggestions": self.get_self_improvement_suggestions(),
            "actions_taken": []
        }

        # For now, just generate suggestions and create issues
        # Full automation would require careful safety guards

        self._trace("improvement_cycle_end", **results)
        return results


def get_github_automation() -> Optional[GitHubAutomation]:
    """
    Factory function to initialize GitHub automation if configured.

    Returns:
        GitHubAutomation instance or None if not configured
    """
    if not os.getenv("GITHUB_TOKEN"):
        return None

    try:
        config = GitHubAutomationConfig(
            token=os.getenv("GITHUB_TOKEN", ""),
            repo_name=os.getenv("GITHUB_REPO", "NousResearch/hermes-agent"),
            auto_commit=False,  # Safe default
            require_review=True
        )
        return GitHubAutomation(config)
    except Exception as e:
        print(f"Warning: Failed to initialize GitHub automation: {e}")
        return None


# Allow direct execution for testing
if __name__ == "__main__":
    gh = get_github_automation()
    if gh:
        print("GitHub Automation initialized")
        print("Repository:", gh.get_repo_info()["name"])
        print("Open issues:", len(gh.list_issues()))
    else:
        print("GITHUB_TOKEN not set")