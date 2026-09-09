# Meeting Intelligence Harness

Enterprise Meeting Notetaker & Action Item Intelligence Pipeline.
Bridges **Happy Scribe / Microsoft Teams** meeting records with **Monday.com** Boards (`5102468049`).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

## Features
- **Raw Email & Webhook Ingestion**: Ingests Happy Scribe notification emails (`POST /api/v1/ingest/email`) and webhooks (`POST /api/v1/webhooks/happyscribe`).
- **Verbatim Evidence Grounding**: Extracts tasks with verbatim speech quotes and timestamps; strictly blocks hallucinations and fabricated deadlines.
- **Entity Resolution**: Resolves spoken names and aliases to Monday.com User IDs (supports CJK display names and guest users).
- **Quality & Cost Gate**: 8 verifiable safety checks (G1–G8) + per-meeting / daily budget limits.
- **Human Approval Gate**: Generates one-click approval digest links (`POST /api/v1/approvals/{token}`) before publishing.
- **Monday.com GraphQL Sync**: Idempotent task creation directly to target groups on Board `5102468049`.

## Endpoints
- `GET /health` - Service health, spending ledger, and cache freshness.
- `POST /api/v1/ingest/email` - RFC 822 MIME raw email ingestion.
- `POST /api/v1/webhooks/happyscribe` - Webhook ingestion with HMAC-SHA256 verification.
- `GET /api/v1/approvals/{token}` - View candidate tasks pending human approval.
- `POST /api/v1/approvals/{token}` - Approve selected or all tasks for Monday.com publication.
- `GET /api/v1/dlq` - Inspect Dead-Letter Queue items.
- `POST /api/v1/dlq/{id}/retry` - Retry failed task.

## Local Run
```bash
pip install -r requirements.txt
python -m uvicorn app:app --port 8000
```
