# -*- coding: utf-8 -*-
"""Configuration for Meeting Intelligence Harness."""
import os
from pathlib import Path

# Base Paths
PACKAGE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = PACKAGE_DIR.parent
DATA_DIR = PACKAGE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SQLITE_DB_PATH = DATA_DIR / "meeting_harness.sqlite"
RAW_ARCHIVE_DIR = DATA_DIR / "raw_emails"
RAW_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# Monday Connection & Board Constants
MONDAY_ENDPOINT = "https://api.monday.com/v2"
MONDAY_FILE_ENDPOINT = "https://api.monday.com/v2/file"
ACCOUNT_ID = "35092246"
DEFAULT_OWNER_ID = 113703761  # Duong Tan
BOARD_ID = "5102468049"       # C-Team Board
SUBITEM_BOARD_ID = "5102468050"

# Target Board Groups on Board 5102468049
INTAKE_GROUP_ID = "group_mm6w83gk"  # "NEW — Cross-Department Intake"
USER_GROUP_MAP = {
    113704803: "group_mm6wyrry",  # 🟠 Leah's task
    113703761: "group_mm6w6bys",  # 🟢 Tan's task
    113703758: "group_mm6w488g",  # 🔵 TT task
}

# Immutable Column IDs for Board 5102468049 (Verified from live board query)
COLUMNS = {
    "assign_to": "multiple_person_mm523asb",      # "Owner" (people)
    "owner": "multiple_person_mm523asb",          # "Owner" (people)
    "monitor": "multiple_person_mm6w75qm",        # "👀 Monitor" (people)
    "status": "color_mm5ken0m",                  # "Status" (status)
    "project_health": "color_mm6cg534",          # "Project Health" (status)
    "priority": "color_mm6cawvp",                # "Priority" (status)
    "due_date": "date_mm6v7v2x",                 # "Due Date" (date)
    "workstream": "text_mm6c9xj9",               # "Workstream" (text)
    "decision_required": "long_text_mm6c6f9s",   # "Decision Required" (long_text)
    "kpi_impact": "long_text_mm6cgaxq",          # "KPI / Business Impact" (long_text)
    "ai_cost": "numeric_mm6cjvtr",               # "AI Cost (Credits)" (numbers)
}

# Verified Status Labels for Board 5102468049
VALID_PARENT_STATUSES = [
    "Not Started",
    "In Progress",
    "Pending Approval",
    "⏳ Waiting",
    "🟢 Active",
    "Blocked",
    "✅ Done"
]
DEFAULT_TASK_STATUS = "Not Started"

# Quality & Cost Gate Settings
MIN_CONFIDENCE_SCORE = 0.85
MAX_RETRIES = 3
BUDGET_CAP_PER_MEETING_USD = 0.50
DAILY_BUDGET_CAP_USD = 5.00
MAX_TASKS_PER_MEETING = 15
DEFAULT_WORKSTREAM = "Automation, Delivery & Reliability"
DEFAULT_TIMEZONE = "Asia/Hong_Kong"

# Webhook & Server Settings
SERVER_HOST = "0.0.0.0"
SERVER_PORT = int(os.environ.get("PORT", 8000))
# Security: No default hardcoded secret in source (B2)
WEBHOOK_SECRET = os.environ.get("HAPPYSCRIBE_WEBHOOK_SECRET", None)

# LLM Reasoning Extractor Configuration (§5.2 & §9.3)
DEFAULT_LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")  # "openai", "google", "anthropic"
DEFAULT_LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
USE_LLM_EXTRACTOR = os.environ.get("USE_LLM_EXTRACTOR", "true").lower() in ("true", "1", "yes")

# Model Pricing per 1M tokens (USD) for cost_ledger tracking
MODEL_PRICING = {
    "gemini-2.5-flash": {"input_cost_per_1m": 0.075, "output_cost_per_1m": 0.30},
    "gemini-1.5-flash": {"input_cost_per_1m": 0.075, "output_cost_per_1m": 0.30},
    "claude-3-5-haiku-20241022": {"input_cost_per_1m": 0.80, "output_cost_per_1m": 4.00},
    "gpt-4o-mini": {"input_cost_per_1m": 0.15, "output_cost_per_1m": 0.60},
    "default": {"input_cost_per_1m": 0.10, "output_cost_per_1m": 0.40}
}

# Approval workflow: false = direct sync to Monday intake group; true = await web token digest
REQUIRE_HUMAN_APPROVAL = os.environ.get("REQUIRE_HUMAN_APPROVAL", "false").lower() in ("true", "1", "yes")
