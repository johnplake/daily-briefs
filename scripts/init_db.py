#!/usr/bin/env python3
"""
Initialize the papers database with the SQLite schema.
Idempotent - safe to run multiple times.
"""

import sqlite3
from pathlib import Path

from config import DB_PATH


SCHEMA = """
-- Main papers table
CREATE TABLE IF NOT EXISTS papers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Identity (these match the file path components)
    -- paper_source is the ORIGINAL source where we first discovered the paper.
    -- It determines the file path and never changes, even if the paper is
    -- later published at a conference.
    paper_source     TEXT NOT NULL,           -- "arxiv" (matches folder, never changes)
    paper_id         TEXT NOT NULL,           -- "2502.12345" (matches folder)
    announced_date   DATE NOT NULL,           -- "2026-02-21" (matches folder)
    
    -- Metadata
    title            TEXT NOT NULL,
    abstract         TEXT,
    authors          TEXT,                    -- JSON array: ["Alice Smith", "Bob Jones"]
    
    -- Categories
    primary_category TEXT,                    -- "cs.AI"
    categories       TEXT,                    -- "cs.AI cs.CL cs.LG"
    
    -- Versioning
    version          INTEGER DEFAULT 1,       -- current version number
    submitted_date   DATE,                    -- original submission date
    updated_date     DATE,                    -- when current version appeared
    
    -- Links
    arxiv_url        TEXT,
    pdf_url          TEXT,
    doi              TEXT,                    -- "10.48550/arXiv.2502.12345"
    
    -- Publication (updated when paper is published at a conference)
    published_venue  TEXT,                    -- "neurips-2026", "acl-2026", etc. (NULL if not published)
    published_url    TEXT,                    -- link to conference version
    volume           TEXT,                    -- journal/proceedings volume
    issue            TEXT,                    -- journal issue
    pages            TEXT,                    -- page range, e.g., "1-15"
    
    -- User-supplied metadata
    tags             TEXT,                    -- JSON array of user tags: ["important", "to-read"]
    
    -- Enrichment (citations)
    citations_s2     INTEGER,                 -- Semantic Scholar count
    citations_oa     INTEGER,                 -- OpenAlex count
    
    -- Embedding
    embedding_idx    INTEGER,                 -- FAISS index position (NULL = not embedded)
    umap_x           REAL,                    -- 2D projection X (NULL = not projected)
    umap_y           REAL,                    -- 2D projection Y (NULL = not projected)
    
    -- Text extraction
    text_extracted   BOOLEAN DEFAULT 0,       -- 1 if paper.txt exists
    
    -- Visibility
    hidden           BOOLEAN DEFAULT 0,       -- 1 = hidden from search/filter
    
    -- Timestamps
    ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(paper_source, paper_id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_announced ON papers(announced_date);
CREATE INDEX IF NOT EXISTS idx_source ON papers(paper_source);
CREATE INDEX IF NOT EXISTS idx_primary_cat ON papers(primary_category);
CREATE INDEX IF NOT EXISTS idx_embedding ON papers(embedding_idx);
CREATE INDEX IF NOT EXISTS idx_text ON papers(text_extracted);
CREATE INDEX IF NOT EXISTS idx_venue ON papers(published_venue);
CREATE INDEX IF NOT EXISTS idx_doi ON papers(doi);
"""

FTS_SCHEMA = """
-- Full-text search (title + abstract only)
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    paper_id,
    title,
    abstract,
    content='papers',
    content_rowid='id'
);
"""

# Triggers need special handling - check if they exist first
TRIGGERS = [
    ("papers_ai", """
        CREATE TRIGGER papers_ai AFTER INSERT ON papers BEGIN
            INSERT INTO papers_fts(rowid, paper_id, title, abstract)
            VALUES (new.id, new.paper_id, new.title, new.abstract);
        END;
    """),
    ("papers_ad", """
        CREATE TRIGGER papers_ad AFTER DELETE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, paper_id, title, abstract)
            VALUES ('delete', old.id, old.paper_id, old.title, old.abstract);
        END;
    """),
    ("papers_au", """
        CREATE TRIGGER papers_au AFTER UPDATE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, paper_id, title, abstract)
            VALUES ('delete', old.id, old.paper_id, old.title, old.abstract);
            INSERT INTO papers_fts(rowid, paper_id, title, abstract)
            VALUES (new.id, new.paper_id, new.title, new.abstract);
        END;
    """),
]


def init_db():
    """Initialize the database schema."""
    # Ensure data directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create main schema
    cursor.executescript(SCHEMA)
    print(f"✓ Created papers table and indexes")
    
    # Create FTS virtual table
    cursor.executescript(FTS_SCHEMA)
    print(f"✓ Created FTS5 virtual table")
    
    # Create triggers (check if they exist first)
    for trigger_name, trigger_sql in TRIGGERS:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger_name,)
        )
        if cursor.fetchone() is None:
            cursor.execute(trigger_sql)
            print(f"✓ Created trigger: {trigger_name}")
        else:
            print(f"  Trigger {trigger_name} already exists")
    
    conn.commit()
    
    # Report stats
    cursor.execute("SELECT COUNT(*) FROM papers")
    count = cursor.fetchone()[0]
    print(f"\n✓ Database ready at: {DB_PATH}")
    print(f"  Papers in database: {count}")
    
    conn.close()


if __name__ == "__main__":
    init_db()
