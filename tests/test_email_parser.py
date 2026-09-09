# -*- coding: utf-8 -*-
"""Unit tests for Happy Scribe email format fingerprinting and MeetingEnvelope v1 parsing."""
import unittest
import tempfile
import pathlib
import sys

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_DIR))

import email_parser
import db

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "emails"

class TestEmailParser(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp_dir.name) / "test.sqlite"
        self.archive_dir = pathlib.Path(self.tmp_dir.name) / "archive"
        self.archive_dir.mkdir()
        db.init_db(self.db_path)
        
    def tearDown(self):
        self.tmp_dir.cleanup()
        
    def test_sample_1_standard_mom(self):
        sample_path = FIXTURES_DIR / "sample_1_standard_mom.eml"
        raw_bytes = sample_path.read_bytes()
        env = email_parser.parse_email_to_envelope(
            raw_bytes,
            archive_dir=self.archive_dir,
            db_path=self.db_path
        )
        self.assertEqual(env["envelope_version"], "1.0")
        self.assertEqual(env["source"], "happyscribe_email")
        self.assertIn("Striking EU Digital & Ops Review", env["meeting"]["title"])
        self.assertGreater(len(env["transcript"]), 3)
        self.assertTrue(env["content_hash"])
        # Verify raw MIME archive exists
        archived_files = list(self.archive_dir.glob("*.eml"))
        self.assertEqual(len(archived_files), 1)
        
    def test_sample_2_cteam_review(self):
        sample_path = FIXTURES_DIR / "sample_2_cteam_review.eml"
        raw_bytes = sample_path.read_bytes()
        env = email_parser.parse_email_to_envelope(
            raw_bytes,
            archive_dir=self.archive_dir,
            db_path=self.db_path
        )
        self.assertEqual(env["envelope_version"], "1.0")
        self.assertIn("C-Team Weekly Operations", env["meeting"]["title"])
        self.assertGreater(len(env["transcript"]), 3)
        
    def test_sample_3_bilingual(self):
        sample_path = FIXTURES_DIR / "sample_3_chinese_english_ops.eml"
        raw_bytes = sample_path.read_bytes()
        env = email_parser.parse_email_to_envelope(
            raw_bytes,
            archive_dir=self.archive_dir,
            db_path=self.db_path
        )
        self.assertEqual(env["envelope_version"], "1.0")
        self.assertIn("Striking HK & EU Production Sync", env["meeting"]["title"])
        # Check Chinese utterances preserved
        cjk_found = any("十月份" in u["text"] or "簡報" in u["text"] for u in env["transcript"])
        self.assertTrue(cjk_found, "Chinese utterances should be preserved in transcript")
        
    def test_sample_4_minimal_valid(self):
        sample_path = FIXTURES_DIR / "sample_4_minimal_valid.eml"
        raw_bytes = sample_path.read_bytes()
        env = email_parser.parse_email_to_envelope(
            raw_bytes,
            archive_dir=self.archive_dir,
            db_path=self.db_path
        )
        self.assertEqual(env["envelope_version"], "1.0")
        self.assertEqual(len(env["transcript"]), 2)
        
    def test_sample_5_unrecognized_format_fails_fingerprint(self):
        sample_path = FIXTURES_DIR / "sample_5_unrecognized_format.eml"
        raw_bytes = sample_path.read_bytes()
        with self.assertRaises(email_parser.EmailFingerprintError):
            email_parser.parse_email_to_envelope(
                raw_bytes,
                archive_dir=self.archive_dir,
                db_path=self.db_path
            )
        # Should record failure in DLQ
        dlq = db.get_dlq_items(db_path=self.db_path)
        self.assertEqual(len(dlq), 1)
        self.assertIn("UNKNOWN_EMAIL_FORMAT", dlq[0]["failure_reason"])

if __name__ == "__main__":
    unittest.main()
