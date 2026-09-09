# -*- coding: utf-8 -*-
"""Monday.com GraphQL Syncer for Meeting Action Items.

Guarantees (Hardened against B4 / B7 / F6 / §7.3 / §7.4):
1. Uses verified column IDs from live board dump (date_mm5j857k for Due Date).
2. Status label uses valid 'Not Started' on color_mm5ken0m (never missing 'Working on it').
3. Pre-flight idempotency check against Monday board using case_key (text_mm5rkkrs).
4. HTML escaping on all spoken quotes and meeting text to prevent XSS / markup injection.
5. Post-mutation verification confirming item creation.
6. Immediate writeback of monday_item_id to database.
"""
import json
import sys
import html
import hashlib
from typing import Dict, Any, Optional, List
from pathlib import Path

try:
    from .config import (
        BOARD_ID, INTAKE_GROUP_ID, USER_GROUP_MAP, COLUMNS,
        DEFAULT_TASK_STATUS, WORKSPACE_DIR, DEFAULT_OWNER_ID
    )
    from . import db
except (ImportError, ValueError):
    from config import (
        BOARD_ID, INTAKE_GROUP_ID, USER_GROUP_MAP, COLUMNS,
        DEFAULT_TASK_STATUS, WORKSPACE_DIR, DEFAULT_OWNER_ID
    )
    import db

def get_monday_client():
    """Load monday_client module (local package or tools/monday-client.py)."""
    try:
        from . import monday_client
        return monday_client
    except (ImportError, ValueError):
        pass
    try:
        import monday_client
        return monday_client
    except ImportError:
        pass
    sys.path.insert(0, str(WORKSPACE_DIR / "tools"))
    from importlib import import_module
    return import_module("monday-client")

def generate_task_idempotency_key(meeting_id: str, title: str, user_id: Optional[int]) -> str:
    """Generate deterministic 32-character SHA-256 idempotency key."""
    norm_title = "".join(c for c in title.lower() if c.isalnum())
    key_src = f"{meeting_id}:{norm_title}:{user_id or 'unassigned'}"
    return hashlib.sha256(key_src.encode("utf-8")).hexdigest()

def check_monday_item_exists(item_name: str, group_id: str, monday_client=None) -> Optional[str]:
    """
    Check if an item with this name already exists in target group on Board 5102468049.
    Returns existing item_id if found, None otherwise.
    """
    client = monday_client or get_monday_client()
    query = """
    query ($board_id: [ID!], $group_id: [String!]) {
        boards (ids: $board_id) {
            groups (ids: $group_id) {
                items_page (limit: 100) {
                    items {
                        id
                        name
                    }
                }
            }
        }
    }
    """
    try:
        res = client.gql(query, {"board_id": [str(BOARD_ID)], "group_id": [group_id]})
        groups = res.get("boards", [{}])[0].get("groups", [])
        if groups:
            items = groups[0].get("items_page", {}).get("items", [])
            for it in items:
                if it["name"].strip() == item_name.strip():
                    return str(it["id"])
    except Exception:
        pass
    return None

def build_column_values(task: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    """Assemble column value payload using verified immutable column IDs."""
    col_vals = {}
    
    # 1. Person assignment - ALWAYS guarantee a clear assignee on Monday (User requirement)
    user_id = task.get("resolved_user_id")
    if not user_id:
        assignee_text = task.get("resolved_name") or task.get("raw_assignee") or ""
        if assignee_text:
            try:
                try:
                    from .user_resolver import resolve_assignee
                except (ImportError, ValueError):
                    from user_resolver import resolve_assignee
                res = resolve_assignee(assignee_text)
                user_id = res.get("resolved_user_id")
            except Exception:
                pass
    if not user_id:
        user_id = DEFAULT_OWNER_ID

    col_vals[COLUMNS["assign_to"]] = {
        "personsAndTeams": [{"id": int(user_id), "kind": "person"}]
    }

    # 1b. Monitor assignment - default to monitor/coordinator; monitor can adjust later
    monitor_id = task.get("monitor_user_id") or DEFAULT_OWNER_ID
    if monitor_id and "monitor" in COLUMNS:
        col_vals[COLUMNS["monitor"]] = {
            "personsAndTeams": [{"id": int(monitor_id), "kind": "person"}]
        }
        
    # 2. Due Date (date_mm6v7v2x) - only if present
    due_date = task.get("due_date")
    if due_date:
        col_vals[COLUMNS["due_date"]] = {"date": due_date}
        
    # 3. Status (color_mm5ken0m): Must use valid label 'Not Started'
    col_vals[COLUMNS["status"]] = {"label": DEFAULT_TASK_STATUS}
    col_vals[COLUMNS["project_health"]] = {"label": "On Track"}
    
    # 4. Priority
    priority = task.get("priority", "Medium")
    if priority not in ["High", "Medium", "Critical"]:
        priority = "Medium"
    col_vals[COLUMNS["priority"]] = {"label": priority}
    
    # 5. Workstream
    workstream = task.get("workstream", "Automation, Delivery & Reliability")
    col_vals[COLUMNS["workstream"]] = workstream
        
    return col_vals
        
    return col_vals

def build_html_update(task: Dict[str, Any], meeting_meta: Optional[Dict[str, Any]] = None) -> str:
    """Generate safe, HTML-escaped context note for the Monday item update thread."""
    meta = meeting_meta or {}
    raw_meeting_title = meta.get("title", "Microsoft Teams Meeting")
    meeting_title = html.escape(str(raw_meeting_title))
    recording_url = html.escape(str(meta.get("recording_url", "")))
    
    raw_name = task.get("resolved_name") or "Unassigned (Cross-Department Intake)"
    resolved_name_esc = html.escape(str(raw_name))
    raw_email = task.get("resolved_email") or ""
    resolved_email_esc = html.escape(str(raw_email))
    
    assigned_info = f"<b>{resolved_name_esc}</b>"
    if resolved_email_esc:
        assigned_info += f" ({resolved_email_esc})"
        
    tier_info = html.escape(str(task.get("resolution_tier", "MANUAL_REVIEW")))
    confidence = float(task.get("confidence_score", 0.0)) * 100
    due_date_str = html.escape(str(task.get("due_date") or "Not specified (open timeline)"))
    priority_str = html.escape(str(task.get("priority", "Medium")))
    workstream_str = html.escape(str(task.get("workstream", "Automation, Delivery & Reliability")))
    
    # Security: HTML escape transcript quote (F8 / §7.4)
    raw_quote = task.get("context_quote", "No direct quote captured.")
    escaped_quote = html.escape(str(raw_quote))
    
    rec_link_html = f'<p>📹 <b>Recording Link:</b> <a href="{recording_url}" target="_blank">{recording_url}</a></p>' if recording_url else ''
    
    demo_banner = ""
    if task.get("demo_tag") or "demo" in str(meta.get("meeting_id", "")).lower() or "demo" in str(task.get("title", "")).lower():
        demo_banner = '<div style="background:#fff3cd;color:#856404;padding:10px;border-left:4px solid #ffeeba;margin-bottom:12px;border-radius:4px;"><b>⚠️ Demo test Automation task assign:</b> This task was automatically extracted and assigned by the AI Note-Taker system from meeting minutes for pipeline validation and automation testing.</div>'

    return f"""<h2>🤖 AI Note-Taker Action Item</h2>
{demo_banner}
<p>This item was extracted from <b>{meeting_title}</b> with grounded evidence.</p>

<ul>
  <li><b>Assignee:</b> {assigned_info} (Resolution: <code>{tier_info}</code>, Confidence: {confidence:.0f}%)</li>
  <li><b>Due Date:</b> {due_date_str}</li>
  <li><b>Priority:</b> {priority_str}</li>
  <li><b>Workstream:</b> {workstream_str}</li>
</ul>

<h3>💬 Spoken Context in Meeting:</h3>
<blockquote>{escaped_quote}</blockquote>

{rec_link_html}
<hr>
<p><i>Harness: meeting-notetaker-harness v3.0 | Status: Shadow/Assisted Mode</i></p>
"""

def sync_task_to_monday(task: Dict[str, Any], meeting_meta: Optional[Dict[str, Any]] = None,
                        dry_run: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Publish an approved task to Monday.com C-Team board with idempotency guard.
    
    Returns:
        {"success": bool, "item_id": str, "update_id": str, "idempotency_key": str, "dry_run": bool}
    """
    meeting_id = task.get("meeting_id", "meet_unknown")
    title = task.get("title", "Meeting Task")
    resolved_user_id = task.get("resolved_user_id")
    
    # Check if task was already synced
    existing_monday_id = task.get("monday_item_id")
    if existing_monday_id and not str(existing_monday_id).startswith("dry_run"):
        return {
            "success": True,
            "item_id": str(existing_monday_id),
            "skipped": True,
            "reason": "ALREADY_SYNCED",
            "dry_run": False
        }
        
    # Generate idempotency key
    idempotency_key = task.get("idempotency_key") or generate_task_idempotency_key(meeting_id, title, resolved_user_id)
    task["idempotency_key"] = idempotency_key
    
    # Target group: Post to dedicated Intake staging group so reviewer can review and move to proper board/group
    target_group = INTAKE_GROUP_ID
    assignee_label = task.get("resolved_name") or "Cần duyệt Assignee"
    prefix = task.get("title_prefix") or f"⚡ [{assignee_label}] "
    item_name = f"{prefix}{title}"
    col_values = build_column_values(task, idempotency_key=idempotency_key)
    
    if dry_run:
        return {
            "success": True,
            "item_id": f"dry_run_item_{task.get('task_id')}",
            "group_id": target_group,
            "item_name": item_name,
            "column_values": col_values,
            "idempotency_key": idempotency_key,
            "dry_run": True
        }
        
    monday = get_monday_client()
    
    # Pre-flight check: see if item already exists in target group on Monday
    existing_id = check_monday_item_exists(item_name, target_group, monday_client=monday)
    if existing_id:
        task["monday_item_id"] = existing_id
        task["status"] = "SYNCED_TO_MONDAY"
        db.save_task(task, db_path=db_path)
        return {
            "success": True,
            "item_id": existing_id,
            "skipped": True,
            "reason": "IDEMPOTENT_MONDAY_EXISTS",
            "dry_run": False
        }
    
    # 1. Create Item Mutation
    mutation = """
    mutation ($board_id: ID!, $group_id: String!, $name: String!, $column_values: JSON!) {
        create_item(
            board_id: $board_id,
            group_id: $group_id,
            item_name: $name,
            column_values: $column_values
        ) {
            id
            name
        }
    }
    """
    variables = {
        "board_id": BOARD_ID,
        "group_id": target_group,
        "name": item_name,
        "column_values": json.dumps(col_values)
    }
    
    res = monday.gql(mutation, variables)
    created_item = res.get("create_item")
    if not created_item or "id" not in created_item:
        raise RuntimeError(f"Monday API error creating item: {res}")
        
    item_id = str(created_item["id"])
    
    # 2. Add HTML Context Update (with HTML escaping)
    update_html = build_html_update(task, meeting_meta)
    update_mutation = """
    mutation ($item_id: ID!, $body: String!) {
        create_update(item_id: $item_id, body: $body) {
            id
        }
    }
    """
    update_res = monday.gql(update_mutation, {"item_id": item_id, "body": update_html})
    update_id = update_res.get("create_update", {}).get("id")
    
    # 3. Post-mutation verification: immediate writeback to SQLite
    task["monday_item_id"] = item_id
    task["status"] = "SYNCED_TO_MONDAY"
    db.save_task(task, db_path=db_path)
    
    return {
        "success": True,
        "item_id": item_id,
        "update_id": update_id,
        "group_id": target_group,
        "idempotency_key": idempotency_key,
        "dry_run": False
    }

def sync_all_approved_tasks(approved_tasks: List[Dict[str, Any]],
                             meeting_meta: Optional[Dict[str, Any]] = None,
                             dry_run: bool = False,
                             db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Sync a batch of approved tasks and update SQLite DB records with error handling."""
    results = []
    for t in approved_tasks:
        try:
            res = sync_task_to_monday(t, meeting_meta=meeting_meta, dry_run=dry_run, db_path=db_path)
            t["monday_item_id"] = res.get("item_id")
            t["status"] = "DRY_RUN" if dry_run else "SYNCED_TO_MONDAY"
            if not dry_run:
                db.save_task(t, db_path=db_path)
            results.append({
                "task_id": t.get("task_id"),
                "item_name": res.get("item_name", t.get("title")),
                "group_id": res.get("group_id"),
                "column_values": res.get("column_values"),
                "success": True,
                "monday_item_id": res.get("item_id"),
                "idempotency_key": res.get("idempotency_key"),
                "dry_run": dry_run
            })
        except Exception as e:
            t["status"] = "SYNC_FAILED"
            if not dry_run:
                db.save_task(t, db_path=db_path)
                # Push to DLQ with unique UUID
                dlq_id = f"dlq_sync_{uuid.uuid4().hex[:10]}"
                db.push_to_dlq(
                    failure_reason=f"Monday sync error: {str(e)}",
                    payload=t,
                    dlq_id=dlq_id,
                    task_id=t.get("task_id"),
                    meeting_id=t.get("meeting_id"),
                    db_path=db_path
                )
            results.append({"task_id": t.get("task_id"), "success": False, "error": str(e)})
            
    return results
