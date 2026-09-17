# -*- coding: utf-8 -*-
"""Unit tests for Meeting Intelligence Harness v3.1 Upgrade.

Validates:
1. Dual-Mode Thematic MoM ingestion.
2. Entity Resolution for core team, compound names (Alexa/team), and aliases.
3. Timeline cross-referencing for relative deadlines.
4. Dynamic workstream classification (TVC & Creative Video Production).
5. Rich context and HTML formatting in Monday Syncer.
"""
import unittest
import pathlib
import sys
import datetime

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_DIR))

import email_parser
import user_resolver
import extractor
import llm_extractor
import monday_syncer
import config

SAMPLE_TVC_MOM = """Meeting summary
 Visual Direction & Storyboard
Use a realistic, non-Hong Kong-specific background, such as a general playground.
Tan will create an initial storyboard/video draft to demonstrate character actions, scene flow, and transitions.
Emmy will refine or redraw visuals where AI may struggle, including specific character poses, effects, and action details.
Use realistic popping-candy visuals where possible; the team will photograph or film the candy and its splash effect.
The candy should retain the original sharp, block-like appearance rather than using rounded konpeitō-style candy.
The final frame should show the product packaging only; Strike Kids does not need to reappear.
Dinky’s missing frame must be restored based on the updated script.
 Production Workflow & Audio
Tan will first build the scene backgrounds and place the existing characters as temporary references.
Emmy will use Tan’s draft to produce accurate character drawings and visual corrections.
Difficult transitions between different backgrounds will be developed collaboratively.
The video will initially be assembled without voice-over, with editing and music added afterward.
Alexa will provide the updated script and the 30-second Stray Kids audio, as well as the full song and related video.
The advertisement will use Cantonese voice-over and Chinese subtitles.
The team will review the first draft before finalizing the voice-over and subtitles.
 Timeline & Coordination
Tan will send the initial draft or storyboard materials to Emmy the following day.
Emmy expects each character illustration to take approximately one to two days, depending on the number of images required.
The team set September 30 as an initial target for reviewing progress, with the understanding that revisions may be needed.
The team will assess what can be completed within two weeks and review the first draft in early October if necessary.
A dedicated WhatsApp group will be created for this project.
Tan and Emmy will coordinate directly in the group, while daily progress updates will be reported there for management visibility.
 Action Items
Tan: Create and share the initial storyboard/video draft, including proposed character actions and transitions.
Tan: Identify scenes and visual elements that require Emmy’s custom drawings.
Alexa: Send the updated script, 30-second audio, full song, and relevant Stray Kids video.
Emmy: Create or revise character illustrations based on Tan’s draft.
Alexa/team: Photograph or film suitable popping-candy visuals and splash effects.
Team: Restore Dinky’s missing frame and confirm the final sequence.
Alexa: Create a dedicated WhatsApp group and add the required project members, including Jerry.
All team members: Review the first draft and coordinate revisions toward the September 30 checkpoint.

Supercharge your meetings with AI
Get summaries, answers, and much more by asking our assistant.
Ask anything about this meeting...	
What's HappyScribe's AI Notetaker?
HappyScribe’s AI Notetaker helps you get more out of your video meetings by transcribing and summarizing them.
Manage notetaker settings
"""

class TestMoMUpgradeV31(unittest.TestCase):
    def test_dual_mode_parser_preserves_sections_and_speakers(self):
        utterances = email_parser.parse_transcript_lines(SAMPLE_TVC_MOM)
        speakers = [u["speaker"] for u in utterances]
        
        # Verify thematic sections preserved
        self.assertTrue(any("Visual Direction" in s for s in speakers), "Visual Direction section must be preserved")
        self.assertTrue(any("Production Workflow" in s for s in speakers), "Production Workflow section must be preserved")
        self.assertTrue(any("Timeline" in s for s in speakers), "Timeline section must be preserved")
        
        # Verify action item speakers preserved
        self.assertIn("Tan", speakers)
        self.assertIn("Alexa", speakers)
        self.assertIn("Emmy", speakers)
        self.assertIn("Alexa/team", speakers)
        self.assertIn("Team", speakers)
        self.assertIn("All team members", speakers)
        
        # Verify marketing footer removed
        for u in utterances:
            self.assertNotIn("Supercharge your meetings with AI", u["text"])
            self.assertNotIn("HappyScribe’s AI Notetaker helps you", u["text"])

    def test_entity_resolution_v31(self):
        # Tan
        res_tan = user_resolver.resolve_assignee("Tan")
        self.assertEqual(res_tan["resolved_user_id"], 113703761)
        self.assertEqual(res_tan["resolved_name"], "Duong Tan")

        # Duonghien Tan (sender display name variation)
        res_duonghien = user_resolver.resolve_assignee("Duonghien Tan")
        self.assertEqual(res_duonghien["resolved_user_id"], 113703761)

        # Alexa
        res_alexa = user_resolver.resolve_assignee("Alexa")
        self.assertEqual(res_alexa["resolved_user_id"], 103982652)
        self.assertIn("Alexa", res_alexa["resolved_name"])

        # Alexa/team compound
        res_alexa_team = user_resolver.resolve_assignee("Alexa/team")
        self.assertEqual(res_alexa_team["resolved_user_id"], 103982652)

        # Emmy
        res_emmy = user_resolver.resolve_assignee("Emmy")
        self.assertEqual(res_emmy["resolved_user_id"], 112035594)
        self.assertIn("Emmy", res_emmy["resolved_name"].title())

        # Jerry
        res_jerry = user_resolver.resolve_assignee("Jerry")
        self.assertEqual(res_jerry["resolved_user_id"], 107995985)

    def test_semantic_extraction_and_timeline_correlation(self):
        utterances = email_parser.parse_transcript_lines(SAMPLE_TVC_MOM)
        ref_date = datetime.date(2026, 9, 18)
        meeting_input = {
            "meeting_id": "tvc_test_v31",
            "title": "[MoM] TVC 30S Strikids cho HK",
            "platform": "Microsoft Teams",
            "started_at": "2026-09-18T10:00:00+08:00",
            "timezone": "Asia/Hong_Kong",
            "participants": [{"name": "Duong Tan"}, {"name": "Alexa Chan"}, {"name": "Emmy Chan"}],
            "transcript": utterances
        }
        
        # Test semantic extractor
        res, cost = llm_extractor.extract_action_items_llm(meeting_input)
        self.assertGreaterEqual(len(res.tasks), 6)
        
        # Verify workstream classified dynamically
        workstreams = [t.workstream for t in res.tasks]
        self.assertTrue(all(w == "TVC & Creative Video Production" for w in workstreams), 
                        f"Expected TVC workstream, got: {set(workstreams)}")
        
        # Verify 0 tasks with [Needs Review]
        for t in res.tasks:
            self.assertNotEqual(t.resolved_name, "Needs Review")
            self.assertIsNotNone(t.resolved_user_id, f"Task '{t.title}' was unresolved")

        # Verify timeline correlation
        tan_tasks = [t for t in res.tasks if t.resolved_user_id == 113703761 and "storyboard" in t.title.lower()]
        self.assertTrue(len(tan_tasks) > 0)
        # Should have tomorrow's deadline
        self.assertEqual(tan_tasks[0].due_date, "2026-09-19")
        
        # Review task should correlate to September 30
        review_tasks = [t for t in res.tasks if "checkpoint" in t.context_quote.lower() or "september 30" in t.context_quote.lower()]
        self.assertTrue(len(review_tasks) > 0)
        self.assertEqual(review_tasks[0].due_date, "2026-09-30")

    def test_monday_syncer_formatting_v31(self):
        task = {
            "task_id": "tsk_test_v31",
            "meeting_id": "tvc_test_v31",
            "title": "Create or revise character illustrations",
            "raw_assignee": "Emmy",
            "resolved_user_id": 112035594,
            "resolved_name": "Emmy Chan",
            "resolved_email": "emmychan@striking.com.hk",
            "resolution_tier": "TIER_2_FUZZY_NAME",
            "due_date": "2026-09-21",
            "priority": "High",
            "workstream": "TVC & Creative Video Production",
            "phase": "Phase 2: Character Illustrations",
            "technical_context": "Sharp block-like popping candy appearance; realistic splash effect; restore Dinky missing frame.",
            "context_quote": "Emmy: Create or revise character illustrations based on Tan’s draft.",
            "confidence_score": 0.95
        }
        
        # Column values
        cols = monday_syncer.build_column_values(task)
        self.assertEqual(cols[config.COLUMNS["workstream"]], "TVC & Creative Video Production")
        self.assertEqual(cols[config.COLUMNS["due_date"]]["date"], "2026-09-21")
        self.assertEqual(cols[config.COLUMNS["assign_to"]]["personsAndTeams"][0]["id"], 112035594)
        
        # HTML Update
        html_update = monday_syncer.build_html_update(task, {"title": "TVC 30S Strikids"})
        self.assertIn("TVC &amp; Creative Video Production", html_update)
        self.assertIn("Phase 2: Character Illustrations", html_update)
        self.assertIn("Technical Guidelines & Context", html_update)
        self.assertIn("Sharp block-like popping candy", html_update)
        
        # Clean naming without [Needs Review]
        res_sync = monday_syncer.sync_task_to_monday(task, dry_run=True)
        self.assertIn("⚡ [Emmy Chan] Create or revise character illustrations", res_sync["item_name"])
        self.assertNotIn("Needs Review", res_sync["item_name"])

if __name__ == "__main__":
    unittest.main()
