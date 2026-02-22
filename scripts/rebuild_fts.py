#!/usr/bin/env python3
"""
Emergency tool: rebuild SQLite FTS index.

Normally, triggers keep FTS in sync automatically.
Only use this if you suspect the FTS index is corrupted or out of sync
(e.g., after manual SQL edits that bypassed triggers).
"""

from config import get_db_connection

def rebuild_fts():
    conn = get_db_connection()
    # Rebuild the FTS5 index from the papers table
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print("✓ FTS index rebuilt")


if __name__ == "__main__":
    rebuild_fts()
