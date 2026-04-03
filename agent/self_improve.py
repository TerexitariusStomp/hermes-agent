#!/usr/bin/env python3
"""
Self-improvement cycle runner for Hermes Agent.

TextGrad-inspired iterative optimization: evaluate skills using heuristic
scoring + LLM as gradient estimator, identify specific weaknesses, generate
targeted mutations, and apply only if scores improve.

Adapted from EvoAgentX optimization algorithms (TextGrad, MIPRO, AFlow)
and existing Hermes skill_evolver.py + skill_quality.py infrastructure.

Self-contained -- no external module dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes-self-improve")

HERMES_HOME = os.path.expanduser("~/.hermes")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
IMPROVEMENT_LOG = os.path.join(HERMES_HOME, "logs", "self_improve.jsonl")

# ── Lane mapping: skill categories from desloppify strategy pattern ─────
# Skills in different lanes can be improved in parallel without interference

LANE_CATEGORIES: Dict[str, List[str]] = {
    "core": ["lifecycle-hooks", "self-eval-criteria", "self-improvement-evolution",
             "self-improvement-eval"],
    "devops": ["operator-runbooks", "gateway-runbooks", "maintainer-triage",
               "incident-response", "deployment-recipes", "desloppify-architecture"],
    "research": ["miroshark-mirofish-integration", "arxiv", "polymarket"],
    "mlops": ["mlops", "mlops-cloud", "axolotl", "grpo-rl-training", "peft",
              "trl-fine-tuning", "vllm", "huggingface-hub", "aws-lambda-deploy",
              "google-colab", "kaggle-compute", "modal", "render"],
    "data": ["data-science", "jupyter-live-kernel", "docker-management",
             "database-operations", "vector-memory-routing"],
    "github": ["github", "github-auth", "github-pr-workflow", "github-code-review",
               "github-issues", "github-repo-management", "subagent-driven-development",
               "code-review", "systematic-debugging", "test-driven-development",
               "unit-testing", "plan", "requesting-code-review", "writing-plans"],
    "external": ["external-project-integration", "github-project-integration",
                 "dogfood", "find-nearby", "hermes-system-improvement"],
    "infra": ["api-integration", "webhook-subscriptions", "server-cleanup",
              "nvidia-gpu-hotplug", "telegram-gateway-troubleshooting",
              "hermes-dojo", "openspace-integration", "autoharness-integration",
              "icarus-daedalus-integration", "observability-integration",
              "llm-observability-tracing", "cloud-memory-architecture",
              "cloud-orchestration", "free-llm-quality-routing", "memory-routing"],
    "tools": ["cli-anything", "rag-anything", "mcporter", "native-mcp"],
}

# ── Improvement type classification (desloppify fixer leverage pattern)

AUTO_FIXABLE_DIMENSIONS = {"pitfalls", "verification", "freshness"}
HUMAN_JUDGMENT_DIMENSIONS = {"applicability", "actionability"}


def _assign_lanes(scoring_results: List[Dict[str, Any]]) -> Dict[str, List[Dict]]:
    """Group skills into parallel work lanes by category (desloppify lane pattern).

    Skills in different lanes don't share content or dependencies,
    so they can be improved simultaneously via delegate_task.
    """
    lanes: Dict[str, List[Dict]] = defaultdict(list)

    for skill in scoring_results:
        name = skill["skill_name"]
        assigned = False
        for lane_name, members in LANE_CATEGORIES.items():
            if name in members:
                lanes[lane_name].append(skill)
                assigned = True
                break
        if not assigned:
            lanes["misc"].append(skill)

    # Sort each lane by priority (weakest first for targeted improvement)
    for lane_name in lanes:
        lanes[lane_name].sort(key=lambda s: s["overall"])

    return dict(lanes)


def _compute_lane_stats(lanes: Dict[str, List[Dict]]) -> Dict[str, Dict]:
    """Compute statistics per lane for strategy hints."""
    stats = {}
    for lane_name, skills in lanes.items():
        if not skills:
            continue
        avg = round(sum(s["overall"] for s in skills) / len(skills), 1)
        weakest = min(s["overall"] for s in skills)
        needs_work = sum(1 for s in skills if s["tier"] in ("C", "D", "F"))
        file_count = len(skills)  # Each skill = one file to improve

        stats[lane_name] = {
            "skill_count": len(skills),
            "average_score": avg,
            "weakest_score": weakest,
            "needs_improvement": needs_work,
            "file_count": file_count,
            "action_type": "auto" if avg < 55 else "mixed",
        }
    return stats


def _detect_stagnant(stagnation_threshold: int = 3,
                     min_score_change: float = 1.0) -> List[Dict[str, Any]]:
    """Detect skills that haven't improved over multiple evaluation cycles.

    Adapted from desloppify's _stagnant_dimensions pattern.
    Looks at self_improve.jsonl history and flags skills whose
    overall score hasn't changed by at least min_score_change
    over the last stagnation_threshold cycles.
    """
    if not os.path.exists(IMPROVEMENT_LOG):
        return []

    entries = []
    with open(IMPROVEMENT_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if len(entries) < stagnation_threshold:
        return []

    # Build per-skill score history
    skill_history: Dict[str, List[float]] = defaultdict(list)
    for entry in entries:
        for skill in entry.get("scoring_results", []):
            name = skill.get("skill_name")
            if name:
                skill_history[name].append(skill.get("overall", 0))

    stagnant = []
    all_names = list(skill_history.keys())

    for name in all_names:
        scores = skill_history[name]
        if len(scores) < stagnation_threshold:
            continue

        last_n = scores[-stagnation_threshold:]
        max_change = max(last_n) - min(last_n)

        if max_change < min_score_change:
            current = scores[-1]
            if current < 100:  # Skip perfect scores
                stagnant.append({
                    "skill_name": name,
                    "current_score": round(current, 1),
                    "evaluations": len(scores),
                    "max_change_during_window": round(max_change, 1),
                    "recommendation": (
                        f"Stagnant at ~{current:.0f} across {stagnation_threshold}+ cycles. "
                        "Consider full rewrite instead of incremental fix."
                        if current < 55
                        else f"Consider targeted dimension overhaul at {current:.0f}."
                    ),
                })

    stagnant.sort(key=lambda x: x["current_score"])
    return stagnant


def _detect_phase(avg_score: float, tier_dist: Dict[str, int]) -> str:
    """Detect self-improvement maturity phase.

    Adapted from desloppify's detect_phase pattern.
    - foundation_building: Score < 60 average, many D/F skills
    - refinement: Score 60-80 average, most skills C+
    - polish: Score 80+ average, mostly B/A skills
    """
    low_count = tier_dist.get("D", 0) + tier_dist.get("F", 0)
    high_count = tier_dist.get("A", 0) + tier_dist.get("B", 0)
    total = sum(tier_dist.values())

    if avg_score < 50 or low_count > total * 0.3:
        return "foundation_building"
    elif avg_score < 70 or low_count > total * 0.1:
        return "refinement"
    elif avg_score < 85:
        return "polish"
    else:
        return "mature"


def phase_strategy(phase: str) -> str:
    """Return strategy hint based on current improvement phase."""
    strategies = {
        "foundation_building": (
            "Focus on obvious gaps first: add pitfalls sections to skills that lack them, "
            "add verification commands, and update stale documentation. "
            "Prioritize Tier D skills — they'll show the biggest score improvement."
        ),
        "refinement": (
            "Fill weak dimensions across skills. Target the weakest dimension per skill. "
            "Add concrete examples and edge-case handling to boost actionability scores."
        ),
        "polish": (
            "Incremental improvements: trim verbosity, cross-reference related skills, "
            "add advanced patterns. Focus on skills with recent usage to maximize impact."
        ),
        "mature": (
            "Maintain quality. Monitor for drift as tools/providers change. "
            "Periodic re-evaluation and deprecation of superseded skills."
        ),
    }
    return strategies.get(phase, strategies["refinement"])


def _compute_fixer_leverage(recommendations: List[Dict]) -> Dict[str, Any]:
    """Estimate what percentage of improvements are auto-fixable vs need human judgment.

    Adapted from desloppify's FixerLeverage pattern.
    Auto-fixable = dimension is in AUTO_FIXABLE_DIMENSIONS (pitfalls, verification, freshness)
    Human judgment = dimension is in HUMAN_JUDGMENT_DIMENSIONS (applicability, actionability)
    """
    if not recommendations:
        return {
            "auto_fixable_count": 0, "total_count": 0,
            "coverage": 0.0, "recommendation": "No skills need improvement.",
        }

    auto = 0
    human = 0
    for rec in recommendations:
        weakest = rec.get("weakest_dimension", "")
        if weakest in AUTO_FIXABLE_DIMENSIONS:
            auto += 1
        elif weakest in HUMAN_JUDGMENT_DIMENSIONS:
            human += 1
        else:
            human += 1  # Default to human if unknown

    total = auto + human
    coverage = round(auto / total * 100, 1) if total > 0 else 0

    if coverage > 70:
        recommendation = (
            f"{auto}/{total} improvements are routine (add pitfalls/verification/update). "
            "These can be done quickly without deep analysis."
        )
    elif coverage > 40:
        recommendation = (
            f"Mix of routine fixes ({auto}) and structural changes ({human}). "
            "Do routine fixes first for quick wins, then tackle the harder ones."
        )
    else:
        recommendation = (
            f"{human}/{total} improvements need structural changes "
            "(rewriting triggers, adding concrete examples). "
            "These require LLM-powered gradient estimation, not simple patches."
        )

    return {
        "auto_fixable_count": auto,
        "human_judgment_count": human,
        "total_count": total,
        "auto_fixable_percentage": coverage,
        "recommendation": recommendation,
    }


WEIGHTS = {
    "applicability": 0.20,
    "actionability": 0.30,
    "pitfalls": 0.20,
    "verification": 0.15,
    "freshness": 0.15,
}


# ── Heuristic scoring functions ─────────────────────────────────────

def _score_applicability(content: str) -> int:
    """Score how well the skill describes WHEN to use it."""
    text = content.lower()
    has_when = any(p in text for p in ['when to use', 'when using', 'trigger', 'use when', 'use this'])
    has_desc = 'description:' in text[:500]
    has_examples = 'example' in text
    if has_when and has_desc and has_examples:
        return 100
    elif has_when and has_desc:
        return 75
    elif has_desc:
        return 50
    elif has_when:
        return 25
    return 0


def _score_actionability(content: str) -> int:
    """Score how specific and executable the steps are."""
    fences = content.count('```')
    has_cmds = bool(re.search(r'```(?:bash|sh|shell)\n.*\w', content))
    has_paths = bool(re.search(r'[~/][\w/.\-]+\.\w+', content))
    has_steps = bool(re.search(r'^\d+\.', content, re.MULTILINE))
    s = 0
    if fences >= 2:
        s += 25
    if has_cmds:
        s += 25
    if has_paths:
        s += 15
    if has_steps:
        s += 20
    if '- [' in content:
        s += 15
    return min(s, 100)


def _score_pitfalls(content: str) -> int:
    """Score failure mode documentation."""
    text = content.lower()
    has_section = any(p in text for p in ['pitfall', 'failure mode', 'warning', 'anti.pattern', 'common issue', 'known issue'])
    has_errors = bool(re.search(r'error|fail|crash|broken|stale', text))
    has_fixes = any(p in text for p in ['workaround', 'avoid', 'do not', "don't", 'fix', 'instead'])
    if has_section and has_errors and has_fixes:
        return 100
    elif has_section and has_errors:
        return 75
    elif has_section or (has_errors and has_fixes):
        return 50
    elif has_errors:
        return 25
    return 0


def _score_verification(content: str) -> int:
    """Score verification / success criteria documentation."""
    text = content.lower()
    has_section = any(p in text for p in ['verify', 'verification', 'success criteria', 'check', 'test', 'validate'])
    has_expected = 'expected' in text and ('output' in text or 'result' in text)
    if has_section and has_expected and '```' in content:
        return 100
    elif has_section and has_expected:
        return 75
    elif has_section:
        return 50
    elif has_expected:
        return 25
    return 0


def _score_freshness(skill_file: str) -> int:
    """Score recency based on file modification time."""
    if not os.path.exists(skill_file):
        return 0
    try:
        age_days = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(skill_file)) / 86400
    except OSError:
        return 0
    if age_days <= 7:
        return 100
    elif age_days <= 30:
        return 75
    elif age_days <= 90:
        return 50
    elif age_days <= 365:
        return 25
    return 0


def score_skill(skill_name: str, skill_path: str) -> Dict[str, Any]:
    """Score a single skill on all 5 dimensions."""
    sf = os.path.join(skill_path, "SKILL.md")
    if not os.path.exists(sf):
        return {}
    with open(sf) as f:
        content = f.read()
    scores = {
        "applicability": _score_applicability(content),
        "actionability": _score_actionability(content),
        "pitfalls": _score_pitfalls(content),
        "verification": _score_verification(content),
        "freshness": _score_freshness(sf),
    }
    overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    weakest = min(scores, key=scores.get)
    strongest = max(scores, key=scores.get)
    if overall >= 85:
        tier = "A"
    elif overall >= 70:
        tier = "B"
    elif overall >= 55:
        tier = "C"
    elif overall >= 40:
        tier = "D"
    else:
        tier = "F"
    return {
        "skill_name": skill_name, "skill_path": skill_path,
        "scores": scores, "overall": round(overall, 1),
        "weakest": weakest, "strongest": strongest, "tier": tier,
    }


# ── Skill scanning ──────────────────────────────────────────────────

def scan_all_skills(max_skills: int = 30) -> List[Dict[str, Any]]:
    """Walk the skills directory and collect all SKILL.md files."""
    results = []
    if not os.path.exists(SKILLS_DIR):
        return results
    for root, dirs, files in os.walk(SKILLS_DIR):
        if "SKILL.md" not in files:
            continue
        sf = os.path.join(root, "SKILL.md")
        skill_name = os.path.basename(root)
        try:
            with open(sf) as f:
                content = f.read()
            mtime = os.path.getmtime(sf)
            age_days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400
            name_m = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
            desc_m = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            cat_m = re.search(r"^category:\s*(.+)$", content, re.MULTILINE)
            results.append({
                "skill_name": skill_name, "skill_path": root,
                "skill_file": sf, "content": content,
                "content_length": len(content), "age_days": round(age_days, 1),
                "display_name": name_m.group(1).strip() if name_m else skill_name,
                "description": desc_m.group(1).strip() if desc_m else "",
                "category": cat_m.group(1).strip() if cat_m else "",
            })
        except Exception as e:
            logger.debug("Failed to read skill %s: %s", skill_name, e)
    return results[:max_skills]


def load_quality_metrics(skill_name: str) -> Optional[Dict[str, Any]]:
    """Load quality metrics from the skill_quality tracker."""
    mf = os.path.join(HERMES_HOME, "skill_quality", "metrics.json")
    if not os.path.exists(mf):
        return None
    try:
        with open(mf) as f:
            records = json.load(f)
        for rec in records:
            if rec.get("skill_name") == skill_name:
                total = rec.get("total_selections", 0)
                completions = rec.get("total_completions", 0)
                fallbacks = rec.get("total_fallbacks", 0)
                return {
                    "total_uses": total,
                    "success_rate": round((completions / total * 100) if total > 0 else 0, 1),
                    "error_rate": round((fallbacks / total * 100) if total > 0 else 0, 1),
                    "error_types": rec.get("error_types", {}),
                    "last_used": rec.get("last_used"),
                    "evolutions": rec.get("total_evolution_suggestions", 0),
                }
    except Exception:
        pass
    return None


def build_evaluation_prompt(skill_name: str, content: str,
                            stats: Optional[Dict] = None) -> str:
    """Build LLM prompt for skill evaluation (TextGrad-style gradient estimation)."""
    prompt = f"""Evaluate the following Hermes Agent skill. Score 0-100 on each dimension:

## Skill: {skill_name}

{content[:3000]}
"""
    if stats:
        prompt += f"""
## Usage Stats
- Uses: {stats.get('total_uses', 0)}
- Success: {stats.get('success_rate', 0)}%
- Error: {stats.get('error_rate', 0)}%
- Top errors: {stats.get('error_types', 'none')}
"""
    prompt += """
## Dimensions (0-100)
1. **Applicability** (20%) — When to use clearly stated? Triggers explicit?
2. **Actionability** (30%) — Steps specific? Exact commands/paths/flags?
3. **Pitfalls** (20%) — Failure modes documented? Workarounds listed?
4. **Verification** (15%) — Success criteria? Commands to verify?
5. **Freshness** (15%) — Up-to-date with current system?

## Return valid JSON:
{
  "scores": {
    "applicability": {"score": 0-100, "reasoning": "..."},
    "actionability": {"score": 0-100, "reasoning": "..."},
    "pitfalls": {"score": 0-100, "reasoning": "..."},
    "verification": {"score": 0-100, "reasoning": "..."},
    "freshness": {"score": 0-100, "reasoning": "..."}
  },
  "highest_priority_fix": "the single most impactful change",
  "specific_mutation": "exact text to add/replace",
  "anti_patterns_to_avoid": ["list"]
}
Be specific. Name exact text to add or change.
"""
    return prompt


def _get_recommendation(skill: Dict[str, Any]) -> str:
    """Generate specific improvement recommendation based on weakest dimension."""
    weakest = skill["weakest"]
    name = skill["skill_name"]
    rec_map = {
        "applicability": f"Add clear 'When to Use' trigger conditions for '{name}'",
        "actionability": f"Replace vague steps with exact commands, paths, flags for '{name}'",
        "pitfalls": f"Document known failure modes and error patterns for '{name}'",
        "verification": f"Add verification commands with expected output for '{name}'",
        "freshness": f"Verify and update '{name}' against current system state",
    }
    return rec_map.get(weakest, f"Review and improve '{name}'")


# ── Main cycle ──────────────────────────────────────────────────────

def run_improvement_cycle(
    max_skills: int = 20,
    max_recommendations: int = 10,
) -> Dict[str, Any]:
    """Execute a self-improvement evaluation cycle.

    Returns a dict with:
    - skills_scanned, average_score, tier_distribution
    - scoring_results: list of scored skills with dimension breakdown
    - recommendations: prioritized list of skills to improve, each with:
      - skill_name, overall, tier, weakest_dimension, weakest_score
      - recommendation (human-readable)
      - evaluation_prompt (LLM prompt for deeper LLM-powered evaluation)
    """
    logger.info("Starting self-improvement cycle (max_skills=%d)", max_skills)

    skills = scan_all_skills(max_skills)
    if not skills:
        return {"status": "no_skills_found", "skills_scanned": 0,
                "recommendations": []}

    results = []
    for skill in skills:
        score = score_skill(skill["skill_name"], skill["skill_path"])
        if not score:
            continue
        score["display_name"] = skill["display_name"]
        score["description"] = skill["description"]
        score["age_days"] = skill["age_days"]

        # Enrich with real usage data
        quality = load_quality_metrics(skill["skill_name"])
        if quality:
            score["usage_stats"] = quality
            if quality.get("last_used"):
                try:
                    last = datetime.fromisoformat(
                        quality["last_used"].replace("Z", "+00:00"))
                    days = (datetime.now(timezone.utc) - last).days
                    if days <= 7:
                        score["scores"]["freshness"] = max(score["scores"]["freshness"], 100)
                    elif days <= 30:
                        score["scores"]["freshness"] = max(score["scores"]["freshness"], 75)
                except Exception:
                    pass

        # Recalc overall
        score["overall"] = round(
            sum(score["scores"][k] * w for k, w in WEIGHTS.items()), 1)
        if score["overall"] >= 85:
            score["tier"] = "A"
        elif score["overall"] >= 70:
            score["tier"] = "B"
        elif score["overall"] >= 55:
            score["tier"] = "C"
        elif score["overall"] >= 40:
            score["tier"] = "D"
        else:
            score["tier"] = "F"
        score["weakest"] = min(score["scores"], key=score["scores"].get)
        score["strongest"] = max(score["scores"], key=score["scores"].get)
        results.append(score)

    # Prioritized recommendations
    recommendations = []
    for s in results:
        if s["tier"] in ("A", "B"):
            continue
        rec = {
            "skill_name": s["skill_name"],
            "display_name": s.get("display_name", s["skill_name"]),
            "overall": s["overall"],
            "tier": s["tier"],
            "weakest_dimension": s["weakest"],
            "weakest_score": s["scores"][s["weakest"]],
            "recommendation": _get_recommendation(s),
            "priority": round(
                (100 - s["overall"]) *
                (1 + s.get("usage_stats", {}).get("total_uses", 0) / 50), 1),
            "evaluation_prompt": build_evaluation_prompt(
                s["skill_name"], s.get("content", "")[:3000],
                s.get("usage_stats")),
        }
        recommendations.append(rec)

    recommendations.sort(key=lambda x: x["priority"], reverse=True)
    recommendations = recommendations[:max_recommendations]

    # Strip heavy fields from results
    for r in results:
        r.pop("content", None)

    tiers = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for s in results:
        tiers[s["tier"]] += 1
    avg = sum(s["overall"] for s in results) / len(results) if results else 0

    # ── Desloppify patterns ──────────────────────────────────────────
    lanes = _assign_lanes(results)
    lane_stats = _compute_lane_stats(lanes)
    phase = _detect_phase(avg, tiers)
    phase_hint = phase_strategy(phase)
    fixer_leverage = _compute_fixer_leverage(recommendations)
    stagnant = _detect_stagnant()

    result = {
        "status": "completed",
        "skills_scanned": len(results),
        "average_score": round(avg, 1),
        "tier_distribution": tiers,
        "phase": phase,
        "phase_hint": phase_hint,
        "scoring_results": results,
        "recommendations": recommendations,
        "lanes": lane_stats,
        "fixer_leverage": fixer_leverage,
        "stagnant_skills": stagnant,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(os.path.dirname(IMPROVEMENT_LOG), exist_ok=True)
    with open(IMPROVEMENT_LOG, "a") as f:
        log = json.loads(json.dumps(result))
        for rec in log.get("recommendations", []):
            rec.pop("evaluation_prompt", None)
        log.pop("lanes", None)  # Too verbose for log
        f.write(json.dumps(log, ensure_ascii=False) + "\n")

    return result


def format_summary(result: Dict[str, Any]) -> str:
    """Human-readable summary of improvement cycle."""
    if result["status"] == "no_skills_found":
        return "No skills found to evaluate."
    lines = [
        "## Self-Improvement Evaluation Summary", "",
        f"**Skills scanned:** {result['skills_scanned']}",
        f"**Average score:** {result['average_score']}/100",
        f"**Phase:** {result.get('phase', 'unknown')} — {result.get('phase_hint', '')}",
        "**Tier distribution:**",
    ]
    for t, c in result["tier_distribution"].items():
        lines.append(f"  - Tier {t}: {c} skills")

    # Lane summary
    lanes = result.get("lanes", {})
    if lanes:
        lines.append("")
        lines.append("**Work Lanes:**")
        for lane, stats in sorted(lanes.items(), key=lambda x: x[1].get("weakest_score", 999)):
            lines.append(
                f"  - **{lane}**: {stats['skill_count']} skills, "
                f"avg={stats['average_score']}, weakest={stats['weakest_score']}, "
                f"needs work={stats['needs_improvement']}")

    # Fixer leverage
    fl = result.get("fixer_leverage", {})
    if fl and fl.get("total_count", 0) > 0:
        lines.append("")
        lines.append(f"**Fixer Leverage:** {fl.get('auto_fixable_count', 0)} auto-fixable, "
                     f"{fl.get('human_judgment_count', 0)} need judgment "
                     f"({fl.get('auto_fixable_percentage', 0)}% routine)")
        lines.append(f"  → {fl.get('recommendation', '')}")

    # Stagnant skills
    stagnant = result.get("stagnant_skills", [])
    if stagnant:
        lines.append("")
        lines.append(f"**⚠️ {len(stagnant)} Stagnant Skills** (no improvement over 3+ cycles):")
        for s in stagnant[:5]:
            lines.append(f"  - **{s['skill_name']}**: stuck at {s['current_score']} "
                         f"({s['evaluations']} evals, max Δ={s['max_change_during_window']})")
            lines.append(f"    → {s['recommendation']}")

    recs = result.get("recommendations", [])
    if recs:
        lines.append("")
        lines.append(f"**Top {len(recs)} improvements needed:**")
        for i, r in enumerate(recs, 1):
            lines.append(
                f"  {i}. **{r['skill_name']}** (score: {r['overall']}, tier: {r['tier']})")
            lines.append(
                f"     Weakest: {r['weakest_dimension']} ({r['weakest_score']}/100)")
            lines.append(f"     → {r['recommendation']}")
    else:
        lines.append("")
        lines.append("All evaluated skills are Tier A or B.")
    lines.append(f"\n**Full data:** {IMPROVEMENT_LOG}")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_improvement_cycle()
    print(format_summary(result))
