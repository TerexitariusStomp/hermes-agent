"""
Self-evaluation criteria and scoring rubrics for skill quality assessment.
Defines structured dimensions for evaluating skills objectively.
Adapted from EvoAgentX's built-in evaluator patterns and TextGrad optimization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension."""
    dimension: str
    score: float  # 0-100
    weight: float
    reasoning: str = ""  # Why this score was given
    
    @property
    def weighted_score(self) -> float:
        return self.score * self.weight


@dataclass 
class SkillEvaluation:
    """Complete evaluation of a skill."""
    skill_name: str
    skill_path: str
    dimensions: List[DimensionScore] = field(default_factory=list)
    overall_score: float = 0.0
    weakest: Optional[str] = None
    strongest: Optional[str] = None
    tier: str = "?"
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = ""
    
    # TextGrad-style: store the "gradient" (what specifically to change)
    improvement_gradient: Dict[str, str] = field(default_factory=dict)
    
    def classify(self):
        if self.overall_score >= 85:
            self.tier = "A"
        elif self.overall_score >= 70:
            self.tier = "B"  
        elif self.overall_score >= 55:
            self.tier = "C"
        elif self.overall_score >= 40:
            self.tier = "D"
        else:
            self.tier = "F"
        
        if self.dimensions:
            self.weakest = min(self.dimensions, key=lambda d: d.score).dimension
            self.strongest = max(self.dimensions, key=lambda d: d.score).dimension


def build_self_eval_prompt(skill_name: str, skill_content: str, 
                           usage_stats: Optional[Dict] = None) -> str:
    """Build a structured evaluation prompt for LLM-based skill assessment.
    
    This is the TextGrad-inspired approach: use the LLM itself as the 
    gradient estimator to tell us exactly what to improve.
    """
    prompt = f"""Evaluate the following Hermes Agent skill for quality and provide specific, actionable improvement suggestions.

## Skill: {skill_name}

{skill_content}
"""
    
    if usage_stats:
        prompt += f"""
## Usage Statistics
- Total uses: {usage_stats.get("total_uses", 0)}
- Success rate: {usage_stats.get("success_rate", 0)}%
- Error rate: {usage_stats.get("error_rate", 0)}%
- Top errors: {usage_stats.get("error_types", "none")}
"""
    
    prompt += """
## Evaluation Criteria

Score each dimension 0-100:

### 1. Applicability (weight: 20%)
- How clearly does it describe WHEN to use this skill?
- Are trigger conditions explicit?
- Does it prevent inappropriate usage?

### 2. Actionability (weight: 30%)
- Are steps specific and executable (exact commands, paths, flags)?
- Can the agent follow it without guessing?
- Does it use concrete examples rather than abstract guidance?

### 3. Pitfalls & Failure Modes (weight: 20%)
- Does it warn about known failure modes?
- Are there workarounds for common errors?
- Does it document what NOT to do?

### 4. Verification (weight: 15%)
- Are there clear success criteria?
- Can the agent verify the skill worked correctly?
- Is there expected output to compare against?

### 5. Freshness (weight: 15%)
- Are commands/paths up-to-date with current system state?
- Would the instructions work if followed today?

## Response Format

Respond as valid JSON:
{
  "scores": {
    "applicability": {"score": 0-100, "reasoning": "specific explanation"},
    "actionability": {"score": 0-100, "reasoning": "specific explanation"},
    "pitfalls": {"score": 0-100, "reasoning": "specific explanation"},
    "verification": {"score": 0-100, "reasoning": "specific explanation"},
    "freshness": {"score": 0-100, "reasoning": "specific explanation"}
  },
  "overall_improvement_gradient": {
    "highest_priority_fix": "the single most impactful change",
    "specific_mutation": "exact text to add/replace/remove",
    "anti_patterns_to_avoid": ["list of things that would make this worse"]
  }
}

Be specific. Don't say "add more detail" -- say exactly what detail is missing.
Don't say "improve examples" -- give a concrete example that should be added.
"""
    return prompt


def grade_skill_from_llm_response(llm_response: str, skill_name: str, 
                                  skill_path: str) -> Optional[SkillEvaluation]:
    """Parse LLM evaluation response into a structured SkillEvaluation.
    
    Returns None if the response couldn't be parsed.
    """
    import json
    import re
    from datetime import datetime, timezone
    
    # Try to extract JSON from response
    json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', llm_response, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            return None
    else:
        try:
            data = json.loads(llm_response)
        except json.JSONDecodeError:
            return None
    
    scores_data = data.get("scores", {})
    weights = {
        "applicability": 0.20,
        "actionability": 0.30,
        "pitfalls": 0.20,
        "verification": 0.15,
        "freshness": 0.15,
    }
    
    dimensions = []
    overall = 0.0
    for dim_name, weight in weights.items():
        score_info = scores_data.get(dim_name, {})
        dim_score = score_info.get("score", 0)
        reasoning = score_info.get("reasoning", "")
        dimensions.append(DimensionScore(
            dimension=dim_name,
            score=dim_score,
            weight=weight,
            reasoning=reasoning,
        ))
        overall += dim_score * weight
    
    gradient = data.get("overall_improvement_gradient", {})
    
    eval_result = SkillEvaluation(
        skill_name=skill_name,
        skill_path=skill_path,
        dimensions=dimensions,
        overall_score=round(overall, 1),
        timestamp=datetime.now(timezone.utc).isoformat(),
        improvement_gradient=gradient,
    )
    eval_result.classify()
    
    return eval_result
