# -*- coding: utf-8 -*-
"""Verification test suite for Meeting Intelligence Harness v3.2 upgrades."""
import unittest
import json
from pathlib import Path

from email_parser import parse_transcript_lines, parse_email_to_envelope
from llm_extractor import LLMExtractor, LLMExtractionPayload, LLMActionItem, EvidenceQuote, MeetingMeta
from monday_syncer import build_column_values, sync_task_to_monday
import config

class TestV32Upgrades(unittest.TestCase):
    def test_bullet_point_parsing(self):
        """Verify that lines with bullets (-, *, •, 1.) are parsed accurately with speakers."""
        sample_mom = """
Meeting Minutes [MoM]
- Tan: Prepare 30s TVC storyboard for popping candy
* Alexa: Coordinate with creative audio agency
• Emmy: Illustrate character expressions
1. TT: Test Android payment integration in Thailand
"""
        utterances = parse_transcript_lines(sample_mom)
        speakers = [u["speaker"] for u in utterances]
        self.assertIn("Tan", speakers)
        self.assertIn("Alexa", speakers)
        self.assertIn("Emmy", speakers)
        self.assertIn("TT", speakers)

    def test_json_payload_from_gas(self):
        """Verify that parse_email_to_envelope parses JSON payloads from Google Apps Script."""
        gas_payload = {
            "message_id": "msg_gas_test_123",
            "subject": "[MoM] Product & Packaging Alignment",
            "date": "2026-09-18T01:00:00Z",
            "body": "Tan: Finalize candy box dimensions\nAlexa: Review barcode specs",
            "participants": [
                {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"},
                {"name": "Alexa Chan", "email": "alexachan@striking.com.hk"}
            ]
        }
        raw_bytes = json.dumps(gas_payload).encode("utf-8")
        envelope = parse_email_to_envelope(raw_bytes)
        self.assertEqual(envelope["meeting"]["title"], "[MoM] Product & Packaging Alignment")
        self.assertEqual(len(envelope["meeting"]["participants"]), 2)
        self.assertGreaterEqual(len(envelope["transcript"]), 2)

    def test_task_continuity_filter(self):
        """Verify that ONGOING_STATUS_UPDATE items are excluded from action item generation."""
        mock_payload = {
            "meeting_id": "meet_test_continuity",
            "title": "Weekly TVC Sync",
            "started_at": "2026-09-18T00:00:00Z",
            "timezone": "Asia/Hong_Kong",
            "participants": [
                {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"}
            ],
            "transcript": [
                {"speaker": "Tan", "timestamp": "00:01:00", "text": "I am continuing work on the storyboard from last week."},
                {"speaker": "Tan", "timestamp": "00:02:00", "text": "I will deliver the 3D model draft tomorrow."}
            ]
        }

        # Mock caller returning 1 ongoing status update and 1 new commitment
        def mock_llm_caller(prompt, model):
            payload = LLMExtractionPayload(
                meeting_meta=MeetingMeta(
                    meeting_id="meet_test_continuity",
                    title="Weekly TVC Sync",
                    attendees=["Duong Tan"]
                ),
                workstream="TVC & Video Production",
                executive_summary="Status recap and new deliverables.",
                action_items=[
                    LLMActionItem(
                        task_name="Continue storyboard production from last week",
                        spoken_assignee="Duong Tan",
                        evidence=EvidenceQuote(quote="I am continuing work on the storyboard from last week.", speaker="Tan"),
                        continuity_type="ONGOING_STATUS_UPDATE",
                        is_commitment=True
                    ),
                    LLMActionItem(
                        task_name="Deliver 3D model draft",
                        spoken_assignee="Duong Tan",
                        due_date="2026-09-19",
                        due_date_source="spoken_relative",
                        evidence=EvidenceQuote(quote="I will deliver the 3D model draft tomorrow.", speaker="Tan"),
                        continuity_type="NEW_COMMITMENT",
                        is_commitment=True
                    )
                ]
            )
            return payload.model_dump_json(), 200, 100

        extractor = LLMExtractor(custom_caller=mock_llm_caller)
        res, cost = extractor.extract(mock_payload)
        # Only the NEW_COMMITMENT should be extracted as a task!
        self.assertEqual(len(res.tasks), 1)
        self.assertEqual(res.tasks[0].title, "Deliver 3D model draft")

    def test_multi_person_team_assignment(self):
        """Verify that tasks assigned to 'Team' / 'All meeting attendees' resolve all attendees dynamically."""
        mock_payload = {
            "meeting_id": "meet_team_assign",
            "title": "Executive Review",
            "started_at": "2026-09-18T00:00:00Z",
            "timezone": "Asia/Hong_Kong",
            "participants": [
                {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"},
                {"name": "Alexa Chan", "email": "alexachan@striking.com.hk"},
                {"name": "Leah Kung", "email": "yhkung@striking.com.hk"},
                {"name": "Thossapong", "email": "tt@strikids.com"},
                {"name": "Mike Wong", "email": "mikewong@striking.com.hk"}
            ],
            "transcript": [
                {"speaker": "Mike", "timestamp": "00:05:00", "text": "All team members will review the TVC script by Friday."}
            ]
        }

        def mock_llm_caller(prompt, model):
            payload = LLMExtractionPayload(
                meeting_meta=MeetingMeta(
                    meeting_id="meet_team_assign",
                    title="Executive Review",
                    attendees=["Duong Tan", "Alexa Chan", "Leah Kung", "Thossapong", "Mike Wong"]
                ),
                workstream="Executive Governance & TVC",
                action_items=[
                    LLMActionItem(
                        task_name="Review TVC script",
                        spoken_assignee="ALL_MEETING_ATTENDEES",
                        commitment_type="group",
                        evidence=EvidenceQuote(quote="All team members will review the TVC script by Friday.", speaker="Mike"),
                        continuity_type="NEW_COMMITMENT",
                        is_commitment=True
                    )
                ]
            )
            return payload.model_dump_json(), 200, 100

        extractor = LLMExtractor(custom_caller=mock_llm_caller)
        res, cost = extractor.extract(mock_payload)
        self.assertEqual(len(res.tasks), 1)
        task = res.tasks[0]
        # Verify resolved_user_ids contains all 5 attendees
        self.assertGreaterEqual(len(task.resolved_user_ids), 4)
        self.assertIn(113703761, task.resolved_user_ids)  # Tan
        self.assertIn(103982652, task.resolved_user_ids)  # Alexa
        self.assertIn(113704803, task.resolved_user_ids)  # Leah
        self.assertIn(103551084, task.resolved_user_ids)  # Mike

        # Test column values assembly for Monday
        col_vals = build_column_values(task.model_dump())
        assign_to_col = col_vals[config.COLUMNS["assign_to"]]
        person_ids = [p["id"] for p in assign_to_col["personsAndTeams"]]
        self.assertGreaterEqual(len(person_ids), 4)

    def test_clean_title_formatting(self):
        """Verify that task title has NO [Assignee] prefix and retains lightning badge."""
        task_dict = {
            "task_id": "tsk_title_test",
            "meeting_id": "meet_title_test",
            "title": "[Tan] Prepare 30s storyboard draft",
            "resolved_user_id": 113703761,
            "resolved_name": "Duong Tan",
            "status": "APPROVED"
        }
        res = sync_task_to_monday(task_dict, dry_run=True)
        # Should be clean: "⚡ Prepare 30s storyboard draft", NOT "⚡ [Tan] Prepare..."
        self.assertEqual(res["item_name"], "⚡ Prepare 30s storyboard draft")

if __name__ == "__main__":
    unittest.main()
