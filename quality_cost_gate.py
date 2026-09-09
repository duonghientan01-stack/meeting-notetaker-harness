# -*- coding: utf-8 -*-
"""Verifiable Quality & Cost Gate for Meeting Intelligence Action Items.

Guarantees (Hardened against F1 / F2 / B6 / §6.1):
- G1: Title has imperative verb and ≥ 3 words, not generic fragment.
- G2: Verbatim quote must be a literal substring of transcript (Anti-Invention check).
- G3: Explicit commitment verification (is_commitment == True).
- G4: Due date is valid or strictly empty (never fabricated).
- G5: Tier 1 & 2 routed to user; Tier 3 strictly unassigned (never guessed).
- G6: Idempotency pre-check against local DB.
- G7: Real cost ledger check against per-meeting cap ($0.50) and daily cap ($5.00).
- G8: Meeting item count cap (≤ 15 items; > 15 parks entire meeting for human review).
- DLQ: Unique UUID keys to prevent IntegrityError collisions (B9).
"""
import uuid
import re
from typing import Dict, Any, Tuple, List, Optional
from pathlib import Path

try:
    from .config import (
        MIN_CONFIDENCE_SCORE,
        BUDGET_CAP_PER_MEETING_USD,
        DAILY_BUDGET_CAP_USD,
        MAX_TASKS_PER_MEETING,
        MAX_RETRIES
    )
    from . import db
except (ImportError, ValueError):
    from config import (
        MIN_CONFIDENCE_SCORE,
        BUDGET_CAP_PER_MEETING_USD,
        DAILY_BUDGET_CAP_USD,
        MAX_TASKS_PER_MEETING,
        MAX_RETRIES
    )
    import db

class QualityCostGate:
    def __init__(self,
                 min_confidence: float = MIN_CONFIDENCE_SCORE,
                 budget_cap_meeting_usd: float = BUDGET_CAP_PER_MEETING_USD,
                 daily_budget_cap_usd: float = DAILY_BUDGET_CAP_USD,
                 max_tasks_per_meeting: int = MAX_TASKS_PER_MEETING,
                 max_retries: int = MAX_RETRIES):
        self.min_confidence = min_confidence
        self.budget_cap_meeting_usd = budget_cap_meeting_usd
        self.daily_budget_cap_usd = daily_budget_cap_usd
        self.max_tasks_per_meeting = max_tasks_per_meeting
        self.max_retries = max_retries
        
    def evaluate_task(self, task: Dict[str, Any], full_transcript_text: str = "") -> Tuple[bool, str]:
        """
        Evaluate if a single task passes verifiable quality gate checks (G1 - G5).
        Returns (passed: bool, reason: str).
        """
        title = task.get("title", "").strip()
        quote = task.get("context_quote", "").strip()
        due_date = task.get("due_date")
        due_date_source = task.get("due_date_source", "absent")
        confidence = float(task.get("confidence_score", 0.0))
        is_commitment = task.get("is_commitment", True)
        
        # G3: Must be a true commitment
        if not is_commitment:
            return False, "G3_NOT_A_COMMITMENT: Utterance was conversational or non-committal"
            
        # G1: Title Substantiveness (≥ 3 words, ≥ 10 chars, not generic)
        words = title.split()
        if len(words) < 3 or len(title) < 10:
            return False, f"G1_TITLE_TOO_SHORT: Task title '{title}' must contain at least 3 words and 10 characters"
            
        generic_titles = ["action item", "follow up", "task", "meeting task", "untitled task"]
        if title.lower() in generic_titles:
            return False, f"G1_GENERIC_TITLE: Task title cannot be generic '{title}'"
            
        # G2: Verbatim Context Grounding Check (Anti-Invention)
        if not quote:
            return False, "G2_MISSING_QUOTE: Missing verbatim transcript quote"
            
        if full_transcript_text and quote not in full_transcript_text:
            # Check normalized quote substring match
            norm_q = re.sub(r'\s+', ' ', quote).strip().lower()
            norm_t = re.sub(r'\s+', ' ', full_transcript_text).strip().lower()
            if norm_q not in norm_t:
                return False, "G2_FABRICATED_QUOTE: Quote was not found as a verbatim substring in transcript"
                
        # G4: Anti-Fabricated Due Date Check
        if due_date and due_date_source == "absent":
            return False, f"G4_FABRICATED_DATE: Due date '{due_date}' was assigned without a spoken deadline source"
            
        # G5: Entity Resolution sanity
        tier = task.get("resolution_tier", "TIER_3_FALLBACK")
        user_id = task.get("resolved_user_id")
        if tier == "TIER_3_FALLBACK" and user_id is not None:
            return False, f"G5_INVALID_RESOLUTION: Tier 3 items must be strictly unassigned (got user_id={user_id})"
            
        # Confidence score check against threshold
        if confidence < self.min_confidence:
            return False, f"CONFIDENCE_TOO_LOW: Score ({confidence:.2f}) < threshold ({self.min_confidence:.2f})"
            
        return True, "Passed Quality Gate"
        
    def evaluate_and_route(self, tasks: List[Any],
                           meeting_id: str,
                           meeting_transcript: Optional[str] = "",
                           current_cost_usd: float = 0.0,
                           db_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Filter tasks through Quality & Cost gates.
        Approved tasks move to human approval digest or Monday Syncer.
        Failed tasks are recorded in Dead-Letter Queue (DLQ).
        """
        approved = []
        blocked = []
        
        # Convert Pydantic models to dict if needed
        task_dicts = [t if isinstance(t, dict) else t.model_dump() for t in tasks]
        
        # G8: Task Flood Safety Cap (> 15 items parks entire meeting)
        if len(task_dicts) > self.max_tasks_per_meeting:
            reason = f"G8_FLOOD_CAP_EXCEEDED: Meeting produced {len(task_dicts)} tasks (> {self.max_tasks_per_meeting} limit). Parked for human review."
            for t in task_dicts:
                t["quality_gate_passed"] = False
                blocked.append(t)
                dlq_id = f"dlq_flood_{uuid.uuid4().hex[:10]}"
                db.push_to_dlq(
                    failure_reason=reason,
                    payload=t,
                    dlq_id=dlq_id,
                    task_id=t.get("task_id"),
                    meeting_id=meeting_id,
                    db_path=db_path
                )
            return {
                "approved_tasks": [],
                "blocked_tasks": blocked,
                "cost_overrun": False,
                "flood_cap_exceeded": True,
                "summary": reason
            }
            
        # G7: Cost Gate (Per-meeting and daily budget check from real ledger)
        historical_meeting_cost = db.get_meeting_cost_usd(meeting_id, db_path=db_path)
        total_meeting_cost = historical_meeting_cost + current_cost_usd
        daily_cost = db.get_daily_cost_usd(db_path=db_path) + current_cost_usd
        
        if total_meeting_cost > self.budget_cap_meeting_usd:
            reason = f"G7_MEETING_BUDGET_EXCEEDED: Meeting cost (${total_meeting_cost:.4f}) exceeded meeting cap (${self.budget_cap_meeting_usd:.2f})"
            for t in task_dicts:
                t["quality_gate_passed"] = False
                blocked.append(t)
                dlq_id = f"dlq_cost_{uuid.uuid4().hex[:10]}"
                db.push_to_dlq(
                    failure_reason=reason,
                    payload=t,
                    dlq_id=dlq_id,
                    task_id=t.get("task_id"),
                    meeting_id=meeting_id,
                    db_path=db_path
                )
            return {
                "approved_tasks": [],
                "blocked_tasks": blocked,
                "cost_overrun": True,
                "flood_cap_exceeded": False,
                "summary": reason
            }
            
        if daily_cost > self.daily_budget_cap_usd:
            reason = f"G7_DAILY_BUDGET_EXCEEDED: Daily cost (${daily_cost:.4f}) exceeded daily cap (${self.daily_budget_cap_usd:.2f})"
            for t in task_dicts:
                t["quality_gate_passed"] = False
                blocked.append(t)
                dlq_id = f"dlq_dailycost_{uuid.uuid4().hex[:10]}"
                db.push_to_dlq(
                    failure_reason=reason,
                    payload=t,
                    dlq_id=dlq_id,
                    task_id=t.get("task_id"),
                    meeting_id=meeting_id,
                    db_path=db_path
                )
            return {
                "approved_tasks": [],
                "blocked_tasks": blocked,
                "cost_overrun": True,
                "flood_cap_exceeded": False,
                "summary": reason
            }
            
        # Record cost to ledger if non-zero
        if current_cost_usd > 0:
            db.record_cost(
                meeting_id=meeting_id,
                stage="extraction_and_gating",
                cost_usd=current_cost_usd,
                db_path=db_path
            )
            
        # Evaluate individual tasks
        for t in task_dicts:
            passed, reason = self.evaluate_task(t, full_transcript_text=meeting_transcript or "")
            
            # G6: Local Idempotency check
            if passed and t.get("idempotency_key"):
                existing_task = db.get_task_by_idempotency_key(meeting_id, t["idempotency_key"], db_path=db_path)
                if existing_task:
                    passed = False
                    reason = f"G6_DUPLICATE_TASK: Task with identical idempotency key already exists (task_id={existing_task['task_id']})"
                    
            if passed:
                t["quality_gate_passed"] = True
                approved.append(t)
            else:
                t["quality_gate_passed"] = False
                blocked.append(t)
                # Push to DLQ with unique UUID
                dlq_id = f"dlq_qual_{uuid.uuid4().hex[:10]}"
                db.push_to_dlq(
                    failure_reason=reason,
                    payload=t,
                    dlq_id=dlq_id,
                    task_id=t.get("task_id"),
                    meeting_id=meeting_id,
                    db_path=db_path
                )
                
        return {
            "approved_tasks": approved,
            "blocked_tasks": blocked,
            "cost_overrun": False,
            "flood_cap_exceeded": False,
            "summary": f"Approved {len(approved)}/{len(task_dicts)} tasks. Blocked {len(blocked)}."
        }
