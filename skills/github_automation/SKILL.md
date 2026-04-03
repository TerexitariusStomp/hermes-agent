---
name: github-automation
description: Autonomous GitHub repository management and self-improvement for Hermes
version: 0.1.0
author: Hermes Agent
tags: [github, automation, devops, self-improvement]
---

# GitHub Automation Skill

Enables Hermes to autonomously manage its GitHub repository: create issues, submit PRs, run tests, bump versions, and implement self-improvements.

## Setup

Requires:
- `GITHUB_TOKEN` in environment with `repo` scope
- Repository must already be cloned and tracked by git
- PyGithub and GitPython installed: `pip install PyGithub GitPython`

The skill automatically initializes when imported if `GITHUB_TOKEN` is set.

## Tools

### `github_info()`
Get repository metadata.
- Returns: dict with name, description, stars, forks, issues, etc.

### `github_list_issues(state='open', labels=None)`
List repository issues.
- `state`: 'open', 'closed', or 'all'
- `labels`: optional list of label names
- Returns: list of issue dicts (number, title, body, url, labels)

### `github_create_issue(title, body, labels=None)`
Create a new GitHub issue.
- `title`: issue title (string, required)
- `body`: issue description (Markdown supported)
- `labels`: optional list of label strings
- Returns: dict with issue number and url

### `github_create_branch(branch_name, source_branch=None)`
Create a new branch.
- `branch_name`: name of the new branch
- `source_branch`: source branch (default: main)
- Returns: True on success

### `github_commit_changes(message, files=None, branch=None)`
Commit changes to repository.
- `message`: commit message
- `files`: list of specific file paths (None for all changes)
- `branch`: branch to commit to (default: current)
- Returns: commit SHA
- **Safety**: Requires auto_commit=False (default). Integrations should push after committing.

### `github_push_branch(branch_name=None)`
Push local branch to remote.
- `branch_name`: branch to push (default: current)
- Returns: True on success

### `github_create_pr(title, body, head, base=None, draft=True)`
Create a pull request.
- `title`: PR title
- `body`: PR description
- `head`: head branch name (source)
- `base`: base branch (default: main)
- `draft`: create as draft PR (default: True)
- Returns: dict with PR number and url

### `github_ci_status(ref=None)`
Check CI status for a commit/branch.
- `ref`: commit SHA or branch name (default: HEAD)
- Returns: dict with state, passes, failures, pending checks

### `github_add_comment(issue_number, body)`
Add a comment to an issue or PR.
- `issue_number`: issue or PR number
- `body`: comment text (Markdown supported)
- Returns: True on success

### `github_bump_version(part='patch', commit=True, push=False, create_tag=True, changelog=True)`
Bump semantic version.
- `part`: 'major', 'minor', or 'patch'
- `commit`: commit version changes
- `push`: push commit and tag
- `create_tag`: create git tag (vX.Y.Z)
- `changelog`: auto-update CHANGELOG.md
- Returns: dict with old/new versions and actions

### `github_run_tests(test_path=None)`
Run pytest locally.
- `test_path`: specific test file/directory (None for all tests)
- Returns: dict with exit_code, stdout tail, stderr tail, passed bool

### `github_get_suggestions()`
Get self-improvement suggestions.
- Returns: list of suggested actions (stale issues, coverage gaps, etc.)

### `github_improvement_cycle()`
Run full autonomous improvement cycle (currently just generates suggestions).
- Returns: summary with suggestions and actions taken

## Usage Example

```python
from hermes_agent.github_automation import get_github_automation

gh = get_github_automation()
if gh:
    # Check repository health
    info = gh.get_repo_info()
    print(f"Repository: {info['name']}")

    # See if there are open issues
    issues = gh.list_issues(state='open')
    for issue in issues[:5]:
        print(f"#{issue['number']}: {issue['title']}")

    # Create an issue if needed
    if not issues:
        gh.create_issue(
            title="Automated check: All clear",
            body="GitHub automation is functioning correctly.",
            labels=["automation"]
        )

    # Run tests and report
    tests = gh.run_tests()
    if not tests['passed']:
        gh.create_issue(
            title="Test failures detected by automation",
            body=f"Tests failing:\n```\n{tests['stderr']}\n```",
            labels=["bug", "ci"]
        )
```

## Safety & Observability

- All actions are traced via Langfuse/Langfuse if configured
- Auto-commit is **disabled by default** (`auto_commit=False`)
- Draft PRs by default (`draft=True`)
- Commits require confirmation in higher-level workflows
- Integration with Honcho memory for decision context

## Future Enhancements

- Dependency update with version checking
- Conventional commit parsing for automated changelog
- Test coverage analysis
- Auto-fix common lint issues
- Stale issue/PR closure automation
- Release automation (GitHub Releases)
- Security vulnerability scanning