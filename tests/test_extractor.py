# -*- coding: utf-8 -*-
"""Unit tests for multilingual action item extraction and anti-fabrication rules."""
import unittest
import tempfile
import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from extractor import extract_action_items, parse_relative_deadline

class TestExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_extract.sqlite"
        db.init_db(self.db_path)
        
        # Seed users
        db.upsert_user(103551084, "Mike Wong", "mikewong@striking.com.hk", ["mike", "boss"], db_path=self.db_path)
        db.upsert_user(113704803, "Leah", "yhkung@striking.com.hk", ["leah", "kung"], db_path=self.db_path)
        db.upsert_user(113703761, "Duong Tan", "tan.dh@poppingcandy.com.hk", ["tan", "dương tấn"], db_path=self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_bilingual_extraction(self):
        sample_transcript = {
            "meeting_id": "meet_test_001",
            "title": "Weekly Striking EU Operations Alignment",
            "platform": "Microsoft Teams",
            "started_at": "2026-09-02T09:00:00+08:00",
            "participants": [
                {"name": "Mike Wong", "email": "mikewong@striking.com.hk"},
                {"name": "Leah", "email": "yhkung@striking.com.hk"},
                {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"}
            ],
            "transcript": [
                {
                    "speaker": "Mike Wong",
                    "timestamp": "00:02:10",
                    "text": "Tan, please package the Carousel 5 and 6 deliverables and verify cloud links."
                },
                {
                    "speaker": "Duong Tan",
                    "timestamp": "00:02:30",
                    "text": "Sure Mike, I will package all rendered slides and verify cloud links today."
                },
                {
                    "speaker": "Leah",
                    "timestamp": "00:05:15",
                    "text": "I will review the teams calendar invites and confirm the Happy Scribe bot whitelist tomorrow."
                },
                {
                    "speaker": "Duong Tan",
                    "timestamp": "00:07:00",
                    "text": "Thời tiết hôm nay ở Sài Gòn rất đẹp." # Casual talk, not a task
                }
            ]
        }
        
        res = extract_action_items(sample_transcript, db_path=self.db_path)
        self.assertGreaterEqual(res.extracted_count, 2)
        
        # Check task 1 (Assigned to Tan)
        tan_tasks = [t for t in res.tasks if t.resolved_user_id == 113703761]
        self.assertTrue(len(tan_tasks) > 0)
        self.assertEqual(tan_tasks[0].resolved_name, "Duong Tan")
        
        # Check task 2 (Assigned to Leah)
        leah_tasks = [t for t in res.tasks if t.resolved_user_id == 113704803]
        self.assertTrue(len(leah_tasks) > 0)
        self.assertEqual(leah_tasks[0].resolved_name, "Leah")

    def test_anti_fabrication_due_date(self):
        """Prove F3 fix: No deadline mentioned returns None, not 'this Friday'."""
        ref_date = datetime.date(2026, 9, 2)  # Wednesday
        date_val, source = parse_relative_deadline("Please review the document when you have time.", ref_date)
        self.assertIsNone(date_val)
        self.assertEqual(source, "absent")

    def test_monday_tool_mention_not_a_deadline(self):
        """Prove F3 fix: 'Monday.com' is not parsed as next Monday."""
        ref_date = datetime.date(2026, 9, 2)  # Wednesday
        date_val, source = parse_relative_deadline("Please put the tasks on Monday.com and update the board.", ref_date)
        self.assertIsNone(date_val)
        self.assertEqual(source, "absent")

    def test_conversational_noise_filtered(self):
        """Prove F1 fix: Microphone muting and holiday talk do not produce tasks."""
        noise_transcript = {
            "meeting_id": "meet_noise",
            "title": "Audio Check",
            "transcript": [
                {"speaker": "Mike", "timestamp": "00:00:10", "text": "Can everyone please mute their microphones, the echo is bad"},
                {"speaker": "TT", "timestamp": "00:00:30", "text": "I will be on holiday next week so I am handing over my tasks"}
            ]
        }
        res = extract_action_items(noise_transcript, db_path=self.db_path)
        self.assertEqual(len(res.tasks), 0)

if __name__ == "__main__":
    unittest.main()
