# -*- coding: utf-8 -*-
"""Monday.com client for the Striking C-Team board.

The working implementation of the connection protocol documented in
`research/2026-08-19-session-53-monday-handoff.md` section 8. Written in
session 54 (2026-08-19) after the same code had to be rebuilt from scratch in
a temporary folder.

WHY THIS FILE EXISTS
    Hosted MCP (`https://mcp.monday.com/mcp`) returns HTTP 403 because Public
    Hosted MCP is not enabled by the Monday account administrator. Until that
    changes, every Monday operation goes through the GraphQL API using the
    OAuth token already held in the Codex credential store.

SECURITY — THE ONE RULE
    The access token is read at runtime and is NEVER printed, logged, returned,
    written to disk, or committed. There is no token in this file. Do not add
    one, and do not add a `--print-token` convenience flag.

USAGE
    python tools/monday-client.py whoami
    python tools/monday-client.py board [--out board.json]
    python tools/monday-client.py labels
    python tools/monday-client.py query '{ me { id name } }'
    python tools/monday-client.py upload <item_id> <column_id> <file_path>
    python tools/monday-client.py set-api-token

    As a library:
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "monday", pathlib.Path("tools/monday-client.py"))
        monday = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(monday)
        data = monday.gql("query { me { id } }")

MUTATION SAFETY SEQUENCE (handoff section 8 — follow it every time)
    1. `whoami` first. Confirm user 113703761 and account 35092246.
    2. Query the board and resolve every target by immutable ID, never by name.
    3. Send the smallest necessary mutation.
    4. Re-query the mutated item and verify the returned state.
    5. Record affected IDs and any irreversible deletion in the project handoff.
"""
import io
import json
import mimetypes
import os
import sys
import getpass
import urllib.error
import urllib.request
import uuid

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --------------------------------------------------------------------------
# Connection constants
# --------------------------------------------------------------------------
MCP_SHARED_TOKEN_PATH = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".gemini", "config", "sidecars", "monday-guest", "token.txt"
)
OWN_CRED_PATH = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                             ".striking-monday", "credentials.json")
CODEX_CRED_PATH = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                               ".codex", ".credentials.json")
CRED_PATH = OWN_CRED_PATH if os.path.exists(OWN_CRED_PATH) else CODEX_CRED_PATH
ENDPOINT = "https://api.monday.com/v2"
FILE_ENDPOINT = "https://api.monday.com/v2/file"
API_VERSION = "2024-10"

ACCOUNT_ID = "35092246"          # Striking
OWNER_USER_ID = 113703761        # Duong Tan — the project decision owner
BOARD_ID = "5102468049"          # C-Team
SUBITEM_BOARD_ID = "5102468050"  # Subitems of C-Team

# --------------------------------------------------------------------------
# Column ids. Resolve by these, never by display title — titles are editable
# in the Monday UI and two boards here use different ids for the same concept.
# --------------------------------------------------------------------------
PARENT_COLS = {
    "status": "color_mm5ken0m", "stage": "color_mm6c30sz",
    "health": "color_mm6cg534", "priority": "color_mm6cawvp",
    "progress": "numeric_mm6cb45z", "workstream": "text_mm6c9xj9",
    "market": "dropdown_mm6c784r", "verified": "date_mm6ct6m8",
    "credits": "numeric_mm6cjvtr", "evidence": "long_text_mm6ckq93",
    "next_action": "long_text_mm6cpe4a", "risk": "long_text_mm6c4yd",
    "decision": "long_text_mm6c6f9s", "kpi": "long_text_mm6cgaxq",
    "proof_files": "file_mm6czjvf", "proof_status": "color_mm6c6bgt",
}
SUBITEM_COLS = {
    "status": "status", "stage": "color_mm6cqzg7",
    "health": "color_mm6cgh8x", "priority": "color_mm6cjsdb",
    "progress": "numeric_mm6c8ar3", "workstream": "text_mm6cyd67",
    "market": "dropdown_mm6cfhtv", "verified": "date_mm6c9n52",
    "credits": "numeric_mm6c22cx", "evidence": "long_text_mm6cgg4w",
    "next_action": "long_text_mm6cnq45", "risk": "long_text_mm6cpy0k",
    "decision": "long_text_mm6c4wec", "kpi": "long_text_mm6c3pe5",
    "assignee": "multiple_person_mm571veq", "deadline": "date_mm57g465",
    "proof_files": "file_mm6cnrbs", "proof_status": "color_mm6cx9eh",
    "submitted": "date_mm6cacra", "reviewer": "multiple_person_mm6c8evh",
}

# Label indexes. `create_labels_if_missing` is deliberately left false in
# set_values, so an unknown label fails loudly instead of silently inventing one.
SUBITEM_STATUS = {"Done": 1, "In Progress": 7, "Not Started": 8, "Blocked": 9}
PARENT_STATUS = {"In Progress": 3, "Blocked": 4, "Not Started": 7, "Done": 8}
SUBITEM_STAGE = {"Production": 3, "Planned": 4, "QA": 6, "Research": 7,
                 "Published": 8, "Approved": 9}
PARENT_STAGE = {"Production": 3, "Research": 4, "QA": 6, "Approved": 7,
                "Planned": 8, "Published": 9}
SUBITEM_HEALTH = {"On Track": 3, "At Risk": 4, "Off Track": 6, "On Hold": 7,
                  "Complete": 8}
PARENT_HEALTH = {"On Track": 3, "At Risk": 4, "Off Track": 6, "Complete": 7}
PRIORITY = {"High": 3, "Critical": 4, "Medium": 6}
MARKET = {"Shared": 0, "EU": 1, "DE": 2}
# Proof lifecycle: Awaiting Proof -> Proof Submitted -> In Review -> Approved/Rejected.
# An unset cell displays as "Awaiting Proof"; that is the column default, not stored data.
PROOF_STATUS = {"Not Required": 0, "Approved": 1, "Rejected": 2,
                "Proof Submitted": 3, "In Review": 4, "Awaiting Proof": 5}


# --------------------------------------------------------------------------
# Credential handling
# --------------------------------------------------------------------------
def _walk(obj, path=""):
    """Yield (path, key, scalar) for every leaf in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s/%s" % (path, k)
            if isinstance(v, (dict, list)):
                for r in _walk(v, p):
                    yield r
            else:
                yield (p, str(k), v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = "%s/[%d]" % (path, i)
            if isinstance(v, (dict, list)):
                for r in _walk(v, p):
                    yield r
            else:
                yield (p, "[%d]" % i, v)


def _using_own_store():
    return CRED_PATH == OWN_CRED_PATH


def _load_own_record():
    with io.open(CRED_PATH, encoding="utf-8") as fh:
        return json.load(fh)  # flat: client_id, client_secret, access_token, ...


def _load_codex_record():
    with io.open(CRED_PATH, encoding="utf-8") as fh:
        store = json.load(fh)
    key = [k for k in store if "monday" in k.lower()][0]
    return store, key, store[key]


def check_expiry():
    """Report token expiry BEFORE spending a call. Returns (expired, iso_string).

    THE TRAP THIS EXISTS FOR: the grant lasts roughly 24 hours. Session 54
    authenticated successfully at 09:29 on 19 August and every call failed at
    09:29 the next day with a bare `401 Not authenticated`, which names neither
    expiry nor the refresh route. Handoff section 8 does not mention the
    lifetime at all.
    """
    if not os.path.exists(CRED_PATH):
        return (True, None)
    try:
        rec = _load_own_record() if _using_own_store() else _load_codex_record()[2]
        if _using_own_store() and rec.get("personal_api_token"):
            return (False, None)
        ms = int(rec["expires_at"])
    except Exception:
        return (False, None)
    import datetime
    dt = datetime.datetime.fromtimestamp(ms / 1000.0)
    return (dt < datetime.datetime.now(), dt.isoformat())


def refresh_token():
    """Exchange the stored refresh token for a new access token, and write it back.

    Two entirely different code paths depending on which store is active:

    OWN STORE (`~/.striking-monday/credentials.json`, from `monday-authorize.py`):
    refreshes against `https://auth.monday.com/oauth_ms/oauth/token` — the
    authorization server's own, internally consistent metadata. **Verified
    working 2026-08-20.**

    CODEX STORE (`~/.codex/.credentials.json`): refreshes against
    `https://mcp.monday.com/token`. **Confirmed BROKEN 2026-08-20** — that
    endpoint returns `HTTP 401 invalid_client, "Client not found"` because
    Monday's dynamic client registration expires faster than the access token
    it issued. Kept only so the failure mode is documented in one place instead
    of rediscovered; do not spend a second attempt on it. Re-authorize instead:
    `python tools/monday-authorize.py`.
    """
    import datetime
    import shutil
    import urllib.parse

    if not os.path.exists(CRED_PATH):
        raise SystemExit("NO_CREDENTIAL_STORE: %s" % CRED_PATH)

    if _using_own_store() and _load_own_record().get("personal_api_token"):
        raise SystemExit(
            "PERSONAL_API_TOKEN_HAS_NO_REFRESH: use  python tools/monday-client.py "
            "set-api-token  to replace it")

    backup = "%s.bak-%s" % (CRED_PATH, datetime.date.today().isoformat())
    if not os.path.exists(backup):
        shutil.copy2(CRED_PATH, backup)

    own = _using_own_store()
    if own:
        store = None
        rec = _load_own_record()
        token_endpoint = rec.get("token_endpoint", "https://auth.monday.com/oauth_ms/oauth/token")
    else:
        store, _key, rec = _load_codex_record()
        token_endpoint = "https://mcp.monday.com/token"

    if not rec.get("refresh_token"):
        raise SystemExit("NO_REFRESH_TOKEN: run  python tools/monday-authorize.py")

    params = {"grant_type": "refresh_token", "refresh_token": rec["refresh_token"],
              "client_id": rec["client_id"]}
    if own and rec.get("client_secret"):
        params["client_secret"] = rec["client_secret"]
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        token_endpoint, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit("REFRESH_FAILED: HTTP %s %s\nRun  python tools/monday-authorize.py"
                         % (e.code, e.read().decode("utf-8", "replace")[:600]))

    rec["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        rec["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        rec["expires_at"] = int(
            (datetime.datetime.now().timestamp() + int(payload["expires_in"])) * 1000)

    tmp = CRED_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec if own else store, fh, indent=2)
    os.replace(tmp, CRED_PATH)
    expiry = datetime.datetime.fromtimestamp(rec["expires_at"] / 1000.0).isoformat()
    return {"refreshed": True, "expires_at": expiry, "backup": os.path.basename(backup)}


def _extract_jwt(raw):
    if not raw:
        return ""
    s = str(raw).strip()
    import re
    m = re.search(r"jwt_session_token=([^;\s]+)", s)
    if m:
        return m.group(1)
    if s.lower().startswith("bearer "):
        return s[7:].strip()
    return s


def get_token():
    """Select the Monday credential from environment, MCP shared store, or local store.
    Never print or return this value anywhere a human or a log can see it.
    """
    # 1. Environment variables
    for ev in ("MONDAY_TOKEN", "MONDAY_API_TOKEN", "MONDAY_SESSION_TOKEN", "MONDAY_COOKIE"):
        val = os.environ.get(ev)
        if val:
            token = _extract_jwt(val)
            if token:
                return token

    # 2. MCP Shared Token Store (synced with IDE / MCP Server)
    if os.path.exists(MCP_SHARED_TOKEN_PATH):
        try:
            with io.open(MCP_SHARED_TOKEN_PATH, encoding="utf-8") as fh:
                raw = fh.read().strip()
                token = _extract_jwt(raw)
                if token:
                    return token
        except Exception:
            pass

    # 3. Local credentials store
    if os.path.exists(CRED_PATH):
        if _using_own_store():
            rec = _load_own_record()
            if rec.get("personal_api_token"):
                return _extract_jwt(rec["personal_api_token"])
            if rec.get("jwt_session_token"):
                return _extract_jwt(rec["jwt_session_token"])
            token = rec.get("access_token")
            if token:
                expired, when = check_expiry()
                if not expired:
                    return token

        with io.open(CRED_PATH, encoding="utf-8") as fh:
            data = json.load(fh)

        candidates = []
        for path, key, value in _walk(data):
            if not isinstance(value, str) or len(value) < 40:
                continue
            if "monday" not in path.lower():
                continue
            if any(t in key.lower() for t in ("access_token", "accesstoken", "token", "bearer", "jwt")):
                candidates.append(value)

        if not candidates:
            pool = [v for p, _k, v in _walk(data)
                    if isinstance(v, str) and "monday" in p.lower() and len(v) > 60]
            pool.sort(key=len, reverse=True)
            candidates = pool[:1]

        if candidates:
            return _extract_jwt(candidates[0])

    raise SystemExit(
        "NO_MONDAY_CREDENTIAL: No active Monday token found in environment, MCP token store, or credential store.\n"
        "  Fix: Provide your Monday session cookie or token, or run  python tools/monday-authorize.py")


def set_personal_api_token():
    """Store a Monday personal API token or session JWT without echoing it to the terminal."""
    if not _using_own_store():
        raise SystemExit(
            "PERSONAL_TOKEN_REQUIRES_OWN_STORE: %s is not the active store" % CRED_PATH)
    token = getpass.getpass("Monday API Token / Session Token (input hidden): ").strip()
    if len(token) < 20:
        raise SystemExit("INVALID_PERSONAL_API_TOKEN: input was empty or too short")

    import datetime
    import shutil
    record = _load_own_record() if os.path.exists(CRED_PATH) else {}
    backup = "%s.personal-api-%s" % (
        CRED_PATH, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    if os.path.exists(CRED_PATH):
        shutil.copy2(CRED_PATH, backup)
    record["auth_mode"] = "personal_api"
    record["personal_api_token"] = _extract_jwt(token)
    os.makedirs(os.path.dirname(CRED_PATH), exist_ok=True)
    tmp = CRED_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    os.replace(tmp, CRED_PATH)
    print("API / Session token stored in the local credential store.")
    print("Verify with:  python tools/monday-client.py whoami")


# --------------------------------------------------------------------------
# GraphQL
# --------------------------------------------------------------------------
def gql(query, variables=None, token=None):
    """POST a GraphQL document. Raises on transport or GraphQL errors."""
    raw_token = token or get_token()
    auth_header = raw_token
    if not auth_header.lower().startswith("bearer ") and auth_header.startswith("eyJ"):
        auth_header = "Bearer " + auth_header

    body = {"query": query}
    if variables:
        body["variables"] = variables
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": auth_header,
                 "Content-Type": "application/json",
                 "API-Version": API_VERSION},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:2000]
        if e.code == 403:
            detail += ("\nHINT: 403 on api.monday.com usually means the OAuth grant lacks a "
                       "scope, not that hosted MCP is off. Check boards:write / updates:write.")
        raise SystemExit("HTTP %s from Monday: %s" % (e.code, detail))
    if "errors" in payload:
        raise SystemExit("GraphQL errors: %s" % json.dumps(payload["errors"])[:3000])
    return payload["data"]


def verify_identity(token=None):
    """Step 1 of the mutation safety sequence. Refuses to proceed on a wrong account."""
    me = gql("query { me { id name email account { id name } } }", token=token)["me"]
    if me["account"]["id"] != ACCOUNT_ID:
        raise SystemExit("WRONG_ACCOUNT: authenticated against %s (%s), expected Striking (%s). "
                         "Refusing to continue." % (me["account"]["name"],
                                                    me["account"]["id"], ACCOUNT_ID))
    return me


def set_values(board_id, item_id, values, token=None):
    """Write column values. Unknown labels fail loudly rather than being invented."""
    q = ("mutation ($b: ID!, $i: ID!, $v: JSON!) { change_multiple_column_values"
         "(board_id: $b, item_id: $i, column_values: $v, create_labels_if_missing: false)"
         " { id name } }")
    return gql(q, {"b": str(board_id), "i": str(item_id), "v": json.dumps(values)}, token)


def create_subitem(parent_item_id, name, values, token=None):
    q = ("mutation ($p: ID!, $n: String!, $v: JSON!) { create_subitem"
         "(parent_item_id: $p, item_name: $n, column_values: $v) { id name } }")
    return gql(q, {"p": str(parent_item_id), "n": name,
                   "v": json.dumps(values)}, token)["create_subitem"]


def post_update(item_id, html_body, token=None):
    q = "mutation ($i: ID!, $b: String!) { create_update(item_id: $i, body: $b) { id created_at } }"
    return gql(q, {"i": str(item_id), "b": html_body}, token)["create_update"]


# --------------------------------------------------------------------------
# File upload
# --------------------------------------------------------------------------
def upload_file_to_column(item_id, column_id, file_path, token=None):
    """Multipart upload to a file column.

    This is the part that is NOT in handoff section 8 and that costs time to
    rediscover. The shape Monday requires:
        - endpoint  https://api.monday.com/v2/file  (not /v2)
        - part `query` : the mutation, with `$file: File!`
        - part `map`   : {"image": ["variables.file"]}
        - part `image` : the bytes, with a filename and a content type
    The literal part name `image` must match the key used in `map`, for any
    file type — it is not restricted to images.
    """
    token = token or get_token()
    if not os.path.exists(file_path):
        return {"error": "FILE_NOT_FOUND: %s" % file_path}

    boundary = "----monday" + uuid.uuid4().hex
    query = ('mutation ($file: File!) { add_file_to_column '
             '(item_id: %s, column_id: "%s", file: $file) { id name url } }'
             % (item_id, column_id))
    fname = os.path.basename(file_path)
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        content = fh.read()

    parts = []

    def add(name, value, filename=None, content_type=None):
        head = '--%s\r\nContent-Disposition: form-data; name="%s"' % (boundary, name)
        if filename:
            head += '; filename="%s"' % filename
        head += "\r\n"
        if content_type:
            head += "Content-Type: %s\r\n" % content_type
        head += "\r\n"
        parts.append(head.encode("utf-8"))
        parts.append(value if isinstance(value, bytes) else value.encode("utf-8"))
        parts.append(b"\r\n")

    add("query", query)
    add("map", json.dumps({"image": ["variables.file"]}))
    add("image", content, filename=fname, content_type=ctype)
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))

    req = urllib.request.Request(
        FILE_ENDPOINT,
        data=b"".join(parts),
        headers={"Authorization": token,
                 "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "API-Version": API_VERSION},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:1500])}
    if "errors" in payload:
        return {"error": json.dumps(payload["errors"])[:1500]}
    return payload["data"]["add_file_to_column"]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _cmd_whoami():
    me = verify_identity()
    print(json.dumps(me, indent=2, ensure_ascii=False))


def _cmd_board(out=None):
    token = get_token()
    verify_identity(token)
    schema = gql("""
    query { boards(ids: [%s, %s]) {
        id name items_count
        groups { id title }
        columns { id title type settings_str }
    } }""" % (BOARD_ID, SUBITEM_BOARD_ID), token=token)

    items, cursor = [], None
    while True:
        page = gql("""
        query ($cursor: String) { boards(ids: [%s]) {
            items_page(limit: 50, cursor: $cursor) { cursor items {
                id name group { id title }
                column_values { id text type }
                subitems { id name column_values { id text type } }
            } } } }""" % BOARD_ID, {"cursor": cursor}, token)["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break

    n_sub = sum(len(i["subitems"]) for i in items)
    print("C-Team: %d parent workstreams, %d subtasks" % (len(items), n_sub))
    for it in items:
        print("\n[%s] %s  (%s)" % (it["id"], it["name"], it["group"]["title"]))
        for s in it["subitems"]:
            cv = {c["id"]: (c["text"] or "") for c in s["column_values"]}
            print("   - [%s] %-64s | %-12s | %s" % (
                s["id"], s["name"][:64], cv.get("status", ""),
                cv.get(SUBITEM_COLS["proof_status"], "")))
    if out:
        with io.open(out, "w", encoding="utf-8") as fh:
            json.dump({"schema": schema, "items": items}, fh, indent=2, ensure_ascii=False)
        print("\nWritten to %s" % out)


def _cmd_labels():
    d = gql("query { boards(ids: [%s, %s]) { id name columns { id title type settings_str } } }"
            % (BOARD_ID, SUBITEM_BOARD_ID))
    for b in d["boards"]:
        print("### %s %s" % (b["id"], b["name"]))
        for c in b["columns"]:
            if c["type"] not in ("status", "dropdown"):
                continue
            try:
                labels = json.loads(c["settings_str"]).get("labels", {})
            except Exception:
                continue
            if not isinstance(labels, dict) or len(labels) > 30:
                continue  # skip the 200-entry country list
            vals = ", ".join("%s=%s" % (k, v)
                             for k, v in sorted(labels.items(), key=lambda t: int(t[0])) if v)
            print("  %-24s %-18s %s" % (c["id"], c["title"], vals))
        print()


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "status":
        expired, when = check_expiry()
        print("credential store : %s" % CRED_PATH)
        if _using_own_store():
            rec = _load_own_record()
            print("auth mode        : %s" % ("personal_api" if rec.get("personal_api_token") else "oauth"))
        print("token expires_at : %s" % (when or "unknown"))
        print("state            : %s" % ("EXPIRED" if expired else "valid"))
        if expired:
            print("\nRun: python tools/monday-client.py refresh")
        return 1 if expired else 0
    elif cmd == "refresh":
        print(json.dumps(refresh_token(), indent=2))
    elif cmd == "set-api-token":
        set_personal_api_token()
    elif cmd == "whoami":
        _cmd_whoami()
    elif cmd == "board":
        out = argv[argv.index("--out") + 1] if "--out" in argv else None
        _cmd_board(out)
    elif cmd == "labels":
        _cmd_labels()
    elif cmd == "query":
        if len(argv) < 3:
            print("usage: query '<graphql document>'")
            return 1
        print(json.dumps(gql(argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "upload":
        if len(argv) < 5:
            print("usage: upload <item_id> <column_id> <file_path>")
            return 1
        r = upload_file_to_column(argv[2], argv[3], argv[4])
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 1 if "error" in r else 0
    else:
        print("unknown command: %s" % cmd)
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
