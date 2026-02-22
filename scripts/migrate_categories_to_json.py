#!/usr/bin/env python3
"""
Migration: Convert categories from space-separated to JSON array.

Before: "cs.AI cs.LG cs.CL"
After:  ["cs.AI", "cs.LG", "cs.CL"]

This is a one-time migration script.
"""

import json
import sqlite3
from config import DB_PATH

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Get all papers with categories
    cursor = conn.execute("SELECT id, categories FROM papers WHERE categories IS NOT NULL")
    papers = cursor.fetchall()
    
    print(f"Migrating {len(papers)} papers...")
    
    migrated = 0
    skipped = 0
    
    for paper in papers:
        paper_id = paper["id"]
        categories = paper["categories"]
        
        # Skip if already JSON (starts with [)
        if categories.startswith("["):
            skipped += 1
            continue
        
        # Convert space-separated to JSON array
        cats_list = categories.split()
        cats_json = json.dumps(cats_list)
        
        conn.execute(
            "UPDATE papers SET categories = ? WHERE id = ?",
            (cats_json, paper_id)
        )
        migrated += 1
    
    conn.commit()
    conn.close()
    
    print(f"✓ Migrated: {migrated}")
    print(f"✓ Skipped (already JSON): {skipped}")
    print("Done!")


if __name__ == "__main__":
    migrate()
