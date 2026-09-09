# -*- coding: utf-8 -*-
"""Unit tests for Quality & Cost Gates with verifiable G1–G8 checks."""
import unittest
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from quality_cost_gate import QualityCostGate

class TestQualityCostGate(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_gate.sqlite"
        db.init_db(self.db_path)
        self.gate = QualityCostGate(
            min_confidence=0.85,
            budget_cap_meeting_usd=0.50,
            daily_budget_cap_usd=5.00,
            max_tasks_per_meeting=15
        )
        
    def tearDown(self):
        self.tmp_dir.cleanup()
        
    def test_approve_grounded_task(self):
        transcript = "Tan, please finalize the Carousel 5 deliverables by Friday."
        task = {
            "task_id": "tsk_001",
            "title": "Finalize Carousel 5 deliverables",
            "confidence_score": 0.92,
            "context_quote": "Tan, please finalize the Carousel 5 deliverables by Friday.",
            "due_date": "2026-09-04",
            "due_date_source": "spoken_relative",
            "is_commitment": True
        }
        passed, reason = self.gate.evaluate_task(task, full_transcript_text=transcript)
        self.assertTrue(passed)
        self.assertEqual(reason, "Passed Quality Gate")
        
    def test_block_low_confidence_task(self):
        task = {
            "task_id": "tsk_002",
            "title": "Maybe check some random files",
            "confidence_score": 0.70,
            "context_quote": "Someone might want to check it.",
            "is_commitment": True
        }
        passed, reason = self.gate.evaluate_task(task)
        self.assertFalse(passed)
        self.assertIn("CONFIDENCE_TOO_LOW", reason)
        
    def test_g2_anti_invention_quote_check(self):
        """Prove G2: Fabricated quote not in transcript is blocked."""
        transcript = "We discussed the budget and Q3 marketing results."
        task = {
            "task_id": "tsk_invented",
            "title": "Deploy new database cluster",
            "confidence_score": 0.90,
            "context_quote": "Tan to deploy new database cluster immediately.",
            "is_commitment": True
        }
        passed, reason = self.gate.evaluate_task(task, full_transcript_text=transcript)
        self.assertFalse(passed)
        self.assertIn("G2_FABRICATED_QUOTE", reason)

    def test_g4_fabricated_due_date_blocked(self):
        """Prove G4: Due date assigned with absent source is blocked."""
        task = {
            "task_id": "tsk_fake_date",
            "title": "Review sales report document",
            "confidence_score": 0.90,
            "context_quote": "Review sales report document",
            "due_date": "2026-09-04",
            "due_date_source": "absent",  # Source says absent, but date was filled!
            "is_commitment": True
        }
        passed, reason = self.gate.evaluate_task(task)
        self.assertFalse(passed)
        self.assertIn("G4_FABRICATED_DATE", reason)
        
    def test_block_meeting_cost_overrun(self):
        tasks = [
            {
                "task_id": "tsk_003",
                "title": "Valid action item title",
                "confidence_score": 0.95,
                "context_quote": "Valid context quote.",
                "is_commitment": True
            }
        ]
        # Current spend ($0.65) > Meeting Budget Cap ($0.50)
        res = self.gate.evaluate_and_route(tasks, meeting_id="meet_cost_test", current_cost_usd=0.65, db_path=self.db_path)
        self.assertTrue(res["cost_overrun"])
        self.assertEqual(len(res["approved_tasks"]), 0)
        self.assertEqual(len(res["blocked_tasks"]), 1)
        
        # Verify DLQ entry created
        dlq_items = db.get_dlq_items(db_path=self.db_path)
        self.assertEqual(len(dlq_items), 1)
        self.assertIn("exceeded meeting cap", dlq_items[0]["failure_reason"])

    def test_g8_flood_cap_parks_meeting(self):
        """Prove G8: > 15 tasks halts and parks entire meeting."""
        flood_tasks = [
            {
                "task_id": f"tsk_flood_{i}",
                "title": f"Action item number {i} for review",
                "confidence_score": 0.90,
                "context_quote": "Valid quote",
                "is_commitment": True
            }
            for i in range(16)
        ]
        res = self.gate.evaluate_and_route(flood_tasks, meeting_id="meet_flood", db_path=self.db_path)
        self.assertTrue(res["flood_cap_exceeded"])
        self.assertEqual(len(res["approved_tasks"]), 0)
        self.assertEqual(len(res["blocked_tasks"]), 16)
        dlq_items = db.get_dlq_items(db_path=self.db_path)
        self.assertTrue(any("G8_FLOOD_CAP_EXCEEDED" in item["failure_reason"] for item in dlq_items))

if __name__ == "__main__":
    unittest.main()
