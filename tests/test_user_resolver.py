# -*- coding: utf-8 -*-
"""Unit tests for 3-Tier Entity Resolution & Monday User Matching."""
import unittest
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import user_resolver

class TestUserResolver(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_harness.sqlite"
        db.init_db(self.db_path)
        
        # Seed test users
        db.upsert_user(103551084, "Mike Wong", "mikewong@striking.com.hk", ["mike", "boss"], db_path=self.db_path)
        db.upsert_user(113704803, "Leah", "yhkung@striking.com.hk", ["leah", "kung"], db_path=self.db_path)
        db.upsert_user(113703761, "Duong Tan", "tan.dh@poppingcandy.com.hk", ["tan", "dương tấn"], db_path=self.db_path)
        db.upsert_user(103982655, "Wayne", "waynechan@striking.com.hk", ["wayne"], db_path=self.db_path)
        # Add Chinese name user
        db.upsert_user(999999, "陳大文", "chan@striking.com.hk", ["tai man", "taiman"], db_path=self.db_path)
        
    def tearDown(self):
        self.tmp_dir.cleanup()
        
    def test_tier1_exact_email(self):
        res = user_resolver.resolve_assignee("", email="mikewong@striking.com.hk", db_path=self.db_path)
        self.assertEqual(res["resolved_user_id"], 103551084)
        self.assertEqual(res["tier"], "TIER_1_EMAIL")
        self.assertEqual(res["confidence"], 1.0)
        
    def test_tier2_nickname_and_alias(self):
        # Spoken as 'Tan'
        res_tan = user_resolver.resolve_assignee("Tan", db_path=self.db_path)
        self.assertEqual(res_tan["resolved_user_id"], 113703761)
        self.assertEqual(res_tan["resolved_name"], "Duong Tan")
        self.assertGreaterEqual(res_tan["confidence"], 0.85)
        
        # Spoken as 'Leah'
        res_leah = user_resolver.resolve_assignee("Leah", db_path=self.db_path)
        self.assertEqual(res_leah["resolved_user_id"], 113704803)
        self.assertGreaterEqual(res_leah["confidence"], 0.85)
        
        # Spoken as 'Mike Wong'
        res_mike = user_resolver.resolve_assignee("Mike Wong", db_path=self.db_path)
        self.assertEqual(res_mike["resolved_user_id"], 103551084)
        
    def test_tier2_vietnamese_accents(self):
        # Spoken with accents 'Dương Tấn'
        res = user_resolver.resolve_assignee("Dương Tấn", db_path=self.db_path)
        self.assertEqual(res["resolved_user_id"], 113703761)
        self.assertGreaterEqual(res["confidence"], 0.85)

    def test_cjk_character_preservation_and_anti_catchall(self):
        """Prove F4 fix: Chinese names normalize properly and do NOT become empty-string catchalls."""
        norm = user_resolver.normalize_text("陳大文")
        self.assertEqual(norm, "陳大文")
        self.assertNotEqual(norm, "")
        
        # 'Kevin' must NOT resolve to CJK user 999999
        res_kevin = user_resolver.resolve_assignee("Kevin", db_path=self.db_path)
        self.assertIsNone(res_kevin["resolved_user_id"])
        self.assertEqual(res_kevin["tier"], "TIER_3_FALLBACK")
        
        # 'the marketing team' must NOT resolve to CJK user 999999
        res_team = user_resolver.resolve_assignee("the marketing team", db_path=self.db_path)
        self.assertIsNone(res_team["resolved_user_id"])
        self.assertEqual(res_team["tier"], "TIER_3_FALLBACK")
        
        # Exact CJK match works
        res_cjk = user_resolver.resolve_assignee("陳大文", db_path=self.db_path)
        self.assertEqual(res_cjk["resolved_user_id"], 999999)

    def test_generic_stop_words_route_to_tier3(self):
        for word in ["the team", "everyone", "ops", "someone", "we", "mọi người"]:
            res = user_resolver.resolve_assignee(word, db_path=self.db_path)
            self.assertIsNone(res["resolved_user_id"])
            self.assertEqual(res["tier"], "TIER_3_FALLBACK")
        
    def test_tier3_unknown_fallback(self):
        res = user_resolver.resolve_assignee("Unknown Contributor XYZ", db_path=self.db_path)
        self.assertIsNone(res["resolved_user_id"])
        self.assertEqual(res["tier"], "TIER_3_FALLBACK")

if __name__ == "__main__":
    unittest.main()
