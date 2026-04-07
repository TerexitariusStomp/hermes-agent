#!/usr/bin/env python3
"""
Benchmark-Driven Evolution Evaluator v2.

Evaluates the Hermes Agent codebase quality directly -- no agent startup needed.
Measures: code health, skill quality, tool coverage, error handling, test quality.
Scores are deterministic (AST analysis) + LLM-judged (semantic quality).
"""

import ast
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

BENCHMARK_DIR = os.path.expanduser("~/.hermes/benchmarks")
HERMES_HOME = os.path.expanduser("~/.hermes")
AGENT_DIR = os.path.join(HERMES_HOME, "hermes-agent")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")


def save_results(all_results):
    os.makedirs(BENCHMARK_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BENCHMARK_DIR, "evolution_bench_" + ts + ".json")
    with open(path, "w") as f:
        json.dump(all_results, f, indent=2)
    with open(os.path.join(BENCHMARK_DIR, "evolution_latest.json"), "w") as f:
        json.dump(all_results, f, indent=2)
    history_path = os.path.join(BENCHMARK_DIR, "evolution_history.jsonl")
    summary = {
        "timestamp": ts,
        "model": all_results.get("model", ""),
        "suites": {},
        "overall_mean": all_results.get("overall_mean", 0),
    }
    for s in all_results.get("suites", []):
        summary["suites"][s["suite"]] = s.get("mean_score", 0)
    with open(history_path, "a") as f:
        f.write(json.dumps(summary) + "\n")
    print("Results saved to " + path)


def find_py(directory):
    pattern = os.path.join(directory, "**", "*.py")
    return glob.glob(pattern, recursive=True)


def suite_code_health():
    scores = []
    findings = []
    py_files = find_py(AGENT_DIR)
    if not py_files:
        return {"suite": "code_health", "mean_score": 0, "findings": ["No Python files found"]}

    total_funcs = 0
    long_funcs = 0
    total_lines = 0
    comment_lines = 0
    print_lines = 0
    logging_lines = 0
    except_blocks = 0
    security_issues = []

    for fpath in py_files:
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            lines = content.split("\n")
            total_lines += len(lines)

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#"):
                    comment_lines += 1
                if re.search(r'\bprint\s*\(', stripped):
                    print_lines += 1
                if re.search(r'\blogger\.(info|debug|warning|error|exception)', stripped):
                    logging_lines += 1

            tree = ast.parse(content, filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    total_funcs += 1
                    if hasattr(node, 'end_lineno') and hasattr(node, 'lineno'):
                        flen = node.end_lineno - node.lineno
                        if flen > 50:
                            long_funcs += 1
                        if flen > 200:
                            rel = fpath.replace(AGENT_DIR, "")
                            findings.append("Long function (" + str(flen) + " lines): " + node.name + " in " + rel)

                if isinstance(node, ast.Try):
                    for handler in node.handlers:
                        except_blocks += 1

        except SyntaxError:
            pass
        except Exception:
            pass

    error_coverage = min(1.0, except_blocks / max(1, total_funcs * 0.5))
    scores.append(error_coverage)

    total_output = print_lines + logging_lines
    logging_maturity = (logging_lines / total_output) if total_output > 0 else 1.0
    scores.append(logging_maturity)

    long_ratio = 1.0 - min(1.0, long_funcs / max(1, total_funcs * 0.3)) if total_funcs > 0 else 1.0
    scores.append(long_ratio)

    security_score = max(0, 1.0 - len(security_issues) * 0.1)
    scores.append(security_score)

    if total_lines > 0:
        comment_ratio = comment_lines / total_lines
        comment_score = max(0, min(1, 1.0 - abs(comment_ratio - 0.15) * 2))
    else:
        comment_score = 0
    scores.append(comment_score)

    mean_score = round(sum(scores) / len(scores), 4) if scores else 0

    return {
        "suite": "code_health",
        "mean_score": mean_score,
        "details": {
            "total_files": len(py_files),
            "total_lines": total_lines,
            "total_functions": total_funcs,
            "long_functions_50plus": long_funcs,
            "error_coverage": round(error_coverage, 4),
            "logging_maturity": round(logging_maturity, 4),
            "print_count": print_lines,
            "logging_count": logging_lines,
            "security_issue_count": len(security_issues),
            "comment_ratio": round(comment_lines / max(1, total_lines), 4),
        },
        "findings": findings[:20],
    }


def suite_skill_quality():
    if not os.path.exists(SKILLS_DIR):
        return {"suite": "skill_quality", "mean_score": 0, "findings": ["No skills directory"]}

    total_skill_files = 0
    compile_failures = 0
    oversized = 0
    undersized = 0
    skill_sizes = []
    findings = []

    for root, dirs, files in os.walk(SKILLS_DIR):
        for f in files:
            if f == "SKILL.md":
                total_skill_files += 1
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()

                    size = len(content)
                    skill_sizes.append(size)

                    if content.startswith("---"):
                        end = content.find("---", 3)
                        if end == -1 or "---" not in content[end+3:]:
                            compile_failures += 1
                            findings.append("Malformed frontmatter: " + fpath.replace(SKILLS_DIR, ""))

                    if size > 8000:
                        oversized += 1
                        findings.append("Oversized skill (" + str(size) + " chars): " + fpath.replace(SKILLS_DIR, ""))
                    if size < 500:
                        undersized += 1

                except Exception:
                    compile_failures += 1

    scores = []
    compile_rate = 1.0 - (compile_failures / total_skill_files) if total_skill_files > 0 else 1.0
    scores.append(compile_rate)

    if skill_sizes:
        well_sized = sum(1 for s in skill_sizes if 500 <= s <= 5000)
        size_score = well_sized / len(skill_sizes)
    else:
        size_score = 1.0
    scores.append(size_score)

    mean_score = round(sum(scores) / len(scores), 4) if scores else 0

    return {
        "suite": "skill_quality",
        "mean_score": mean_score,
        "details": {
            "total_skills": total_skill_files,
            "compile_failures": compile_failures,
            "oversized_8000plus": oversized,
            "undersized_500minus": undersized,
            "avg_size": round(sum(skill_sizes) / len(skill_sizes)) if skill_sizes else 0,
            "max_size": max(skill_sizes) if skill_sizes else 0,
        },
        "findings": findings[:20],
    }


def suite_tool_coverage():
    tools_dir = os.path.join(AGENT_DIR, "tools")
    if not os.path.exists(tools_dir):
        return {"suite": "tool_coverage", "mean_score": 0, "findings": ["No tools directory"]}

    tool_files = [
        f for f in os.listdir(tools_dir)
        if f.endswith(".py") and f != "__init__.py"
    ]
    tools_with_handlers = 0
    tools_with_error_handling = 0
    tools_with_timeout = 0
    tools_with_schema = 0
    findings = []

    for tf in tool_files:
        fpath = os.path.join(tools_dir, tf)
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()

            has_handler = "registry.register" in content
            has_error_handling = "try:" in content or "except" in content
            has_timeout = "timeout" in content.lower()
            has_schema = "schema" in content.lower() or "description" in content.lower()

            if has_handler:
                tools_with_handlers += 1
                if has_error_handling:
                    tools_with_error_handling += 1
                if has_timeout:
                    tools_with_timeout += 1
                if has_schema:
                    tools_with_schema += 1
            else:
                findings.append("No registry.register in " + tf)

        except Exception:
            pass

    scores = []
    reg_ratio = tools_with_handlers / len(tool_files) if tool_files else 1.0
    scores.append(reg_ratio)

    error_ratio = tools_with_error_handling / tools_with_handlers if tools_with_handlers > 0 else 1.0
    scores.append(error_ratio)

    timeout_ratio = tools_with_timeout / tools_with_handlers if tools_with_handlers > 0 else 1.0
    scores.append(timeout_ratio)

    schema_ratio = tools_with_schema / tools_with_handlers if tools_with_handlers > 0 else 1.0
    scores.append(schema_ratio)

    mean_score = round(sum(scores) / len(scores), 4) if scores else 0

    return {
        "suite": "tool_coverage",
        "mean_score": mean_score,
        "details": {
            "total_tool_files": len(tool_files),
            "tools_with_handlers": tools_with_handlers,
            "tools_with_error_handling": tools_with_error_handling,
            "tools_with_timeout": tools_with_timeout,
            "tools_with_schema": tools_with_schema,
        },
        "findings": findings,
    }


def suite_codebase_integrity():
    py_files = find_py(AGENT_DIR)
    syntax_errors = 0
    findings = []

    for fpath in py_files:
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                ast.parse(fh.read(), filename=fpath)
        except SyntaxError as e:
            syntax_errors += 1
            rel = fpath.replace(AGENT_DIR, "")
            findings.append("Syntax error in " + rel + ": " + str(e.msg))

    untracked = 0
    modified = 0
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=AGENT_DIR,
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n"):
            if line.startswith("??"):
                untracked += 1
            elif line.startswith(" M"):
                modified += 1
    except Exception:
        pass

    scores = []
    syntax_score = 1.0 - (syntax_errors / len(py_files)) if py_files else 1.0
    scores.append(syntax_score)

    git_score = max(0, 1.0 - (untracked * 0.05) - (modified * 0.02))
    scores.append(git_score)

    mean_score = round(sum(scores) / len(scores), 4) if scores else 0

    return {
        "suite": "codebase_integrity",
        "mean_score": mean_score,
        "details": {
            "total_files": len(py_files),
            "syntax_errors": syntax_errors,
            "untracked_files": untracked,
            "modified_files": modified,
        },
        "findings": findings,
    }


def call_openrouter(system, prompt, timeout=30):
    """Make an OpenRouter API call. Returns parsed JSON or None."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    try:
        import urllib.request
        payload = {
            "model": "qwen/qwen3.6-plus:free",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=data,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://hermes-agent.local",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("  API error: " + str(e))
        return None


def suite_llm_judge_sample():
    results = []
    total = 0

    test_cases = [
        {
            "description": "Error handling in terminal tool",
            "prompt": (
                "Rate this code 0.0-1.0 for error handling quality. "
                "1.0 = excellent, 0.0 = broken. Respond ONLY as JSON: "
                '{"score": 0.0-1.0, "reasoning": "brief"}\n\n'
                'def terminal(command, timeout=180):\n'
                '    try:\n'
                '        result = subprocess.run(command, shell=True, capture_output=True,\n'
                '                              text=True, timeout=timeout)\n'
                '        return {"output": result.stdout, "exit_code": result.returncode}\n'
                '    except subprocess.TimeoutExpired:\n'
                '        return {"error": "Timeout", "exit_code": -1}\n'
                '    except Exception as e:\n'
                '        logger.error(f"Terminal command failed: {e}")\n'
                '        return {"error": str(e), "exit_code": -2}'
            ),
        },
        {
            "description": "Tool registration pattern",
            "prompt": (
                "Rate this pattern 0.0-1.0 for completeness. "
                "1.0 = excellent, 0.0 = broken. Respond ONLY as JSON: "
                '{"score": 0.0-1.0, "reasoning": "brief"}\n\n'
                'registry.register(\n'
                '    name="diagnose", toolset="debugging",\n'
                '    schema={"name": "diagnose", "description": "...", "parameters": {...}},\n'
                '    handler=lambda args, **kw: diagnose(args), check_fn=lambda: True,\n'
                ')'
            ),
        },
    ]

    for tc in test_cases:
        start = time.time()
        system = (
            "You are a senior Python reviewer. Score 0.0-1.0. "
            "Respond ONLY as JSON: {\"score\": 0.0-1.0, \"reasoning\": \"brief\"}"
        )
        content = call_openrouter(system, tc["prompt"])
        if content:
            try:
                parsed = json.loads(content)
                score = float(parsed.get("score", 0))
                results.append({"description": tc["description"], "score": score, "reasoning": parsed.get("reasoning", "")})
            except (json.JSONDecodeError, ValueError):
                results.append({"description": tc["description"], "score": -1, "error": "bad JSON: " + content[:100]})
        else:
            results.append({"description": tc["description"], "score": -1, "error": "API failed"})
        total += time.time() - start

    scored = [r["score"] for r in results if r["score"] >= 0]
    mean_score = round(sum(scored) / len(scored), 4) if scored else -1

    return {
        "suite": "llm_judge",
        "mean_score": mean_score,
        "details": {
            "cases_run": len(results),
            "cases_scored": len(scored),
            "total_time": round(total, 2),
        },
        "results": results,
    }


def suite_penta_check():
    """
    Analyze the local clone of pentagi (vxcontrol/pentagi) if available,
    extracting learnings about pentesting/automation patterns.
    """
    repo_dir = os.path.join(HERMES_HOME, "repos", "pentagi")
    if not os.path.exists(repo_dir):
        return {
            "suite": "pentagi_analysis",
            "mean_score": 0,
            "details": {"status": "repo_not_cloned", "path": repo_dir},
            "findings": ["Clone pentagi to analyze. Run: git clone https://github.com/vxcontrol/pentagi.git " + repo_dir],
        }

    try:
        # Walk the repo and analyze structure
        py_files = find_py(repo_dir)
        total_lines = 0
        total_py = 0
        top_modules = []
        tool_names = []

        for fpath in py_files:
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                lines = content.strip().split("\n")
                total_lines += len(lines)
                total_py += 1

                rel = fpath.replace(repo_dir, "")
                if len(lines) > 50:
                    top_modules.append((rel, len(lines)))

                # Look for tool/agent patterns
                if "pentest" in content.lower() or "exploit" in content.lower() or "scan" in content.lower():
                    tool_names.append(rel)

            except Exception:
                pass

        # Find key patterns
        has_docker = os.path.exists(os.path.join(repo_dir, "Dockerfile"))
        has_tests = any("test" in f.lower() for f in os.listdir(repo_dir))
        has_docs = os.path.exists(os.path.join(repo_dir, "README.md"))
        has_config = any("config" in f.lower() or "yaml" in f.lower() or "toml" in f.lower() for f in os.listdir(repo_dir)) if os.path.exists(repo_dir) else False

        top_modules.sort(key=lambda x: x[1], reverse=True)

        score = 0.5  # Baseline for having the repo
        if has_docker:
            score += 0.1
        if has_tests:
            score += 0.1
        if has_docs:
            score += 0.1
        if has_config:
            score += 0.1
        if total_py > 5:
            score += 0.1
        score = min(1.0, score)

        return {
            "suite": "pentagi_analysis",
            "mean_score": round(score, 4),
            "details": {
                "total_python_files": total_py,
                "total_lines": total_lines,
                "top_modules": [(m, s) for m, s in top_modules[:10]],
                "pentest_tools_found": tool_names[:20],
                "has_docker": has_docker,
                "has_tests": has_tests,
                "has_docs": has_docs,
                "has_config": has_config,
            },
            "findings": [
                "PentaGi repo cloned and analyzed -- " + str(total_py) + " Python files, " + str(total_lines) + " lines",
                "Top modules: " + ", ".join(m for m, _ in top_modules[:5]) if top_modules else "No large modules",
            ],
        }
    except Exception as e:
        return {
            "suite": "pentagi_analysis",
            "mean_score": 0,
            "details": {"status": "analysis_failed"},
            "findings": ["Analysis error: " + str(e)],
        }


def main():
    print("=" * 60)
    print("Benchmark-Driven Evolution Evaluation v2")
    print("Timestamp: " + datetime.now().isoformat())
    print("=" * 60)

    cycle = 0
    score = 0
    history_path = os.path.join(BENCHMARK_DIR, "evolution_history.jsonl")
    if os.path.exists(history_path):
        with open(history_path) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        if lines:
            v2_lines = [l for l in lines if "code_health" in l or "skill_quality" in l or "tool_coverage" in l]
            if v2_lines:
                last = json.loads(v2_lines[-1])
                score = last.get("overall_mean", 0)
                cycle = len(v2_lines)
            else:
                cycle = 0
                score = 0

    cycle += 1
    print("Cycle: " + str(cycle) + " (v2)")
    print()

    all_suites = []
    overall_scores = []

    for suite_fn in [suite_code_health, suite_skill_quality, suite_tool_coverage, suite_codebase_integrity]:
        name = suite_fn.__name__.replace("suite_", "")
        print("Running suite: " + name + "...")
        result = suite_fn()
        all_suites.append(result)
        overall_scores.append(result["mean_score"])
        print("  Score: " + str(result["mean_score"]))
        if result.get("findings"):
            for finding in result["findings"][:3]:
                print("  - " + finding)
        print()

    print("Running suite: pentagi_analysis...")
    pentagi_result = suite_penta_check()
    all_suites.append(pentagi_result)
    overall_scores.append(pentagi_result["mean_score"])
    print("  Score: " + str(pentagi_result["mean_score"]))
    if pentagi_result.get("findings"):
        for finding in pentagi_result["findings"][:3]:
            print("  - " + finding)
    print()

    print("Running suite: llm_judge (API)...")
    judge_result = suite_llm_judge_sample()
    all_suites.append(judge_result)
    if judge_result["mean_score"] >= 0:
        overall_scores.append(judge_result["mean_score"])
        print("  Score: " + str(judge_result["mean_score"]))
    else:
        print("  Skipped (no API key or error)")
    print()

    overall_mean = round(sum(overall_scores) / len(overall_scores), 4) if overall_scores else 0

    all_results = {
        "cycle": cycle,
        "version": "v2",
        "model": "qwen/qwen3.6-plus:free",
        "timestamp": datetime.now().isoformat(),
        "overall_mean": overall_mean,
        "suites": all_suites,
    }

    save_results(all_results)

    print("=" * 60)
    print("Cycle " + str(cycle) + " Complete (v2)")
    print("Overall benchmark mean: " + str(overall_mean))
    print("Previous score: " + str(score))
    if overall_mean > score and score > 0:
        print("Change: +" + str(round(overall_mean - score, 4)) + " UP")
    elif score > 0:
        print("Change: " + str(round(overall_mean - score, 4)))
    print("\nPer-suite scores:")
    for s in all_suites:
        marker = " [skipped]" if s.get("mean_score", -1) < 0 else ""
        print("  " + s["suite"] + ": " + str(s.get("mean_score", "N/A")) + marker)
    print("=" * 60)


if __name__ == "__main__":
    main()
