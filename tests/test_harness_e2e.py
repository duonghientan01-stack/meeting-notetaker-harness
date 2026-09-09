# -*- coding: utf-8 -*-
"""End-to-End Pipeline Integration Test for Meeting Intelligence Harness."""
import unittest
import tempfile
import hmac
import hashlib
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import config
from app import (
    process_meeting_pipeline,
    verify_webhook_signature,
    approve_all_digest,
    decide_approval_digest,
    ApprovalDecisionRequest
)
from fastapi import HTTPException

class TestHarnessE2E(unittest.TestCase):
    def setUp(self):
        self.orig_db_path = config.SQLITE_DB_PATH
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_e2e.sqlite"
        config.SQLITE_DB_PATH = self.db_path
        db.init_db(self.db_path)
        
        # Seed users
        db.upsert_user(103551084, "Mike Wong", "mikewong@striking.com.hk", ["mike", "boss"], db_path=self.db_path)
        db.upsert_user(113704803, "Leah", "yhkung@striking.com.hk", ["leah", "kung"], db_path=self.db_path)
        db.upsert_user(113703761, "Duong Tan", "tan.dh@poppingcandy.com.hk", ["tan", "dương tấn"], db_path=self.db_path)

    def tearDown(self):
        # F8: Restore original DB path to eliminate test order-dependence
        config.SQLITE_DB_PATH = self.orig_db_path
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def get_sample_payload(self):
        return {
            "meeting_id": "hs_meet_real_001",
            "title": "Striking Digital EU — Weekly Executive Alignment",
            "platform": "Microsoft Teams (Happy Scribe AI Bot)",
            "started_at": "2026-09-01T09:00:00+08:00",
            "ended_at": "2026-09-01T09:30:00+08:00",
            "recording_url": "https://app.happyscribe.com/recordings/hs_meet_real_001",
            "participants": [
                {"name": "Mike Wong", "email": "mikewong@striking.com.hk"},
                {"name": "Leah", "email": "yhkung@striking.com.hk"},
                {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"}
            ],
            "transcript": [
                {
                    "speaker": "Mike Wong",
                    "timestamp": "00:01:45",
                    "text": "Tan, please package all Carousel 5 and 6 deliverables by Friday."
                },
                {
                    "speaker": "Duong Tan",
                    "timestamp": "00:02:10",
                    "text": "I will package the carousels and verify cloud proof links today."
                },
                {
                    "speaker": "Leah",
                    "timestamp": "00:04:30",
                    "text": "I will review and confirm the Happy Scribe bot invite on the Teams calendar tomorrow."
                }
            ]
        }

    def test_full_pipeline_dry_run(self):
        meeting_payload = self.get_sample_payload()
        res = process_meeting_pipeline(meeting_payload, dry_run=True, require_approval=False)
        
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreaterEqual(res["approved_count"], 2)
        self.assertEqual(res["blocked_count"], 0)
        self.assertEqual(len(res["sync_results"]), res["approved_count"])
        
        first_sync = res["sync_results"][0]
        self.assertTrue(first_sync["success"])
        self.assertIn("dry_run_item", first_sync["monday_item_id"])

    def test_assisted_mode_human_approval_workflow(self):
        """Prove B10 & §6.3: Pipeline produces approval digest, requires approval before Monday write."""
        meeting_payload = self.get_sample_payload()
        res = process_meeting_pipeline(meeting_payload, dry_run=False, require_approval=True)
        
        self.assertEqual(res["status"], "AWAITING_APPROVAL")
        token = res["approval_token"]
        self.assertTrue(token)
        
        # Verify approval record in DB
        appr = db.get_approval_by_token(token, db_path=self.db_path)
        self.assertIsNotNone(appr)
        self.assertEqual(appr["status"], "PENDING")
        self.assertGreaterEqual(len(appr["digest_payload"]["proposed_tasks"]), 2)
        
        # Simulate Human Reviewer approving all tasks (dry_run for Monday)
        review_res = approve_all_digest(token, dry_run=True)
        self.assertEqual(review_res["status"], "APPROVED_ALL")
        self.assertGreaterEqual(review_res["synced_count"], 2)
        
        # Check approval status updated
        appr_after = db.get_approval_by_token(token, db_path=self.db_path)
        self.assertEqual(appr_after["status"], "APPROVED_ALL")

    def test_idempotency_duplicate_skipping(self):
        """Prove F6 / §7.3: Re-delivering identical meeting skips reprocessing."""
        meeting_payload = self.get_sample_payload()
        res1 = process_meeting_pipeline(meeting_payload, dry_run=False, require_approval=True)
        self.assertEqual(res1["status"], "AWAITING_APPROVAL")
        
        # Second delivery of identical payload
        res2 = process_meeting_pipeline(meeting_payload, dry_run=False, require_approval=True)
        self.assertEqual(res2["status"], "IDEMPOTENT_SKIPPED")

    def test_webhook_hmac_authentication(self):
        """Prove B2 / F5: Webhook requires valid HMAC signature and respects replay window."""
        orig_secret = config.WEBHOOK_SECRET
        config.WEBHOOK_SECRET = "super_secure_test_secret_2026"
        try:
            body = b'{"event": "transcription.ready", "meeting_id": "test_hmac"}'
            valid_sig = hmac.new(b"super_secure_test_secret_2026", body, hashlib.sha256).hexdigest()
            now_ts = str(time.time())
            
            # 1. Valid signature passes
            verify_webhook_signature(body, f"sha256={valid_sig}", now_ts)
            
            # 2. Tampered body raises 403
            tampered_body = b'{"event": "transcription.ready", "meeting_id": "hacked"}'
            with self.assertRaises(HTTPException) as ctx:
                verify_webhook_signature(tampered_body, f"sha256={valid_sig}", now_ts)
            self.assertEqual(ctx.exception.status_code, 403)
            
            # 3. Old timestamp (> 5 min) raises 401
            old_ts = str(time.time() - 400)
            with self.assertRaises(HTTPException) as ctx:
                verify_webhook_signature(body, f"sha256={valid_sig}", old_ts)
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            config.WEBHOOK_SECRET = orig_secret

if __name__ == "__main__":
    unittest.main()
