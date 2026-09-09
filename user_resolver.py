# -*- coding: utf-8 -*-
"""3-Tier Entity Resolution for mapping spoken names/emails to verified Monday.com User IDs.

Guarantees (Hardened against F4 / B1):
1. Never strips CJK characters during normalization.
2. Rejects substring matching on strings shorter than 2 characters.
3. Stop-list intercepts generic terms ("team", "everyone", "ops") -> Tier 3.
4. Unambiguous matching only: ties at the top score fall back to Tier 3 (unassigned).
5. Known aliases cannot return a phantom user absent from the active user cache.
"""
import re
import unicodedata
from typing import Dict, Any, Optional, List, Tuple, Set
from pathlib import Path
try:
    from . import db
    from .config import DEFAULT_OWNER_ID
except (ImportError, ValueError):
    import db
    from config import DEFAULT_OWNER_ID

# Known alias dictionary for Striking C-Team members
KNOWN_ALIASES = {
    103551084: ["mike", "mike wong", "boss", "mr wong", "mikewong"],
    113704803: ["leah", "leah kung", "yh kung", "yhkung", "kung"],
    113703761: ["tan", "duong tan", "duong", "tan duong", "dương tấn", "tấn", "dương", "tan.dh"],
    113703758: ["tt", "thossapong", "thossapong sasipiyanon", "thossa"],
    103982655: ["wayne", "wayne chan", "waynechan"],
    103982654: ["wanlee", "wanlee ng", "wanleeng"],
    103982652: ["alexa", "alexa chan", "alexachan"],
    108225291: ["hay son", "hayson", "yung hay son", "haysonyung"],
    112035594: ["emmy", "emmy chan", "emmychan"],
    107995985: ["jerry", "jerry chong", "jerrychong"],
    107996894: ["alex", "alex chan", "alexchan"]
}

# Generic non-person tokens that must NEVER resolve to an individual
GENERIC_STOP_WORDS: Set[str] = {
    "the team", "team", "everyone", "everybody", "all", "ops", "operations",
    "someone", "somebody", "anyone", "we", "us", "management", "dev", "marketing",
    "bạn", "mọi người", "cả team", "các bạn", "ai đó", "người nào đó", "nhóm",
    "大家", "團隊", "同事", "某人", "有人", "運營"
}

def remove_accents(input_str: str) -> str:
    """Normalize and strip Latin / Vietnamese diacritics while preserving CJK characters."""
    if not input_str:
        return ""
    # Normalize full-width/half-width characters to standard forms
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    # Strip combining diacritical marks (e.g., accents on e, a, o)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).strip()

def normalize_text(text: str) -> str:
    """
    Unicode-safe normalization:
    - Normalizes accents and Unicode forms.
    - Preserves CJK ideographs and alphanumeric characters.
    - Strips punctuation and collapses whitespace.
    """
    if not text:
        return ""
    clean = remove_accents(text)
    # Keep Unicode word characters (\w includes CJK unified ideographs) and spaces
    clean = re.sub(r'[^\w\s]', ' ', clean, flags=re.UNICODE)
    clean = clean.lower()
    return re.sub(r'\s+', ' ', clean).strip()

def resolve_assignee(raw_assignee: str, email: Optional[str] = None,
                     db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Resolve spoken name or email to a verified Monday User ID using 3-tier matching.
    
    Returns:
        {
            "resolved_user_id": int | None,
            "resolved_name": str | None,
            "resolved_email": str | None,
            "confidence": float,
            "tier": "TIER_1_EMAIL" | "TIER_2_FUZZY_NAME" | "TIER_3_FALLBACK"
        }
    """
    users = db.get_all_users(active_only=True, db_path=db_path)
    
    # -------------------------------------------------------------
    # TIER 1: Exact Email Matching (Confidence: 1.0)
    # -------------------------------------------------------------
    if email:
        clean_email = email.lower().strip()
        for u in users:
            if u["email"].lower().strip() == clean_email:
                return {
                    "resolved_user_id": u["id"],
                    "resolved_name": u["name"],
                    "resolved_email": u["email"],
                    "confidence": 1.0,
                    "tier": "TIER_1_EMAIL"
                }

    if not raw_assignee:
        return {
            "resolved_user_id": None,
            "resolved_name": None,
            "resolved_email": None,
            "confidence": 0.0,
            "tier": "TIER_3_FALLBACK"
        }

    norm_target = normalize_text(raw_assignee)
    
    # Guard: target too short or matches generic stop words -> Tier 3 (never guess)
    if len(norm_target) < 2 or norm_target in GENERIC_STOP_WORDS:
        return {
            "resolved_user_id": None,
            "resolved_name": None,
            "resolved_email": None,
            "confidence": 0.0,
            "tier": "TIER_3_FALLBACK"
        }

    # -------------------------------------------------------------
    # TIER 2: Name & Alias Fuzzy Normalization
    # -------------------------------------------------------------
    # 1. Check known aliases dictionary
    for user_id, aliases in KNOWN_ALIASES.items():
        for alias in aliases:
            norm_alias = normalize_text(alias)
            if norm_target == norm_alias:
                # Security/F4: Verify that user_id actually exists in active users cache
                matched_user = next((u for u in users if u["id"] == user_id), None)
                if matched_user:
                    return {
                        "resolved_user_id": user_id,
                        "resolved_name": matched_user["name"],
                        "resolved_email": matched_user["email"],
                        "confidence": 0.98,
                        "tier": "TIER_2_FUZZY_NAME"
                    }
                else:
                    # User absent or disabled in active directory -> do not fabricate phantom user
                    return {
                        "resolved_user_id": None,
                        "resolved_name": None,
                        "resolved_email": None,
                        "confidence": 0.0,
                        "tier": "TIER_3_FALLBACK"
                    }

    # 2. Check cached user names & email prefixes
    candidate_matches: List[Tuple[Dict[str, Any], float]] = []
    
    for u in users:
        norm_name = normalize_text(u["name"])
        # Do not attempt substring matching on degenerate/empty normalized names
        if len(norm_name) < 2:
            continue
            
        name_parts = norm_name.split()
        
        # Exact full name match (highest confidence for Tier 2)
        if norm_target == norm_name:
            candidate_matches.append((u, 0.95))
            continue
            
        # Target matches an individual name token (first name or last name)
        if norm_target in name_parts:
            candidate_matches.append((u, 0.88))
            continue
            
        # Substring match (guarded: both strings must be >= 3 chars, and non-empty)
        if len(norm_target) >= 3 and len(norm_name) >= 3:
            if norm_target in norm_name or norm_name in norm_target:
                candidate_matches.append((u, 0.80))

    if candidate_matches:
        # Sort by score descending
        candidate_matches.sort(key=lambda x: x[1], reverse=True)
        top_user, top_score = candidate_matches[0]
        
        # Ambiguity check: if there is a tie at the top score with a different user, fall back to Tier 3
        ties = [u for u, score in candidate_matches if score == top_score and u["id"] != top_user["id"]]
        if ties:
            # Ambiguous resolution — never coin-flip an assignment
            return {
                "resolved_user_id": None,
                "resolved_name": None,
                "resolved_email": None,
                "confidence": 0.0,
                "tier": "TIER_3_FALLBACK"
            }
            
        if top_score >= 0.80:
            return {
                "resolved_user_id": top_user["id"],
                "resolved_name": top_user["name"],
                "resolved_email": top_user["email"],
                "confidence": top_score,
                "tier": "TIER_2_FUZZY_NAME"
            }

    # -------------------------------------------------------------
    # TIER 3: Fallback (Unresolved -> Intake group, unassigned)
    # -------------------------------------------------------------
    return {
        "resolved_user_id": None,
        "resolved_name": None,
        "resolved_email": None,
        "confidence": 0.0,
        "tier": "TIER_3_FALLBACK"
    }

def sync_users_from_monday(monday_client_module=None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Fetch active users from Monday.com GraphQL API with full pagination.
    Updates SQLite cache with user profiles, enabled flags, and aliases.
    """
    if monday_client_module is None:
        import sys
        try:
            from .config import WORKSPACE_DIR
        except (ImportError, ValueError):
            from config import WORKSPACE_DIR
        sys.path.insert(0, str(WORKSPACE_DIR / "tools"))
        from importlib import import_module
        monday_client_module = import_module("monday-client")
    
    synced = []
    page = 1
    limit = 50
    
    while True:
        query = f"""
        query {{
            users(page: {page}, limit: {limit}) {{
                id
                name
                email
                is_guest
                enabled
            }}
        }}
        """
        res = monday_client_module.gql(query)
        page_users = res.get("users", [])
        if not page_users:
            break
            
        for u in page_users:
            user_id = int(u["id"])
            name = u.get("name", "").strip()
            email = u.get("email", "").strip()
            is_guest = bool(u.get("is_guest", False))
            enabled = bool(u.get("enabled", True))
            aliases = KNOWN_ALIASES.get(user_id, [])
            
            db.upsert_user(
                user_id=user_id,
                name=name,
                email=email,
                aliases=aliases,
                is_guest=is_guest,
                enabled=enabled,
                db_path=db_path
            )
            synced.append({
                "id": user_id,
                "name": name,
                "email": email,
                "is_guest": is_guest,
                "enabled": enabled,
                "aliases": aliases
            })
            
        if len(page_users) < limit:
            break
        page += 1
        
    return synced
