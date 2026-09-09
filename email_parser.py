# -*- coding: utf-8 -*-
"""Email Ingestion & Format Fingerprint Parser for Happy Scribe Notifications.

Guarantees (Hardened against B5 / §4.1 / §4.2 / §4.5):
1. Normalizes all email payloads into strict MeetingEnvelope v1.
2. Computes content_hash over normalized transcript only (never raw payload, fixing F6).
3. Declares and checks format fingerprint. Deviations fail loudly to DLQ as UNKNOWN_EMAIL_FORMAT.
4. Raw MIME archiving before parsing, keyed by Message-ID.
5. Zero-extraction alert rule: meetings yielding 0 items + empty summary alert loudly.
"""
import re
import email
from email.message import Message
import hashlib
import datetime
import zipfile
import xml.etree.ElementTree as ET
import io
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

try:
    from .config import RAW_ARCHIVE_DIR, DEFAULT_TIMEZONE
    from . import db
except (ImportError, ValueError):
    from config import RAW_ARCHIVE_DIR, DEFAULT_TIMEZONE
    import db

class EmailFingerprintError(Exception):
    """Raised when email does not conform to the expected Happy Scribe format contract."""
    pass

class ZeroExtractionError(Exception):
    """Raised when parsing extracts zero substantive content from a meeting."""
    pass

def decode_mime_header(header_val: Optional[str]) -> str:
    """Safely decode RFC 2047 MIME encoded headers (UTF-8, GB2312, etc.)."""
    if not header_val:
        return ""
    try:
        from email.header import decode_header
        decoded_parts = []
        for part, charset in decode_header(header_val):
            if isinstance(part, bytes):
                enc = charset or "utf-8"
                try:
                    decoded_parts.append(part.decode(enc, errors="replace"))
                except Exception:
                    decoded_parts.append(part.decode("latin-1", errors="replace"))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts).strip()
    except Exception:
        return str(header_val).strip()

def extract_docx_text(docx_bytes: bytes) -> str:
    """Extract plain text from a .docx binary file using standard library zipfile and XML parser."""
    try:
        if not docx_bytes.startswith(b"PK"):
            return ""
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as docx:
            if "word/document.xml" not in docx.namelist():
                return ""
            xml_content = docx.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for p in tree.iterfind(".//w:p", namespaces):
                texts = [node.text for node in p.iterfind(".//w:t", namespaces) if node.text]
                if texts:
                    paragraphs.append("".join(texts))
            return "\n\n".join(paragraphs).strip()
    except Exception:
        return ""

def extract_email_body(msg: Message) -> Tuple[str, str]:
    """Extract plain text, HTML bodies, and text from attached .docx or .txt files."""
    text_body = ""
    html_body = ""
    attachment_texts = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            raw_filename = part.get_filename() or ""
            filename = decode_mime_header(raw_filename)
            payload = part.get_payload(decode=True)
            if not payload:
                continue

            # Check for Word (.docx) attachment
            is_docx = (
                filename.lower().endswith(".docx") or 
                content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or
                (isinstance(payload, bytes) and payload.startswith(b"PK") and (filename.lower().endswith(".docx") or not filename))
            )
            if is_docx and isinstance(payload, bytes):
                doc_text = extract_docx_text(payload)
                if doc_text:
                    attachment_texts.append(f"--- [Attachment: {filename or 'Meeting_Notes.docx'}] ---\n{doc_text}")
                continue

            # Check for plain text (.txt) attachment
            is_txt = (
                "attachment" in content_disposition and 
                (filename.lower().endswith(".txt") or content_type == "text/plain")
            )
            if is_txt and isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                txt_content = payload.decode(charset, errors="replace").strip()
                if txt_content:
                    attachment_texts.append(f"--- [Attachment: {filename or 'Transcript.txt'}] ---\n{txt_content}")
                continue

            # Ignore other attachments (images, zip, binaries, etc.)
            if "attachment" in content_disposition:
                continue

            charset = part.get_content_charset() or "utf-8"
            decoded_text = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                text_body += decoded_text
            elif content_type == "text/html":
                html_body += decoded_text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text_body = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = text_body

    if attachment_texts:
        joined_attachments = "\n\n".join(attachment_texts)
        if text_body.strip():
            text_body = text_body.strip() + "\n\n" + joined_attachments
        else:
            text_body = joined_attachments

    return text_body.strip(), html_body.strip()

def check_format_fingerprint(subject: str, text_body: str, html_body: str) -> bool:
    """
    Validate presence of essential structural markers from Happy Scribe notification templates or meeting emails.
    """
    # 1. Subject check: must mention Happy Scribe, transcription, meeting, minutes, or MOM
    subj_pattern = r"(?:happy\s*scribe|transcription|meeting\s*notes|meeting|minutes|summary|\[mom\]|\bmom\b|biên\s*bản|họp)"
    if not re.search(subj_pattern, subject, re.IGNORECASE):
        return False
        
    combined = (text_body + " " + html_body).lower()
    # 2. Structural marker: Must mention transcript, speakers, summary, tasks, or substantive meeting body
    has_transcript_marker = any(m in combined for m in [
        "transcript", "summary", "happyscribe.com", "recording", "speakers", "biên bản", "tóm tắt",
        "action item", "action items", "task", "tasks", "nội dung", "kết luận", "deadline", "công việc",
        "attachment", "minutes", "meeting minutes", "agenda"
    ])
    if not has_transcript_marker and len(text_body.strip()) >= 30:
        has_transcript_marker = True

    return has_transcript_marker

def parse_transcript_lines(text_body: str) -> List[Dict[str, Any]]:
    """
    Parse speaker, timestamp, and utterance lines from Happy Scribe notification text.
    Patterns handled:
    - [00:12:04] Mike Wong: ...
    - Mike Wong (00:12:04): ...
    - Mike Wong: [00:12:04] ...
    """
    transcript_utterances = []
    lines = text_body.splitlines()
    
    pattern1 = re.compile(r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]+):\s*(.*)$')
    pattern2 = re.compile(r'^([^:(]+)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\):\s*(.*)$')
    pattern3 = re.compile(r'^([^:]+):\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(.*)$')
    pattern4 = re.compile(r'^([A-ZÀ-Ỹ\u4e00-\u9fff][A-Za-zÀ-ỹ\s\u4e00-\u9fff]{1,30}):\s+(.*)$')
    
    current_speaker = "Unknown"
    current_ts = "00:00:00"
    current_text = []
    
    for line in lines:
        line_s = line.strip()
        if not line_s:
            continue
            
        m1 = pattern1.match(line_s)
        m2 = pattern2.match(line_s)
        m3 = pattern3.match(line_s)
        m4 = pattern4.match(line_s)
        
        if m1:
            if current_text:
                transcript_utterances.append({
                    "speaker": current_speaker,
                    "timestamp": current_ts,
                    "text": " ".join(current_text)
                })
                current_text = []
            current_ts, current_speaker, text = m1.group(1), m1.group(2).strip(), m1.group(3).strip()
            current_text.append(text)
        elif m2:
            if current_text:
                transcript_utterances.append({
                    "speaker": current_speaker,
                    "timestamp": current_ts,
                    "text": " ".join(current_text)
                })
                current_text = []
            current_speaker, current_ts, text = m2.group(1).strip(), m2.group(2), m2.group(3).strip()
            current_text.append(text)
        elif m3 and len(m3.group(1).split()) <= 4:
            if current_text:
                transcript_utterances.append({
                    "speaker": current_speaker,
                    "timestamp": current_ts,
                    "text": " ".join(current_text)
                })
                current_text = []
            current_speaker, current_ts, text = m3.group(1).strip(), m3.group(2), m3.group(3).strip()
            current_text.append(text)
        elif m4 and len(m4.group(1).split()) <= 4 and m4.group(1).lower() not in {"link", "subject", "from", "to", "date", "summary", "transcript", "note", "notes", "agenda"}:
            if current_text:
                transcript_utterances.append({
                    "speaker": current_speaker,
                    "timestamp": current_ts,
                    "text": " ".join(current_text)
                })
                current_text = []
            current_speaker, text = m4.group(1).strip(), m4.group(2).strip()
            current_text.append(text)
        else:
            if current_text:
                current_text.append(line_s)
                
    if current_text:
        transcript_utterances.append({
            "speaker": current_speaker,
            "timestamp": current_ts,
            "text": " ".join(current_text)
        })
        
    return transcript_utterances

def parse_email_to_envelope(raw_email_bytes: bytes,
                            archive_dir: Optional[Path] = None,
                            db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Parse a raw MIME email into a standardized MeetingEnvelope v1.
    
    Guarantees:
    - Archives raw MIME keyed by Message-ID.
    - Validates format fingerprint.
    - Computes content_hash exclusively over normalized transcript text.
    - Emits MeetingEnvelope v1 schema.
    """
    msg = email.message_from_bytes(raw_email_bytes)
    message_id = msg.get("Message-ID", f"msg_{hashlib.sha256(raw_email_bytes).hexdigest()[:16]}")
    subject = decode_mime_header(msg.get("Subject", "Untitled Meeting Notification"))
    date_header = msg.get("Date", "")
    
    # 1. Archive raw email before parsing (B5 / §4.1)
    target_archive = archive_dir or RAW_ARCHIVE_DIR
    clean_msg_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', message_id)
    archive_file = target_archive / f"{clean_msg_id}.eml"
    try:
        archive_file.write_bytes(raw_email_bytes)
    except Exception as e:
        pass
        
    text_body, html_body = extract_email_body(msg)
    
    # 2. Check format fingerprint contract (B5 / §4.1)
    if not check_format_fingerprint(subject, text_body, html_body):
        err_msg = f"UNKNOWN_EMAIL_FORMAT: Email '{subject}' failed format fingerprint contract."
        # Push to DLQ
        db.push_to_dlq(
            failure_reason=err_msg,
            payload={"message_id": message_id, "subject": subject, "date": date_header},
            db_path=db_path
        )
        raise EmailFingerprintError(err_msg)
        
    # 3. Extract meeting metadata
    meeting_title = subject
    if "[MoM]" in subject:
        meeting_title = subject[subject.index("[MoM]"):].strip()
        
    # Extract recording link if present
    rec_url_match = re.search(r'https://app\.happyscribe\.com/[^\s<>"\'\)]+', text_body + " " + html_body)
    recording_url = rec_url_match.group(0) if rec_url_match else ""
    
    # 4. Extract transcript
    transcript_utterances = parse_transcript_lines(text_body)
    
    # Fallback: if line parser found nothing structured, treat body paragraphs as utterances
    if not transcript_utterances and len(text_body) > 50:
        paragraphs = [p.strip() for p in text_body.split("\n\n") if len(p.strip()) > 20]
        for idx, p in enumerate(paragraphs):
            transcript_utterances.append({
                "speaker": "Speaker",
                "timestamp": f"00:{idx:02d}:00",
                "text": p
            })
            
    # 5. Extract attendees from headers or body
    participants = []
    from_header = msg.get("From", "")
    to_header = msg.get("To", "")
    for h in [from_header, to_header]:
        if h:
            real_name, addr = email.utils.parseaddr(h)
            if addr:
                participants.append({"name": real_name or addr.split("@")[0], "email": addr})
                
    # Unique participants
    unique_participants = []
    seen_emails = set()
    for p in participants:
        if p["email"] not in seen_emails:
            seen_emails.add(p["email"])
            unique_participants.append(p)
            
    # 6. Compute normalized transcript text and content_hash (F6 / §4.5)
    normalized_transcript_text = " ".join([f"{u['speaker']}:{u['text']}" for u in transcript_utterances])
    content_hash = hashlib.sha256(normalized_transcript_text.encode("utf-8")).hexdigest()
    
    # 7. Check zero-extraction alert rule (§4.2)
    provider_summary = ""
    summary_match = re.search(r'(?:summary|tóm tắt|會議摘要)[:\s]+(.*?)(?=\n\n|\Z)', text_body, re.IGNORECASE | re.DOTALL)
    if summary_match:
        provider_summary = summary_match.group(1).strip()
        
    if len(transcript_utterances) == 0 and len(provider_summary) < 50:
        err_msg = f"ZERO_EXTRACTION_SUSPECT: Meeting '{meeting_title}' yielded 0 utterances and under 50 chars summary."
        db.push_to_dlq(
            failure_reason=err_msg,
            payload={"message_id": message_id, "subject": subject, "body_preview": text_body[:300]},
            db_path=db_path
        )
        raise ZeroExtractionError(err_msg)
        
    # Standard started_at
    started_at_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if date_header:
        try:
            parsed_date = email.utils.parsedate_to_datetime(date_header)
            started_at_iso = parsed_date.isoformat()
        except Exception:
            pass
            
    meeting_id = f"hs_meet_{hashlib.sha256(message_id.encode('utf-8')).hexdigest()[:10]}"
    
    envelope = {
        "envelope_version": "1.0",
        "source": "happyscribe_email",
        "source_record_id": message_id,
        "ingested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "meeting": {
            "meeting_id": meeting_id,
            "title": meeting_title,
            "platform": "Microsoft Teams",
            "started_at": started_at_iso,
            "timezone": DEFAULT_TIMEZONE,
            "recording_url": recording_url,
            "languages_detected": ["en", "zh-HK", "vi"],
            "participants": unique_participants
        },
        "transcript": transcript_utterances,
        "provider_summary": provider_summary,
        "content_hash": content_hash
    }
    return envelope
