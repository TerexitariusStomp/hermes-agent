#!/usr/bin/env python3
"""Hive Integration Runner — convenience CLI for all Hive-inspired modules.

Usage:
  python3 ~/.hermes/skills/hivemind-integration/scripts/run.py spillover      # Test spillover system
  python3 ~/.hermes/skills/hivemind-integration/scripts/run.py stall          # Test stall detector
  python3 ~/.hermes/skills/hivemind-integration/scripts/run.py compaction     # Test compaction
  python3 ~/.hermes/skills/hivemind-integration/scripts/run.py checkpoint     # Test checkpoint system
  python3 ~/.hermes/skills/hivemind-integration/scripts/run.py status         # Show all stats
"""

import json
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, str(SCRIPT_DIR))

HERMES_HOME = Path.home() / ".hermes"


def test_spillover():
    """Test the tool result spillover system."""
    from spillover import ToolSpillover

    spillover = ToolSpillover(max_inline_chars=100)  # Low threshold for testing

    # Test: small result stays inline
    small = "short result"
    result = spillover.maybe_spill(small, "echo")
    print(f"Small result (12 chars): inline={result == small}")

    # Test: large result spills
    large = "X" * 500
    result = spillover.maybe_spill(large, "read_file")
    is_spilled = isinstance(result, str) and "SPILLED TO DISK" in result
    print(f"Large result (500 chars): spilled={is_spilled}")

    # Test: error result never spills
    error_content = "ERROR: something failed\nTraceback: line 42"
    large_error = error_content + ("\nMore error detail " * 50)
    result = spillover.maybe_spill(large_error, "execute_code")
    is_preserved = "ERROR:" in result
    print(f"Error result (never spilled): preserved={is_preserved}")

    # Test: excluded tool never spills
    large = "Y" * 500
    result = spillover.maybe_spill(large, "load_data", exclude_tools={"load_data"})
    is_inline = result == large
    print(f"Excluded tool (load_data): inline={is_inline}")

    # Stats
    stats = spillover.create_spillover_stats()
    print(f"\nSpillover stats:")
    print(f"  Spills this session: {stats['total_spills_this_session']}")
    print(f"  Total bytes spilled: {stats['total_bytes_spilled']}")
    print(f"  Spillover files: {stats['current_spillover_files']}")
    print(f"  Spillover dir: {stats['spillover_dir']}")
    print("[OK] Spillover system")


def test_stall_detector():
    """Test the stall detection system."""
    from stall_detector import StallDetector

    detector = StallDetector(fingerprint_window=5, sequence_window=8, max_iterations=50)

    # Test 1: Normal execution
    for i in range(5):
        detector.record_tool_call("different_tool", {"arg": i})

    status = detector.check_stall()
    print(f"Normal execution: stalled={status['stalled']}")

    # Test 2: Exact repetition
    detector2 = StallDetector(fingerprint_window=5)
    for _ in range(5):
        detector2.record_tool_call("read_file", {"path": "same_file.txt"})

    status = detector2.check_stall()
    print(f"Exact repetition: stalled={status['stalled']}, type={status['stall_type']}")

    # Test 3: Single tool loop
    detector3 = StallDetector(fingerprint_window=10)
    for i in range(7):
        detector3.record_tool_call("execute_code", {"code": f"print({i})"})

    status = detector3.check_stall()
    print(f"Single tool loop: stalled={status['stalled']}, type={status['stall_type']}")

    # Test 4: Get escape prompt
    prompt = detector2.get_escape_prompt()
    has_instruction = "SYSTEM:" in prompt
    print(f"Escape prompt available: {has_instruction}")
    print(f"Escape prompt: {prompt[:80]}...")

    print("[OK] Stall detector")


def test_compaction():
    """Test the compaction system."""
    from compaction import microcompact, prune_by_token_budget, spill_contents, estimate_token_count

    # Test token estimation
    tokens = estimate_token_count("Hello world, this is a test message!")
    print(f"Token estimate for 36 chars: ~{tokens} (expected ~9)")

    # Test microcompact
    messages = [{"role": "tool", "content": f"result {i}"} for i in range(20)]
    compacted = microcompact(messages, keep_last=5)
    print(f"Microcompact: {len(messages)} -> {len(compacted)} (kept last 5)")

    # Test token budget pruning
    messages = [{"role": "user", "content": f"message {i} with some extra content to add tokens"} for i in range(50)]
    pruned = prune_by_token_budget(messages, max_tokens=500, keep_first=2, keep_last=3)
    has_summary = any("COMPACTED" in m.get('content', '') for m in pruned)
    print(f"Token prune: {len(messages)} -> {len(pruned)} (has summary={has_summary})")

    # Test content spillover
    messages = [
        {"role": "tool", "content": "X" * 5000, "tool_name": "read_file"},
        {"role": "tool", "content": "short result", "tool_name": "echo"},
    ]
    new_msgs, spilled = spill_contents(messages, max_field_chars=4000)
    print(f"Content spillover: {len(spilled)} files spilled")

    print("[OK] Compaction")


def test_checkpoint():
    """Test the checkpoint system."""
    from checkpoint import CheckpointManager

    cm = CheckpointManager()

    # Save a checkpoint
    conv = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    path = cm.save_checkpoint(
        session_id="test_session",
        conversation=conv,
        iteration=5,
        tool_calls=[{"tool": "echo", "args": {"msg": "hello"}}],
        metadata={"model": "test"},
    )
    print(f"Checkpoint saved: {path}")

    # Load it back
    loaded = cm.load_latest_checkpoint("test_session")
    if loaded:
        print(f"Checkpoint loaded: iteration={loaded['iteration']}, conv_len={len(loaded['conversation'])}")

    # List checkpoints
    checkpoints = cm.list_checkpoints("test_session")
    print(f"Available checkpoints: {len(checkpoints)}")

    print("[OK] Checkpoint system")


def show_status():
    """Show status of all Hive integration components."""
    from spillover import ToolSpillover
    from checkpoint import CheckpointManager

    print("=== Hive Integration Status ===\n")

    # Spillover
    sp = ToolSpillover()
    stats = sp.create_spillover_stats()
    print(f"Spillover:")
    print(f"  Max inline chars: {stats['max_inline_chars']}")
    print(f"  Spill dir: {stats['spillover_dir']}")
    print(f"  Files: {stats['current_spillover_files']}")
    print(f"  Total spilled: {stats['total_spillover_size_bytes']:,} bytes")

    # Checkpoints
    cm = CheckpointManager()
    cps = cm.list_checkpoints()
    print(f"\nCheckpoints:")
    print(f"  Dir: {cm.checkpoint_dir}")
    print(f"  Available: {len(cps)}")

    # Skills
    skill_dir = SCRIPT_DIR.parent
    print(f"\nSkill files:")
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        size = f.stat().st_size
        print(f"  {f.name} ({size:,} bytes)")

    print(f"\n[OK] All components ready")


COMMANDS = {
    "spillover": test_spillover,
    "stall": test_stall_detector,
    "compaction": test_compaction,
    "checkpoint": test_checkpoint,
    "status": show_status,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python3 {sys.argv[0]} <command>")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
