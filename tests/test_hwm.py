#!/usr/bin/env python3
"""Tests for Hierarchical Workflow Memory (HWM) system."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Add hermes-agent to path for testing
import sys
HERMES_AGENT = Path(__file__).parent.parent
if str(HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT))

from hermes_cli.hierarchical_memory import HWMStore, MemoryTier
from hermes_cli.phase_detector import PhaseDetector
from hermes_cli.memory_consolidation import (
    MemoryConsolidator,
    jaccard_similarity,
    cosine_similarity_simple,
    combined_similarity,
)


class TestHWMStore(unittest.TestCase):
    """Test the hierarchical workflow memory store."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.hwm = HWMStore(db_path=self.db_path)

    def tearDown(self):
        if self.hwm:
            self.hwm.close()
        self.db_path.unlink(missing_ok=True)

    def test_workflow_lifecycle(self):
        wid = self.hwm.start_workflow("test-workflow", "Test goal", "test")
        self.assertIsNotNone(wid)
        self.assertTrue(wid.startswith("wf_"))

        stats = self.hwm.stats()
        self.assertEqual(stats["workflow_count"], 1)
        self.assertEqual(stats["active_workflows"], 1)

        self.hwm.end_workflow(wid)
        stats = self.hwm.stats()
        self.assertEqual(stats["active_workflows"], 0)

    def test_phase_lifecycle(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid = self.hwm.start_phase("setup", "Initial setup", workflow_id=wid)
        self.assertIsNotNone(pid)

        active = self.hwm.get_active_phase()
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], pid)

        self.hwm.end_phase(pid, summary="Done setup")
        active = self.hwm.get_active_phase()
        # Should be None since we ended the only phase
        self.assertIsNone(active)

        # Starting a new phase should auto-close the old one
        pid2 = self.hwm.start_phase("execution", "Main work", workflow_id=wid)
        stats = self.hwm.stats()
        self.assertEqual(stats["phase_count"], 2)

    def test_memory_write_and_retrieve(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid = self.hwm.start_phase("phase1", "first", workflow_id=wid)

        mid = self.hwm.write_memory(
            "Important finding about Python GIL",
            tier="phase",
            phase_id=pid,
            workflow_id=wid,
        )
        self.assertTrue(mid.startswith("mem_"))

        results = self.hwm.retrieve("Python GIL", top_k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], mid)
        self.assertIn("Python", results[0]["content"])

    def test_tier_promotion(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid = self.hwm.start_phase("phase1", "first", workflow_id=wid)

        mid = self.hwm.write_memory("Test promotion", tier="phase",
                                    phase_id=pid, workflow_id=wid)
        result = self.hwm.promote_to_workflow(mid)
        self.assertTrue(result)

        results = self.hwm.retrieve("Test promotion", top_k=5)
        self.assertEqual(results[0]["tier"], "workflow")

    def test_phase_dependencies(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid1 = self.hwm.start_phase("setup", "first", workflow_id=wid)
        pid2 = self.hwm.start_phase("deploy", "second", workflow_id=wid,
                                     depends_on=[pid1])

        deps = self.hwm.get_phase_dependencies(pid2)
        self.assertIn(pid1, deps)

    def test_cross_workflow_retrieval(self):
        # Create two workflows
        w1 = self.hwm.start_workflow("devops-task", "Deploy app", "devops")
        p1 = self.hwm.start_phase("setup", "setup", workflow_id=w1)
        self.hwm.write_memory("nginx config tip for deployment", tier="workflow",
                             workflow_id=w1, phase_id=p1)
        self.hwm.end_phase(phase_id=p1, summary="setup done")

        w2 = self.hwm.start_workflow("data-task", "Analyze data", "data")
        p2 = self.hwm.start_phase("explore", "explore", workflow_id=w2)
        self.hwm.write_memory("pandas merge trick for analysis", tier="workflow",
                             workflow_id=w2, phase_id=p2)
        self.hwm.end_phase(phase_id=p2, summary="explore done")

        # Cross-workflow search should find memories from OTHER workflows
        # When we're in w2, we should find w1's content
        results = self.hwm.retrieve_cross_workflow(
            "nginx", exclude_workflow=w2, top_k=5)
        found = [r for r in results if "nginx" in r["content"].lower()]
        self.assertTrue(len(found) > 0, f"Expected nginx in cross-workflow results, got: {results}")

    def test_relevance_feedback(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid = self.hwm.start_phase("p1", "first", workflow_id=wid)
        mid = self.hwm.write_memory("test content", tier="phase",
                                   workflow_id=wid, phase_id=pid)

        self.hwm.update_relevance_score(mid, 0.9)

        # Retrieve and check score was updated
        results = self.hwm.retrieve("test content", top_k=5)
        self.assertTrue(len(results) > 0)
        # Score should have been boosted by update
        self.assertTrue(any(r["id"] == mid for r in results))

    def test_stats(self):
        wid = self.hwm.start_workflow("test", "goal")
        pid = self.hwm.start_phase("p1", "first", workflow_id=wid)
        self.hwm.write_memory("mem1", tier="phase", workflow_id=wid, phase_id=pid)
        self.hwm.write_memory("mem2", tier="global", workflow_id=wid)

        stats = self.hwm.stats()
        self.assertEqual(stats["phase_count"], 1)
        self.assertEqual(stats["workflow_count"], 1)
        self.assertIn("phase_count", stats)


class TestPhaseDetector(unittest.TestCase):
    """Test the automatic phase boundary detector."""

    def setUp(self):
        self.detector = PhaseDetector()

    def test_explicit_phase_start(self):
        msg = "Now let us fix the deployment issue"
        result = self.detector.should_split_phase(msg, current_phase_duration_msgs=5)
        self.assertIsNotNone(result)
        self.assertTrue(result["confidence"] > 0.5)

    def test_continuation_not_split(self):
        msg = "Can you also show me the logs?"
        result = self.detector.should_split_phase(msg, current_phase_duration_msgs=5)
        # Should not split for continuation
        self.assertIsNone(result)

    def test_error_recovery_phase(self):
        msg = "That does not work, I'm getting a 500 error"
        result = self.detector.should_split_phase(msg, current_phase_duration_msgs=5)
        self.assertIsNotNone(result)

    def test_short_phase_not_split(self):
        msg = "Now let's deploy"
        result = self.detector.should_split_phase(msg, current_phase_duration_msgs=1)
        self.assertIsNone(result)  # Phase too young

    def test_batch_detection(self):
        messages = [
            {"role": "user", "content": "Help me set up the database"},
            {"role": "assistant", "content": "Sure, here's how..."},
            {"role": "user", "content": "Great"},
            {"role": "assistant", "content": "Done"},
            {"role": "user", "content": "Can you show me the logs"},
            {"role": "assistant", "content": "Here are the logs..."},
            {"role": "user", "content": "Now let us deploy it to production"},
            {"role": "assistant", "content": "Deploying..."},
            {"role": "user", "content": "That does not work, I am seeing an error"},
        ]

        phases = self.detector.detect_phases(messages)
        self.assertGreater(len(phases), 1)

    def test_extract_phase_name(self):
        name = PhaseDetector._extract_phase_name("can you help me fix the nginx config")
        self.assertIn("nginx", name.lower())

    def test_tool_category_extraction(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "read_file"}}
            ]},
            {"role": "user", "content": "Here's what I found"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "browser_navigate"}}
            ]},
        ]

        cats = self.detector._extract_tool_categories(msgs)
        # Should detect filesystem and browser categories
        self.assertTrue(len(cats) > 0)


class TestMemoryConsolidation(unittest.TestCase):
    """Test memory consolidation engine."""

    def test_jaccard_similarity_identical(self):
        sim = jaccard_similarity("hello world", "hello world")
        self.assertGreater(sim, 0.9)

    def test_jaccard_similarity_different(self):
        sim = jaccard_similarity("hello world", "goodbye universe")
        self.assertLess(sim, 0.3)

    def test_cosine_similarity(self):
        sim = cosine_similarity_simple("the quick brown fox", "the quick brown fox")
        self.assertGreater(sim, 0.9)

    def test_combined_similarity(self):
        sim = combined_similarity(
            "Python GIL prevents true parallelism",
            "Python GIL prevents true parallelism")
        self.assertGreater(sim, 0.95)

        low_sim = combined_similarity(
            "Python GIL prevents true parallelism",
            "deploy to AWS Lambda")
        self.assertLess(low_sim, 0.3)


if __name__ == "__main__":
    unittest.main()
