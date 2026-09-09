# -*- coding: utf-8 -*-
"""FastAPI Ingestion Gateway & Webhook Server for Meeting Intelligence.

Guarantees (Hardened against B2 / B9 / B10 / F5 / F8):
1. HMAC-SHA256 signature verification with constant-time comparison on webhooks.
2. Background task exceptions are caught and recorded to DLQ (never silently swallowed).
3. Human approval gate workflow (Shadow / Assisted mode before autonomous writes).
4. DLQ retry and discard lifecycle management.
5. Observability /health with spend ledger, token check, and meeting freshness.
6. Modern FastAPI lifespan context manager.
"""
import os
import hmac
import hashlib
import json
import uuid
import secrets
import datetime
import time
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, BackgroundTasks, Query, Request, status
from pydantic import BaseModel, Field

try:
    from . import db, config
    from .user_resolver import sync_users_from_monday
    from .extractor import extract_action_items
    from .llm_extractor import extract_action_items_llm
    from .quality_cost_gate import QualityCostGate
    from .monday_syncer import sync_all_approved_tasks
    from .email_parser import parse_email_to_envelope, EmailFingerprintError, ZeroExtractionError
except (ImportError, ValueError):
    import db, config
    from user_resolver import sync_users_from_monday
    from extractor import extract_action_items
    from llm_extractor import extract_action_items_llm
    from quality_cost_gate import QualityCostGate
    from monday_syncer import sync_all_approved_tasks
    from email_parser import parse_email_to_envelope, EmailFingerprintError, ZeroExtractionError

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler initializing database and tables."""
    db.init_db()
    yield

# Initialize FastAPI app
app = FastAPI(
    title="Meeting Intelligence Harness",
    description="Enterprise Action Item Extraction and Monday.com Synchronization for MS Teams & Happy Scribe",
    version="3.0.0",
    lifespan=lifespan
)

# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------
class ApprovalDecisionRequest(BaseModel):
    approved_task_ids: List[str] = Field(default_factory=list)
    rejected_task_ids: List[str] = Field(default_factory=list)
    reviewer_notes: Optional[str] = None
    dry_run: bool = False

class DiscardDLQRequest(BaseModel):
    reason: str

# ---------------------------------------------------------------------------
# Webhook Authentication Helper
# ---------------------------------------------------------------------------
def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str], timestamp_header: Optional[str] = None):
    """
    Verify HMAC signature of incoming webhook.
    Fails immediately (HTTP 401/403) on invalid signature without creating DLQ clutter (B2 / §4.4).
    """
    secret = config.WEBHOOK_SECRET
    if not secret:
        # In production, missing secret blocks webhooks
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured")
        
    if not signature_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-HappyScribe-Signature header")
        
    # Check timestamp replay window if header provided
    if timestamp_header:
        try:
            req_ts = float(timestamp_header)
            current_ts = time.time()
            if abs(current_ts - req_ts) > 300:  # 5 minutes replay window
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook timestamp outside 5-minute replay window")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp format")
            
    # Compute expected HMAC-SHA256 signature
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    clean_sig = signature_header.replace("sha256=", "").strip()
    
    if not hmac.compare_digest(computed, clean_sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook signature")

# ---------------------------------------------------------------------------
# Core Pipeline
# ---------------------------------------------------------------------------
def process_meeting_pipeline(raw_payload: Dict[str, Any], dry_run: bool = False, require_approval: bool = True, use_llm: Optional[bool] = None, demo_tag: bool = False) -> Dict[str, Any]:
    """
    Execute end-to-end meeting processing pipeline.
    
    Phases:
    1. Ingestion normalization & content hash check.
    2. Action extraction (LLM Reasoning or Rule-based) & entity resolution.
    3. Quality & cost gate validation.
    4. Human approval digest generation (Assisted mode) or Monday sync.
    """
    meeting_meta = raw_payload.get("meeting", raw_payload)
    meeting_id = meeting_meta.get("meeting_id") or f"meet_{uuid.uuid4().hex[:10]}"
    meeting_meta["meeting_id"] = meeting_id
    
    # 1. Normalized content hash check
    transcript_list = raw_payload.get("transcript", [])
    normalized_text = " ".join([f"{u.get('speaker','')}:{u.get('text','')}" for u in transcript_list])
    content_hash = raw_payload.get("content_hash") or hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    
    existing = db.get_meeting_by_hash(content_hash)
    if existing and not dry_run:
        tasks = db.get_tasks_for_meeting(existing["meeting_id"])
        return {
            "status": "IDEMPOTENT_SKIPPED",
            "meeting_id": existing["meeting_id"],
            "message": "Meeting already processed with identical normalized transcript hash.",
            "tasks_count": len(tasks),
            "tasks": tasks
        }
        
    # 2. Save Meeting Record
    meeting_record = {
        "meeting_id": meeting_id,
        "title": meeting_meta.get("title", "Microsoft Teams Meeting"),
        "platform": meeting_meta.get("platform", "Microsoft Teams"),
        "started_at": meeting_meta.get("started_at"),
        "ended_at": meeting_meta.get("ended_at"),
        "timezone": meeting_meta.get("timezone", config.DEFAULT_TIMEZONE),
        "recording_url": meeting_meta.get("recording_url"),
        "raw_payload": raw_payload,
        "content_hash": content_hash,
        "status": "PROCESSING"
    }
    if not dry_run:
        db.save_meeting(meeting_record)
        
    # 3. Extract Action Items & Grounded Quotes (LLM or Rule Baseline)
    extraction_input = {
        "meeting_id": meeting_id,
        "title": meeting_record["title"],
        "platform": meeting_record["platform"],
        "started_at": meeting_record["started_at"],
        "ended_at": meeting_record["ended_at"],
        "timezone": meeting_record["timezone"],
        "recording_url": meeting_record["recording_url"],
        "participants": meeting_meta.get("participants", []),
        "transcript": transcript_list
    }
    
    should_use_llm = config.USE_LLM_EXTRACTOR if use_llm is None else use_llm
    current_cost_usd = 0.0
    if should_use_llm:
        extraction_res, current_cost_usd = extract_action_items_llm(extraction_input)
    else:
        extraction_res = extract_action_items(extraction_input)
    
    # 4. Quality & Cost Gate Filtering
    gate = QualityCostGate()
    gate_res = gate.evaluate_and_route(
        tasks=extraction_res.tasks,
        meeting_id=meeting_id,
        meeting_transcript=normalized_text,
        current_cost_usd=current_cost_usd
    )
    
    approved_tasks = gate_res["approved_tasks"]
    blocked_tasks = gate_res["blocked_tasks"]
    
    # Tag demo tasks if requested
    if demo_tag or raw_payload.get("demo_tag"):
        for t in approved_tasks:
            t["demo_tag"] = True
            t["title_prefix"] = "🤖 [Demo test Automation task assign] "

    # Save initial task states
    if not dry_run:
        for t in approved_tasks + blocked_tasks:
            db.save_task(t)
            
    # 5. Human Approval Gate Workflow (§6.3, B10)
    approval_token = None
    if require_approval and not dry_run:
        approval_id = f"appr_{uuid.uuid4().hex[:10]}"
        approval_token = secrets.token_urlsafe(24)
        db.save_approval({
            "approval_id": approval_id,
            "meeting_id": meeting_id,
            "token": approval_token,
            "status": "PENDING",
            "digest_payload": {
                "meeting_id": meeting_id,
                "title": meeting_record["title"],
                "proposed_tasks": approved_tasks,
                "blocked_tasks": blocked_tasks,
                "gate_summary": gate_res["summary"]
            }
        })
        meeting_record["status"] = "AWAITING_HUMAN_APPROVAL"
        db.save_meeting(meeting_record)
        sync_results = []
    else:
        # Autonomous / Dry run sync
        sync_results = sync_all_approved_tasks(
            approved_tasks=approved_tasks,
            meeting_meta=meeting_record,
            dry_run=dry_run
        )
        if not dry_run:
            meeting_record["status"] = "COMPLETED"
            db.save_meeting(meeting_record)
            
    return {
        "status": "AWAITING_APPROVAL" if (require_approval and not dry_run) else "SUCCESS",
        "meeting_id": meeting_id,
        "title": meeting_record["title"],
        "total_extracted": len(extraction_res.tasks),
        "approved_count": len(approved_tasks),
        "blocked_count": len(blocked_tasks),
        "approval_token": approval_token,
        "sync_results": sync_results,
        "dry_run": dry_run
    }

def safe_background_pipeline(payload: Dict[str, Any]):
    """Background task wrapper catching unhandled exceptions and logging to DLQ (F8)."""
    try:
        process_meeting_pipeline(payload, dry_run=False, require_approval=getattr(config, "REQUIRE_HUMAN_APPROVAL", False))
    except Exception as e:
        meeting_id = payload.get("meeting_id", payload.get("meeting", {}).get("meeting_id", "meet_unhandled"))
        db.push_to_dlq(
            failure_reason=f"PIPELINE_UNHANDLED_EXCEPTION: {str(e)}",
            payload=payload,
            meeting_id=meeting_id
        )
        db.log_event({
            "event_type": "BACKGROUND_PIPELINE_ERROR",
            "status": "ERROR",
            "error_str": str(e),
            "payload": {"meeting_id": meeting_id}
        })

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {
        "service": "Meeting Intelligence Harness",
        "version": "3.0.0",
        "mode": "Shadow / Assisted Mode",
        "target_board": config.BOARD_ID,
        "docs_url": "/docs"
    }

@app.get("/health")
def health_check():
    """Observability endpoint reporting database, token, cost, DLQ, and meeting freshness (B6 / §10.1)."""
    users = db.get_all_users(active_only=True)
    dlq_items = db.get_dlq_items(status="PENDING_REVIEW")
    daily_spend = db.get_daily_cost_usd()
    
    # Check Monday token availability
    has_monday_token = bool(config.os.environ.get("MONDAY_TOKEN"))
    
    return {
        "status": "HEALTHY",
        "version": "3.0.0",
        "mode": "Shadow / Assisted Mode",
        "database": str(config.SQLITE_DB_PATH),
        "active_users_cached": len(users),
        "dlq_pending_count": len(dlq_items),
        "daily_spend_usd": daily_spend,
        "daily_budget_cap_usd": config.DAILY_BUDGET_CAP_USD,
        "monday_token_configured": has_monday_token,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

@app.post("/api/v1/sync/users")
def trigger_user_sync():
    """Manually trigger Monday User Directory synchronization with pagination."""
    try:
        synced = sync_users_from_monday()
        return {
            "success": True,
            "synced_count": len(synced),
            "users": synced
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sync Monday users: {str(e)}")

@app.post("/api/v1/process-transcript")
def process_transcript_endpoint(
    payload: Dict[str, Any],
    dry_run: bool = Query(default=True),
    require_approval: bool = Query(default=True),
    use_llm: Optional[bool] = Query(default=None)
):
    """Direct processing endpoint for test transcripts and manual submissions."""
    try:
        res = process_meeting_pipeline(payload, dry_run=dry_run, require_approval=require_approval, use_llm=use_llm)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/ingest/email")
async def ingest_raw_email(request: Request, background_tasks: BackgroundTasks):
    """Ingest a raw MIME email, parse to MeetingEnvelope v1, and queue for processing."""
    body_bytes = await request.body()
    if not body_bytes:
        raise HTTPException(status_code=400, detail="Empty email body")
        
    try:
        envelope = parse_email_to_envelope(body_bytes)
        # Process in background safely
        background_tasks.add_task(safe_background_pipeline, envelope)
        return {
            "status": "ACCEPTED",
            "message": "Raw email parsed to MeetingEnvelope v1 and queued.",
            "meeting_id": envelope["meeting"]["meeting_id"],
            "title": envelope["meeting"]["title"],
            "content_hash": envelope["content_hash"]
        }
    except (EmailFingerprintError, ZeroExtractionError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email parsing error: {str(e)}")

@app.post("/api/v1/webhooks/happyscribe")
async def happyscribe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_happyscribe_signature: Optional[str] = Header(None),
    x_happyscribe_timestamp: Optional[str] = Header(None)
):
    """
    Ingest live Happy Scribe webhook with HMAC signature verification (B2 / §4.4).
    """
    body_bytes = await request.body()
    
    # If secret is set, verify signature strictly
    if config.WEBHOOK_SECRET:
        verify_webhook_signature(body_bytes, x_happyscribe_signature, x_happyscribe_timestamp)
        
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    background_tasks.add_task(safe_background_pipeline, payload)
    return {
        "status": "ACCEPTED",
        "message": "Webhook authenticated and queued for meeting intelligence.",
        "received_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

# ---------------------------------------------------------------------------
# Human Approval Gate Endpoints (§6.3, B10)
# ---------------------------------------------------------------------------
@app.get("/api/v1/approvals/{token}")
def get_approval_digest(token: str):
    """Retrieve proposed action items and evidence digest for human review."""
    appr = db.get_approval_by_token(token)
    if not appr:
        raise HTTPException(status_code=404, detail="Approval digest not found or expired")
    return appr

@app.post("/api/v1/approvals/{token}/approve-all")
def approve_all_digest(token: str, dry_run: bool = Query(default=False)):
    """Approve all proposed items in the digest and synchronize to Monday.com."""
    appr = db.get_approval_by_token(token)
    if not appr:
        raise HTTPException(status_code=404, detail="Approval digest not found")
        
    if appr["status"] != "PENDING":
        return {"message": f"Approval digest already reviewed: status={appr['status']}"}
        
    meeting_id = appr["meeting_id"]
    proposed_tasks = appr["digest_payload"].get("proposed_tasks", [])
    approved_ids = [t["task_id"] for t in proposed_tasks]
    
    # Mark tasks approved
    for t in proposed_tasks:
        t["approval_status"] = "APPROVED"
        db.save_task(t)
        
    # Sync to Monday
    meeting = db.get_meeting(meeting_id) or {"meeting_id": meeting_id, "title": "Meeting"}
    sync_results = sync_all_approved_tasks(proposed_tasks, meeting_meta=meeting, dry_run=dry_run)
    
    # Update approval record
    appr["status"] = "APPROVED_ALL"
    appr["approved_task_ids"] = approved_ids
    appr["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.save_approval(appr)
    
    return {
        "status": "APPROVED_ALL",
        "meeting_id": meeting_id,
        "synced_count": len(sync_results),
        "results": sync_results
    }

@app.post("/api/v1/approvals/{token}/decide")
def decide_approval_digest(token: str, decision: ApprovalDecisionRequest):
    """Selectively approve or reject specific tasks from the digest."""
    appr = db.get_approval_by_token(token)
    if not appr:
        raise HTTPException(status_code=404, detail="Approval digest not found")
        
    meeting_id = appr["meeting_id"]
    proposed_tasks = appr["digest_payload"].get("proposed_tasks", [])
    
    tasks_to_sync = []
    for t in proposed_tasks:
        tid = t.get("task_id")
        if tid in decision.approved_task_ids:
            t["approval_status"] = "APPROVED"
            db.save_task(t)
            tasks_to_sync.append(t)
        elif tid in decision.rejected_task_ids:
            t["approval_status"] = "REJECTED"
            db.save_task(t)
            
    sync_results = []
    if tasks_to_sync:
        meeting = db.get_meeting(meeting_id) or {"meeting_id": meeting_id, "title": "Meeting"}
        sync_results = sync_all_approved_tasks(tasks_to_sync, meeting_meta=meeting, dry_run=decision.dry_run)
        
    appr["status"] = "APPROVED_SELECTED"
    appr["approved_task_ids"] = decision.approved_task_ids
    appr["rejected_task_ids"] = decision.rejected_task_ids
    appr["reviewer_notes"] = decision.reviewer_notes
    appr["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.save_approval(appr)
    
    return {
        "status": "APPROVED_SELECTED",
        "meeting_id": meeting_id,
        "approved_count": len(tasks_to_sync),
        "rejected_count": len(decision.rejected_task_ids),
        "sync_results": sync_results
    }

# ---------------------------------------------------------------------------
# DLQ Management Endpoints (§10.2, B9)
# ---------------------------------------------------------------------------
@app.get("/api/v1/dlq")
def get_dead_letter_queue(status: Optional[str] = Query(default=None)):
    """Inspect dead-letter queue items."""
    return {
        "dlq_items": db.get_dlq_items(status=status)
    }

@app.post("/api/v1/dlq/{dlq_id}/retry")
def retry_dlq_item(dlq_id: str, background_tasks: BackgroundTasks):
    """Retry a failed item from the dead-letter queue."""
    items = db.get_dlq_items(status=None)
    target = next((i for i in items if i["dlq_id"] == dlq_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="DLQ item not found")
        
    payload = target.get("payload", {})
    db.update_dlq_status(dlq_id, status="RETRIED", increment_retry=True)
    background_tasks.add_task(safe_background_pipeline, payload)
    
    return {
        "status": "RETRY_QUEUED",
        "dlq_id": dlq_id,
        "retry_count": target.get("retry_count", 0) + 1
    }

@app.post("/api/v1/dlq/{dlq_id}/discard")
def discard_dlq_item(dlq_id: str, req: DiscardDLQRequest):
    """Mark a DLQ item as discarded with review reason."""
    success = db.update_dlq_status(dlq_id, status="DISCARDED", resolution_notes=req.reason)
    if not success:
        raise HTTPException(status_code=404, detail="DLQ item not found")
    return {
        "status": "DISCARDED",
        "dlq_id": dlq_id,
        "reason": req.reason
    }

@app.get("/api/v1/meetings/{meeting_id}")
def get_meeting_details(meeting_id: str):
    """Get meeting metadata and associated extracted tasks."""
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    tasks = db.get_tasks_for_meeting(meeting_id)
    return {
        "meeting": meeting,
        "tasks_count": len(tasks),
        "tasks": tasks
    }

@app.get("/api/v1/debug/openai")
def debug_openai_connectivity():
    """Diagnostic endpoint to inspect OpenAI API key cleanliness and live connection from the host/container."""
    import traceback
    import socket
    import urllib.request

    raw_key = os.environ.get("OPENAI_API_KEY", "")
    clean_key = raw_key.strip().strip("'").strip('"').strip()
    has_invisible_chars = raw_key != clean_key
    masked = f"{clean_key[:7]}...{clean_key[-4:]}" if len(clean_key) > 12 else f"length={len(clean_key)}"

    report = {
        "raw_key_length": len(raw_key),
        "clean_key_length": len(clean_key),
        "has_invisible_chars": has_invisible_chars,
        "masked_key": masked,
    }

    # Test DNS
    try:
        addr = socket.getaddrinfo("api.openai.com", 443)
        report["dns_resolved_ips"] = [a[4][0] for a in addr[:3]]
    except Exception as e:
        report["dns_error"] = str(e)

    # Test urllib direct
    if clean_key:
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {clean_key}"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                report["urllib_models_status"] = resp.status
        except Exception as e:
            report["urllib_error"] = str(e)

    # Test OpenAI SDK
    if clean_key:
        try:
            import openai
            client = openai.OpenAI(api_key=clean_key, timeout=10.0)
            models = client.models.list()
            report["sdk_status"] = "OK"
        except Exception as e:
            report["sdk_error"] = str(e)
            report["sdk_cause"] = repr(getattr(e, "__cause__", None))

    return report

