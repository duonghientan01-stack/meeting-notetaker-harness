# Meeting Intelligence Harness (v3.0.0)

Enterprise Meeting Notetaker & Action Item Intelligence Pipeline.
Bridges **Happy Scribe / Microsoft Teams / Gmail MoM** meeting records with **Monday.com** Board `C-Team` (`5102468049`).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

---

## 🏗️ Architecture & 24/7 Cloud Pipeline

```
[ Meeting Concludes / MoM Sent ]
               │
               ▼
[ Gmail: duonghientan.02@gmail.com ]
               │ (Trigger every minute)
               ▼
[ Google Apps Script Cloud Bot ]
               │ (HTTP POST raw RFC 822 MIME)
               ▼
[ Render Cloud Service: meeting-notetaker-harness ]
   ├── Fast Ingestion & MIME Parsing (handles inline text, RFC 2047 subjects, & .docx Word attachments)
   ├── OpenAI GPT-4o-mini Semantic Extraction (verbatim quotes, zero-hallucination deadlines)
   ├── Multi-Tier Entity Resolution (matches names, aliases, possessives like "Tan's" to Monday User IDs)
   └── Quality & Cost Gate (G1–G8 validation, budget protection ~$0.001 - $0.003/meeting)
               │
               ▼ (GraphQL Mutation)
[ Monday.com Board: C-Team (5102468049) ]
   └── Group: NEW — Cross-Department Intake (group_mm6w83gk)
        ├── Owner (Assignee): Exact assignee (Leah, Tan, Mike) or fallback [Needs Review]
        ├── 👀 Monitor: Dedicated reviewer/coordinator (Duong Tan)
        ├── Due Date, Priority, Workstream, Status ('Not Started')
        └── HTML Update Thread: Grounded speech quote & verbatim evidence
               │
               ▼ (Human Review / Triage)
[ Reviewer moves task to: Leah's task / Tan's task / TT task ]
```

---

## 🚀 Key Features

1. **24/7 Autonomous Ingestion:**
   - **Google Apps Script Integration:** Continuously monitors unread `[MoM]` / `MOM` emails on Google Cloud and dispatches raw emails to Render.
   - **Multi-Format Extraction:** Automatically parses inline email text, HTML, and extracts XML text from attached `.docx` files via zero-dependency `zipfile` + `xml.etree.ElementTree`.

2. **AI Action Item Extraction (OpenAI GPT-4o-mini):**
   - Extracts concrete action items, owners, and explicit deadlines.
   - **Verbatim Grounding:** Links every item to an exact spoken quote from the meeting minutes.
   - **Cost-Optimized:** Average execution cost is only **$0.001 – $0.003 USD** per meeting.

3. **Dedicated Staging Intake Workflow (Option 1):**
   - Directly publishes all AI-extracted tasks into **`NEW — Cross-Department Intake`** (`group_mm6w83gk`) on Board `5102468049`.
   - Prevents mixing unreviewed tasks into active workstreams.
   - Enables a 2-step human triage: Review evidence ➔ Click **Move to Group** (`Leah's task`, `Tan's task`, `TT task`).

4. **Robust Entity Resolution:**
   - Pre-mapped Monday IDs: Mike Wong (`103551084`), Leah (`113704803`), Duong Tan (`113703761`), TT (`113703758`), Alexa Chan (`103982652`), Emmy Chan (`112035594`).
   - English possessive handling (`Tan's task` ➔ Duong Tan, `Leah's task` ➔ Leah).
   - Standardized English prefix labels: `⚡ [Assignee Name] Task Title` or `⚡ [Needs Review] Task Title`.

5. **Enterprise Reliability & Observability:**
   - Dead-Letter Queue (DLQ) prevents message dropping on transient errors.
   - Idempotent task creation prevents duplicate posts.
   - Observability endpoints report daily spend, active cached users, and DLQ status.

---

## 📡 Live API Endpoints

- `GET /health` - Service health, daily AI spend ledger, and database connection.
- `GET /api/v1/meetings` - List recently processed meetings.
- `GET /api/v1/meetings/{meeting_id}` - Retrieve meeting details and extracted tasks.
- `POST /api/v1/ingest/email` - Ingest raw RFC 822 MIME emails (called by Google Apps Script).
- `POST /api/v1/webhooks/happyscribe` - Happy Scribe webhook ingestion with HMAC-SHA256 signature verification.
- `GET /api/v1/dlq` - Inspect Dead-Letter Queue items.
- `POST /api/v1/dlq/{id}/retry` - Retry a failed item from the DLQ.

---

## 🧪 Testing & Verification

Run the full automated test suite:
```bash
python -m unittest discover -s tests -p "test_*.py"
```
*Current status: 32/32 unit tests passing.*
