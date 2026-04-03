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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes-self-improve")

HERMES_HOME = os.path.expanduser("~/.hermes")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
IMPROVEMENT_LOG = os.path.join(HERMES_HOME, "logs", "self_improve.jsonl")

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

    result = {
        "status": "completed",
        "skills_scanned": len(results),
        "average_score": round(avg, 1),
        "tier_distribution": tiers,
        "scoring_results": results,
        "recommendations": recommendations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(os.path.dirname(IMPROVEMENT_LOG), exist_ok=True)
    with open(IMPROVEMENT_LOG, "a") as f:
        log = json.loads(json.dumps(result))
        for rec in log.get("recommendations", []):
            rec.pop("evaluation_prompt", None)
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
        "**Tier distribution:**",
    ]
    for t, c in result["tier_distribution"].items():
        lines.append(f"  - Tier {t}: {c} skills")
    recs = result.get("recommendations", [])
    if recs:
        lines.append(f"\n**Top {len(recs)} improvements needed:**")
        for i, r in enumerate(recs, 1):
            lines.append(
                f"  {i}. **{r['skill_name']}** (score: {r['overall']}, tier: {r['tier']})")
            lines.append(
                f"     Weakest: {r['weakest_dimension']} ({r['weakest_score']}/100)")
            lines.append(f"     -> {r['recommendation']}")
    else:
        lines.append("\nAll evaluated skills are Tier A or B.")
    lines.append(f"\n**Full data:** {IMPROVEMENT_LOG}")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_improvement_cycle()
    print(format_summary(result))
