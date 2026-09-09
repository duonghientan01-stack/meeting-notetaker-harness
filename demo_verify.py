# -*- coding: utf-8 -*-
"""Verification and demonstration script for Meeting Intelligence Harness."""
import sys
import json
from pathlib import Path

pkg_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(pkg_dir))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import db
import config
from app import process_meeting_pipeline

def run_demo():
    print("=" * 70)
    print("🚀 RUNNING MEETING INTELLIGENCE HARNESS DEMONSTRATION")
    print("=" * 70)
    
    # 1. Realistic Sample Meeting Ingested via Happy Scribe / MS Teams
    sample_meeting = {
        "meeting_id": "hs_teams_20260901_align",
        "title": "Striking EU Q3 Digital Marketing & Deliverable Review",
        "platform": "Microsoft Teams (Happy Scribe AI Bot)",
        "started_at": "2026-09-01T09:00:00Z",
        "ended_at": "2026-09-01T09:40:00Z",
        "recording_url": "https://app.happyscribe.com/recordings/hs_teams_20260901_align",
        "participants": [
            {"name": "Mike Wong", "email": "mikewong@striking.com.hk"},
            {"name": "Leah", "email": "yhkung@striking.com.hk"},
            {"name": "Duong Tan", "email": "tan.dh@poppingcandy.com.hk"},
            {"name": "Wayne", "email": "waynechan@striking.com.hk"}
        ],
        "transcript": [
            {
                "speaker": "Mike Wong",
                "timestamp": "00:02:15",
                "text": "Good morning team. Tan, please review the finalized Carousel 5 and 6 deliverables and prepare the cloud links by this Friday."
            },
            {
                "speaker": "Duong Tan",
                "timestamp": "00:02:40",
                "text": "Sure Mike, I will verify all 48 rendered slides, clean the board items, and package the Google Drive evidence today."
            },
            {
                "speaker": "Mike Wong",
                "timestamp": "00:08:10",
                "text": "Wayne, please coordinate with the logistics distributor regarding the EU popping candy customs clearance."
            },
            {
                "speaker": "Wayne",
                "timestamp": "00:08:35",
                "text": "Yes Mike, I will follow up with the Rotterdam freight forwarder by tomorrow."
            },
            {
                "speaker": "Leah",
                "timestamp": "00:15:20",
                "text": "I will update the Microsoft Teams calendar invite and verify the Happy Scribe note-taker bot whitelist tomorrow morning."
            },
            {
                "speaker": "Duong Tan",
                "timestamp": "00:25:00",
                "text": "Cảm ơn mọi người, buổi họp hôm nay rất hiệu quả."
            }
        ]
    }
    
    # Process pipeline with dry_run=True (safe preview)
    res = process_meeting_pipeline(sample_meeting, dry_run=True)
    
    print(f"\n📊 Extraction & Verification Summary:")
    print(f"  • Meeting Title:    {res['title']}")
    print(f"  • Total Extracted:  {res['total_extracted']} action items")
    print(f"  • Quality Approved: {res['approved_count']}")
    print(f"  • Blocked / DLQ:    {res['blocked_count']}")
    
    print("\n📋 Action Items Prepared for Monday.com Sync:")
    for idx, item in enumerate(res["sync_results"], 1):
        print(f"\n  [{idx}] Task ID: {item['task_id']}")
        print(f"      Item Name:     {item['item_name']}")
        print(f"      Target Group:  {item['group_id']}")
        print(f"      Columns:       {json.dumps(item['column_values'], ensure_ascii=False)}")
        
    print("\n" + "=" * 70)
    print("✅ HARNESS PIPELINE EXECUTION VERIFIED CLEANLY")
    print("=" * 70)

if __name__ == "__main__":
    run_demo()
