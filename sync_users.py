# -*- coding: utf-8 -*-
"""Script to sync Monday users into local SQLite cache."""
import sys
from pathlib import Path

pkg_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(pkg_dir))

import db
import config
import user_resolver

def main():
    db.init_db()
    users = user_resolver.sync_users_from_monday()
    print(f"✅ Successfully synced {len(users)} active Monday users into SQLite cache:\n")
    for u in users:
        print(f"  • ID: {u['id']} | Name: {u['name']} | Email: {u['email']} | Aliases: {u['aliases']}")
    print(f"\n📁 Database stored at: {config.SQLITE_DB_PATH}")

if __name__ == "__main__":
    main()
