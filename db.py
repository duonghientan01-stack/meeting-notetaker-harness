# -*- coding: utf-8 -*-
"""Database layer for Meeting Intelligence Harness using SQLite with WAL mode and foreign keys."""
import json
import sqlite3
import datetime
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path
try:
    from . import config
except (ImportError, ValueError):
    import config

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or config.SQLITE_DB_PATH
    conn = sqlite3.connect(str(target_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # F8 & Harness Hardening: Enforce foreign keys, WAL mode, and busy timeout
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn

def init_db(db_path: Optional[Path] = None):
    """Initialize all harness database tables."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users_cache (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        aliases TEXT, -- JSON array of strings
        is_guest INTEGER DEFAULT 0,
        enabled INTEGER DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        meeting_id TEXT PRIMARY KEY,
        title TEXT,
        platform TEXT,
        started_at TEXT,
        ended_at TEXT,
        timezone TEXT DEFAULT 'Asia/Hong_Kong',
        recording_url TEXT,
        raw_payload TEXT,
        content_hash TEXT UNIQUE,
        status TEXT,
        created_at TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        meeting_id TEXT NOT NULL,
        idempotency_key TEXT,
        title TEXT NOT NULL,
        description TEXT,
        raw_assignee TEXT,
        resolved_user_id INTEGER,
        resolved_name TEXT,
        resolved_email TEXT,
        resolution_tier TEXT,
        due_date TEXT,
        due_date_source TEXT,
        priority TEXT,
        workstream TEXT,
        context_quote TEXT,
        confidence_score REAL,
        quality_gate_passed INTEGER,
        approval_status TEXT DEFAULT 'PENDING_APPROVAL', -- PENDING_APPROVAL, APPROVED, REJECTED
        monday_item_id TEXT,
        status TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
        UNIQUE(meeting_id, idempotency_key)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cost_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        model TEXT,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_usd REAL NOT NULL,
        timestamp TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        meeting_id TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL, -- PENDING, APPROVED_ALL, APPROVED_SELECTED, REJECTED
        digest_payload TEXT NOT NULL, -- JSON
        approved_task_ids TEXT, -- JSON list
        rejected_task_ids TEXT, -- JSON list
        reviewer_notes TEXT,
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        FOREIGN KEY(meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events_log (
        event_id TEXT PRIMARY KEY,
        correlation_id TEXT,
        event_type TEXT NOT NULL,
        status TEXT NOT NULL,
        payload TEXT,
        error_str TEXT,
        timestamp TEXT NOT NULL
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dlq (
        dlq_id TEXT PRIMARY KEY,
        task_id TEXT,
        meeting_id TEXT,
        failure_reason TEXT NOT NULL,
        payload TEXT,
        retry_count INTEGER DEFAULT 0,
        status TEXT NOT NULL, -- PENDING_REVIEW, RETRIED, RESOLVED, DISCARDED
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolution_notes TEXT
    );
    """)
    # Run backward-compatible schema migrations for existing databases
    def ensure_column(table: str, col_name: str, col_type: str):
        cursor.execute(f"PRAGMA table_info({table})")
        existing_cols = [row["name"] for row in cursor.fetchall()]
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")

    ensure_column("users_cache", "is_guest", "INTEGER DEFAULT 0")
    ensure_column("users_cache", "enabled", "INTEGER DEFAULT 1")
    ensure_column("tasks", "idempotency_key", "TEXT")
    ensure_column("tasks", "due_date_source", "TEXT DEFAULT 'absent'")
    ensure_column("tasks", "approval_status", "TEXT DEFAULT 'PENDING_APPROVAL'")
    ensure_column("meetings", "timezone", "TEXT DEFAULT 'Asia/Hong_Kong'")
    ensure_column("dlq", "resolution_notes", "TEXT")
    
    conn.commit()
    conn.close()

def upsert_user(user_id: int, name: str, email: str, aliases: Optional[List[str]] = None,
                is_guest: bool = False, enabled: bool = True, db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)
    
    with conn:
        conn.execute("""
        INSERT INTO users_cache (id, name, email, aliases, is_guest, enabled, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            email=excluded.email,
            aliases=excluded.aliases,
            is_guest=excluded.is_guest,
            enabled=excluded.enabled,
            updated_at=excluded.updated_at
        """, (user_id, name, email, aliases_json, 1 if is_guest else 0, 1 if enabled else 0, now))
    conn.close()

def get_all_users(active_only: bool = True, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    query = "SELECT id, name, email, aliases, is_guest, enabled, updated_at FROM users_cache"
    if active_only:
        query += " WHERE enabled = 1"
    cursor.execute(query)
    rows = cursor.fetchall()
    users = []
    for r in rows:
        users.append({
            "id": r["id"],
            "name": r["name"],
            "email": r["email"],
            "aliases": json.loads(r["aliases"]) if r["aliases"] else [],
            "is_guest": bool(r["is_guest"]),
            "enabled": bool(r["enabled"]),
            "updated_at": r["updated_at"]
        })
    conn.close()
    return users

def get_meeting_by_hash(content_hash: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM meetings WHERE content_hash = ?", (content_hash,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_meeting(meeting_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_recent_meetings(limit: int = 10, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT meeting_id, title, status, created_at FROM meetings ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_meeting(meeting: Dict[str, Any], db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    raw_payload_json = json.dumps(meeting.get("raw_payload", {}), ensure_ascii=False)
    
    with conn:
        conn.execute("""
        INSERT INTO meetings (
            meeting_id, title, platform, started_at, ended_at, timezone, recording_url,
            raw_payload, content_hash, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(meeting_id) DO UPDATE SET
            title=excluded.title,
            status=excluded.status
        """, (
            meeting["meeting_id"],
            meeting.get("title", "Untitled Meeting"),
            meeting.get("platform", "Microsoft Teams"),
            meeting.get("started_at"),
            meeting.get("ended_at"),
            meeting.get("timezone", config.DEFAULT_TIMEZONE),
            meeting.get("recording_url"),
            raw_payload_json,
            meeting.get("content_hash"),
            meeting.get("status", "INGESTED"),
            meeting.get("created_at", now)
        ))
    conn.close()

def save_task(task: Dict[str, Any], db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    with conn:
        conn.execute("""
        INSERT INTO tasks (
            task_id, meeting_id, idempotency_key, title, description, raw_assignee,
            resolved_user_id, resolved_name, resolved_email, resolution_tier,
            due_date, due_date_source, priority, workstream, context_quote, confidence_score,
            quality_gate_passed, approval_status, monday_item_id, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            resolved_user_id=excluded.resolved_user_id,
            resolved_name=excluded.resolved_name,
            resolved_email=excluded.resolved_email,
            resolution_tier=excluded.resolution_tier,
            due_date=excluded.due_date,
            due_date_source=excluded.due_date_source,
            approval_status=excluded.approval_status,
            monday_item_id=excluded.monday_item_id,
            status=excluded.status
        """, (
            task["task_id"],
            task["meeting_id"],
            task.get("idempotency_key"),
            task["title"],
            task.get("description", ""),
            task.get("raw_assignee", ""),
            task.get("resolved_user_id"),
            task.get("resolved_name"),
            task.get("resolved_email"),
            task.get("resolution_tier"),
            task.get("due_date"),
            task.get("due_date_source", "absent"),
            task.get("priority", "Medium"),
            task.get("workstream", config.DEFAULT_WORKSTREAM),
            task.get("context_quote", ""),
            task.get("confidence_score", 0.0),
            1 if task.get("quality_gate_passed", False) else 0,
            task.get("approval_status", "PENDING_APPROVAL"),
            task.get("monday_item_id"),
            task.get("status", "EXTRACTED"),
            task.get("created_at", now)
        ))
    conn.close()

def get_tasks_for_meeting(meeting_id: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE meeting_id = ?", (meeting_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_task_by_idempotency_key(meeting_id: str, idempotency_key: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE meeting_id = ? AND idempotency_key = ?", (meeting_id, idempotency_key))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def record_cost(meeting_id: str, stage: str, cost_usd: float,
                model: Optional[str] = None, input_tokens: int = 0, output_tokens: int = 0,
                db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute("""
        INSERT INTO cost_ledger (meeting_id, stage, model, input_tokens, output_tokens, cost_usd, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (meeting_id, stage, model, input_tokens, output_tokens, cost_usd, now))
    conn.close()

def get_meeting_cost_usd(meeting_id: str, db_path: Optional[Path] = None) -> float:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM cost_ledger WHERE meeting_id = ?", (meeting_id,))
    row = cursor.fetchone()
    conn.close()
    return float(row["total"]) if row else 0.0

def get_daily_cost_usd(day_str: Optional[str] = None, db_path: Optional[Path] = None) -> float:
    """Compute total cost in USD for a given day (default: today in UTC)."""
    target_day = day_str or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM cost_ledger WHERE timestamp LIKE ?", (f"{target_day}%",))
    row = cursor.fetchone()
    conn.close()
    return float(row["total"]) if row else 0.0

def save_approval(approval: Dict[str, Any], db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute("""
        INSERT INTO approvals (
            approval_id, meeting_id, token, status, digest_payload,
            approved_task_ids, rejected_task_ids, reviewer_notes, created_at, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(approval_id) DO UPDATE SET
            status=excluded.status,
            approved_task_ids=excluded.approved_task_ids,
            rejected_task_ids=excluded.rejected_task_ids,
            reviewer_notes=excluded.reviewer_notes,
            reviewed_at=excluded.reviewed_at
        """, (
            approval["approval_id"],
            approval["meeting_id"],
            approval["token"],
            approval.get("status", "PENDING"),
            json.dumps(approval.get("digest_payload", {}), ensure_ascii=False),
            json.dumps(approval.get("approved_task_ids", []), ensure_ascii=False),
            json.dumps(approval.get("rejected_task_ids", []), ensure_ascii=False),
            approval.get("reviewer_notes"),
            approval.get("created_at", now),
            approval.get("reviewed_at")
        ))
    conn.close()

def get_approval_by_token(token: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM approvals WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["digest_payload"] = json.loads(d["digest_payload"]) if d["digest_payload"] else {}
    d["approved_task_ids"] = json.loads(d["approved_task_ids"]) if d["approved_task_ids"] else []
    d["rejected_task_ids"] = json.loads(d["rejected_task_ids"]) if d["rejected_task_ids"] else []
    return d

def log_event(event: Dict[str, Any], db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload_json = json.dumps(event.get("payload", {}), ensure_ascii=False)
    
    with conn:
        conn.execute("""
        INSERT INTO events_log (
            event_id, correlation_id, event_type, status, payload, error_str, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event.get("event_id", f"evt_{uuid.uuid4().hex[:10]}"),
            event.get("correlation_id"),
            event["event_type"],
            event["status"],
            payload_json,
            event.get("error_str"),
            event.get("timestamp", now)
        ))
    conn.close()

def push_to_dlq(failure_reason: str, payload: Dict[str, Any],
                dlq_id: Optional[str] = None,
                task_id: Optional[str] = None, meeting_id: Optional[str] = None,
                db_path: Optional[Path] = None) -> str:
    """Push a failed item to DLQ with unique UUID to prevent collision on retries (F8/B9)."""
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload_json = json.dumps(payload, ensure_ascii=False)
    actual_dlq_id = dlq_id or f"dlq_{uuid.uuid4().hex}"
    
    with conn:
        conn.execute("""
        INSERT INTO dlq (
            dlq_id, task_id, meeting_id, failure_reason, payload, retry_count, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 0, 'PENDING_REVIEW', ?)
        """, (actual_dlq_id, task_id, meeting_id, failure_reason, payload_json, now))
    conn.close()
    return actual_dlq_id

def get_dlq_items(status: Optional[str] = "PENDING_REVIEW", db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM dlq WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cursor.execute("SELECT * FROM dlq ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        except Exception:
            pass
        items.append(d)
    return items

def update_dlq_status(dlq_id: str, status: str, resolution_notes: Optional[str] = None,
                      increment_retry: bool = False, db_path: Optional[Path] = None) -> bool:
    conn = get_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        if increment_retry:
            cursor = conn.execute("""
            UPDATE dlq SET
                status = ?,
                retry_count = retry_count + 1,
                resolved_at = CASE WHEN ? IN ('RESOLVED', 'DISCARDED') THEN ? ELSE resolved_at END,
                resolution_notes = COALESCE(?, resolution_notes)
            WHERE dlq_id = ?
            """, (status, status, now, resolution_notes, dlq_id))
        else:
            cursor = conn.execute("""
            UPDATE dlq SET
                status = ?,
                resolved_at = CASE WHEN ? IN ('RESOLVED', 'DISCARDED') THEN ? ELSE resolved_at END,
                resolution_notes = COALESCE(?, resolution_notes)
            WHERE dlq_id = ?
            """, (status, status, now, resolution_notes, dlq_id))
    affected = cursor.rowcount
    conn.close()
    return affected > 0
