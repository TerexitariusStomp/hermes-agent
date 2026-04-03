
# GitHub Automation Skill - Quick Start

## Installation

1. Ensure you are in the hermes-agent repository
2. Install optional dependencies:

   Using uv:
   ```bash
   uv pip install -e ".[github]"
   ```

   Using pip:
   ```bash
   pip install hermes-agent[github]
   ```

3. Verify GITHUB_TOKEN is set in ~/.hermes/.env:

   ```bash
   export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
   ```

   The token needs: `repo` (full control of private repositories) or at least `public_repo` + `write:discussion` etc. for public repos.

4. Test the skill:

   ```python
   from github_automation import get_github_automation
   gh = get_github_automation()
   if gh:
       print(gh.get_repo_info())
   ```

## Using from Hermes CLI

Once the skill is loaded (Hermes v0.6+ auto-discovers ~/.hermes/skills/), you can use tools:

- `hermes github_info` — get repository info
- `hermes github_list_issues` — list open issues
- `hermes github_create_issue --title "Bug" --body "desc"` — create issue
- `hermes github_run_tests` — run tests and see results
- `hermes github_bump_version --part minor` — bump version

## Safety Features

- **Auto-commit disabled by default** (`auto_commit=False`)
- **Draft PRs** by default (`draft=True`)
- All actions traced to Langfuse/Langfuse if configured
- Requires explicit Hermes decision to create commits/PRs
- Working directory must be clean for version bumps

## Autonomous Self-Improvement Cycle

Hermes can call `github_improvement_cycle()` to:

1. Analyze repository health
2. Detect stale issues (older than 90 days)
3. Identify potential improvements (coverage, dependencies)
4. Optionally implement fixes (future: via coding skills)
5. Run tests, create PRs

Currently, the cycle generates suggestions. Full automation requires careful testing.

## Typical Workflow

```python
# 1. Discover current state
issues = github_list_issues(state='open')
ci = github_ci_status()

# 2. If changes made locally (by Hermes self-modifying), commit and push
# (Hermes decision point - requires explicit approval)
# github_commit_changes("feat: add new skill for X", files=["skills/new_skill.py"])
# github_push_branch("feature-branch")
# github_create_pull("Add new X skill", "Description", head="feature-branch", draft=True)

# 3. Run tests and report failures as issues
tests = github_run_tests()
if not tests['passed']:
    github_create_issue(
        title=f"Test failures: {tests['stderr'][:100]}...",
        body=f"```\n{tests['stdout']}\n```",
        labels=["bug", "ci"]
    )

# 4. Version bump when ready for release
github_bump_version(part='minor', commit=True, push=False)  # push manually after review
```

## Security Note

- The skill uses `GITHUB_TOKEN` from environment
- Never commit `.env` with real tokens
- The repository's `.gitignore` already excludes `.env`
- All automated actions are logged for audit

## Troubleshooting

- `ImportError: No module named 'PyGithub'` → Install `.[github]` dependencies
- `RuntimeError: GitHub automation not available` → Check `GITHUB_TOKEN` is set and valid
- Permission errors → Ensure token has `repo` scope (for private) or `public_repo` (for public)
- Auto-commit prevented → Set `auto_commit=True` in config (use with caution)
