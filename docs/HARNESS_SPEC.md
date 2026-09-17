# Specification: Meeting Intelligence Harness v3.1 (GPT-4o & Universal MoM Reasoning)

**Date:** September 18, 2026  
**Document Version:** 3.1.0  
**Target Repo:** `meeting-notetaker-harness` (Local & Cloud on Render)  
**Board:** Monday.com C-Team Board (`5102468049`)  
**Executive Sponsor:** Mike Wong / Duong Tan  

---

## 1. Goal & Problem Statement

### The Problem
In real-world operations, Meeting Minutes (MoM) arrive in widely varying, unpredictable formats:
- Structured AI recaps (HappyScribe, Teams Copilot, Zoom AI, Otter) with sections like *Visual Direction*, *Production Workflow*, *Timeline & Coordination*, *Action Items*.
- Conversational spoken transcripts (`[00:12:04] Speaker: Text`).
- Free-form narrative emails and Word documents (`.docx`).

Under the previous v3.0 pipeline:
1. The regex line parser (`email_parser.py`) only recognized `Speaker: Text`, causing rich top sections (*Visual Direction*, *Workflow*, *Timeline*) to be collapsed into a single `Unknown` utterance or discarded.
2. `gpt-4o-mini` suffered from tunnel vision, extracting only the short bullet points at the very end of the email while losing technical constraints and context.
3. Timeline milestones ("tomorrow", "1-2 days", "Sept 30") in narrative sections failed to cross-correlate with Action Items, resulting in empty Due Dates for 7 out of 8 tasks.
4. On cloud restart, Render's user directory cache started at 0, causing the strict security check in `user_resolver.py` to reject all known aliases (Tan, Emmy, Alexa) and default every task to `⚡ [Needs Review]` assigned to Duong Tan.
5. Workstream was hardcoded to `Automation, Delivery & Reliability`, misclassifying creative video TVC projects.

### The Objective
Upgrade the harness to **v3.1** with:
1. **OpenAI GPT-4o Flagship Reasoning:** Capable of complex multi-hop synthesis, cross-referencing narrative timelines with action items, and extracting deep technical context.
2. **MoM-Aware Dual Ingestion:** Preserving full document hierarchy (headings, bullet points, narrative paragraphs) alongside spoken dialogue transcripts.
3. **Automatic Startup Directory Hydration:** Self-healing user cache in `app.py` lifespan that fetches active Monday users on every Render container boot.
4. **Dynamic Workstream & Domain Inference:** Classifying meetings into real Monday board workstreams (*TVC & Creative Video Production*, *Digital Marketing*, *Operations*, etc.).
5. **Team Knowledge Base Injection:** Injecting the official 13-member Striking team directory into the system prompt for deterministic assignee resolution.
6. **Zero Hallucination & Cost Guardrails:** Preserving Verbatim Grounding (G2), Anti-Fabricated Due Date (G4), and strict budget caps ($0.50/meeting, $5.00/day).

---

## 2. Inputs & Outputs

### Inputs
- **Raw RFC 822 MIME emails** received from Google Apps Script (`duonghientan.02@gmail.com`).
- **Body Formats:** Plain text, HTML tables/lists, inline summaries, and attached Word files (`.docx`).
- **Meeting Content:** Multilingual (English, Vietnamese, Chinese / Cantonese zh-HK).

### Outputs
- **Monday.com Items** created in `NEW — Cross-Department Intake` (`group_mm6w83gk`) on Board `5102468049`.
  - **Item Name:** `⚡ [Assignee Name] Task Title` (e.g. `⚡ [Alexa Chan] Send updated script and audio materials`).
  - **Owner (`multiple_person_mm523asb`):** Exact verified Monday User ID.
  - **Due Date (`date_mm6v7v2x`):** Explicit or relative date calculated from meeting reference date.
  - **Priority (`color_mm6cawvp`):** High / Medium / Low.
  - **Workstream (`text_mm6c9xj9`):** Dynamically inferred (e.g. `TVC & Creative Video Production`).
  - **Status (`color_mm5ken0m`):** `Not Started`.
  - **Rich HTML Update Thread:** Full context quote, technical guidelines, phase category, and recording URL.
- **Persistent State:** SQLite database entries for meeting records, task items, cost ledger, and audit events.

---

## 3. Worker Roles & Architecture Pipeline

```
[ Email: [MoM] Ingestion ]
            │ (Raw RFC 822 MIME)
            ▼
[ Stage 1: Dual-Mode Ingestion Parser ]
    ├── Mode A: Conversational Spoken Transcript (Timestamped utterances)
    └── Mode B: Thematic MoM Document (Preserves Headings, Bullet Points, Narrative)
            │
            ▼
[ Stage 2: Startup Directory Hydration ]
    └── SQLite active_users cache pre-seeded at boot via Monday GraphQL
            │
            ▼
[ Stage 3: GPT-4o Multi-Hop Reasoning Engine ]
    ├── System Prompt with Embedded Striking Team Directory
    ├── Pass 1: Classify Workstream & Synthesize Executive Context
    ├── Pass 2: Cross-Correlate Timeline & Map Action Items
    └── Pass 3: Extract Verbatim Spoken Quotes & Technical Constraints
            │
            ▼
[ Stage 4: Quality & Cost Gates (G1 - G8) ]
    ├── G1: Imperative Verb & Substantive Title (≥ 3 words, ≥ 10 chars)
    ├── G2: Verbatim Grounding (Quote verified in source text)
    ├── G3: Real Commitment Filter (Discards chit-chat/etiquette)
    ├── G4: Anti-Fabricated Due Date Check
    ├── G5: 3-Tier Entity Resolution Sanity (No phantom IDs)
    ├── G6: SHA-256 Idempotency Check
    ├── G7: Budget Cap ($0.50/meeting, $5.00/day)
    └── G8: Volume Cap (≤ 15 items/meeting)
            │
            ▼
[ Stage 5: Monday.com Syncer & DLQ Protection ]
    └── GraphQL Mutation with Dynamic Workstream, Assignee, and Rich HTML Context
```

---

## 4. Cost-Sensitive Steps & Budget Control

| Step | Provider | Model | Typical Usage | Cost per Meeting | Gate / Cap |
|---|---|---|---|---|---|
| Semantic Extraction & Reasoning | OpenAI | `gpt-4o` | ~2,500 input / ~1,200 output tokens | ~$0.015 – $0.025 USD (~350–600 VNĐ) | Cap: **$0.50 / meeting** |
| Fallback Simulator | Local Python | Hermetic | 0 tokens | $0.000 USD | Activated if API key missing |
| Daily Spending Limit | System-wide | All | Max ~100 meetings/day | Max **$5.00 USD / day** | Hard cutoff with DLQ alert |

---

## 5. System Prompt Specification (CoT & Team Directory)

The system prompt in `llm_extractor.py` will be upgraded to enforce:

1. **Embedded Team Knowledge Base:**
   - Mike Wong (`103551084`): Executive Sponsor / Director
   - Alexa Chan (`103982652`): Producer / Project Coordinator / Assets
   - Emmy Chan (`112035594`): Lead Visual Artist / Character Illustrator
   - Duong Tan (`113703761`): Lead Integration Engineer / Video Editor / Technical Lead
   - Leah Kung (`113704803`): Meeting Coordinator / Operations
   - Thossapong (TT) (`113703758`): Thailand Marketing Lead
   - Jerry Chong (`107995985`): Team Member / Reviewer
   - Wanlee Ng (`103982654`): Team Member
   - Wayne Chan (`103982655`): Team Member
2. **Compound & Group Assignee Handling:**
   - `Alexa/team` ➔ Primary: `Alexa Chan`, Collaboration Note: `Team`.
   - `Tan and Emmy` ➔ Split into individual sub-commitments or assign primary with co-owner.
   - `All team members` / `Team` ➔ Assign to Project Lead (`Duong Tan`) with group indicator.
3. **Cross-Sectional Timeline Correlation:**
   - If an action item mentions "Send draft" and the Timeline section states "Tan will send draft tomorrow", the action item's `due_date` MUST be resolved to tomorrow's date.
4. **Dynamic Workstream Inference:**
   - Detect domain: `TVC & Creative Video Production`, `Digital Marketing & Social Ads`, `Automation, Delivery & Reliability`, `Operations & Team Coordination`.

---

## 6. Success Criteria & Verification Matrix

| Test Case | Scenario | Expected Outcome |
|---|---|---|
| **TC-1: User Cache Auto-Hydration** | FastAPI startup with empty DB | `active_users_cached` automatically increases to 13 on boot. |
| **TC-2: Thematic MoM Parsing** | Input user's TVC MoM (HappyScribe format) | Full document ingested; visual & audio context preserved. |
| **TC-3: Assignee Precision** | Parse Tan, Emmy, Alexa, Jerry, Alexa/team | Resolved to exact IDs (`113703761`, `112035594`, `103982652`, `107995985`); 0 `[Needs Review]`. |
| **TC-4: Timeline Cross-Correlation** | "Tan draft tomorrow", "Checkpoint Sept 30" | Tan due date = Tomorrow; Emmy = +2 days; All team = `2026-09-30`. |
| **TC-5: Workstream Accuracy** | TVC meeting recap | Workstream set to `TVC & Creative Video Production`, not hardcoded Automation. |
| **TC-6: Verbatim Grounding (G2)** | Quotes from Visual & Action sections | G2 check passes 100% against source document. |
| **TC-7: Budget Ledger (G7)** | `gpt-4o` API execution | Cost ledger records ~$0.018 USD; below $0.50 cap. |
| **TC-8: Full Unit Test Suite** | `python -m unittest discover` | 32/32 existing tests + new regression tests green. |
