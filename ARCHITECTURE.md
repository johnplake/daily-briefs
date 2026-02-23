# Daily Briefs Architecture

## Overview

SQLite-centric architecture for arXiv paper curation. The database is the single source of truth for all metadata; files are used only for large blobs (extracted text) and binary indexes (FAISS embeddings).

---

## File Structure

```
data/
├── papers.db                              # SQLite - single source of truth
├── arxiv/                                 # Text files organized by source
│   └── {announced_date}/                  # e.g., 2026-02-21
│       └── {paper_id}/                    # e.g., 2502.12345
│           └── paper.txt                  # extracted text (~75KB each)
└── embeddings/
    └── faiss.index                        # FAISS vectors (positions match embedding_idx)
```

### Path ↔ DB Mapping

| Path component | DB column | Example | Notes |
|----------------|-----------|---------|-------|
| `arxiv` | `paper_source` | `"arxiv"` | Original source (never changes) |
| `2026-02-21` | `announced_date` | `2026-02-21` | When first announced |
| `2502.12345` | `paper_id` | `"2502.12345"` | Original ID |

**Reconstructing path from DB:**
```python
text_path = f"data/{row.paper_source}/{row.announced_date}/{row.paper_id}/paper.txt"
```

Note: `paper_source` is the *original* source where we discovered the paper. If a paper later gets published at a conference, `published_venue` tracks that, but the path remains based on the original source.

---

## SQLite Schema

```sql
CREATE TABLE papers (
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
    categories       TEXT,                    -- JSON array: ["cs.AI", "cs.CL", "cs.LG"]
    
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
    
    -- 2D projection (UMAP)
    umap_x           REAL,                    -- 2D x-coordinate for visualization
    umap_y           REAL,                    -- 2D y-coordinate for visualization
    
    -- Text extraction
    text_extracted   BOOLEAN DEFAULT 0,       -- 1 if paper.txt exists
    
    -- Timestamps
    ingested_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(paper_source, paper_id)
);

-- Indexes for common queries
CREATE INDEX idx_announced ON papers(announced_date);
CREATE INDEX idx_source ON papers(paper_source);
CREATE INDEX idx_primary_cat ON papers(primary_category);
CREATE INDEX idx_embedding ON papers(embedding_idx);
CREATE INDEX idx_text ON papers(text_extracted);
CREATE INDEX idx_venue ON papers(published_venue);
CREATE INDEX idx_doi ON papers(doi);

-- Full-text search (title + abstract only)
CREATE VIRTUAL TABLE papers_fts USING fts5(
    paper_id,
    title,
    abstract,
    content='papers',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, paper_id, title, abstract)
    VALUES (new.id, new.paper_id, new.title, new.abstract);
END;

CREATE TRIGGER papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, paper_id, title, abstract)
    VALUES ('delete', old.id, old.paper_id, old.title, old.abstract);
END;

CREATE TRIGGER papers_au AFTER UPDATE ON papers
WHEN old.title != new.title OR old.abstract != new.abstract
BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, paper_id, title, abstract)
    VALUES ('delete', old.id, old.paper_id, old.title, old.abstract);
    INSERT INTO papers_fts(rowid, paper_id, title, abstract)
    VALUES (new.id, new.paper_id, new.title, new.abstract);
END;
```

**Full-text search usage:**
```sql
-- Simple search
SELECT p.* FROM papers p
JOIN papers_fts fts ON p.id = fts.rowid
WHERE papers_fts MATCH 'transformer attention';

-- Ranked by relevance
SELECT p.*, bm25(papers_fts) as score FROM papers p
JOIN papers_fts fts ON p.id = fts.rowid
WHERE papers_fts MATCH 'language model'
ORDER BY score;
```

---

## Version Update Behavior

When a newer version of a paper appears in RSS:

```sql
ON CONFLICT(paper_source, paper_id) DO UPDATE SET
    title = excluded.title,
    abstract = excluded.abstract,
    authors = excluded.authors,
    version = excluded.version,
    updated_date = excluded.updated_date,
    updated_at = CURRENT_TIMESTAMP
    -- embedding_idx NOT cleared (intentional)
```

**Then:**
- Re-download PDF, re-extract text, overwrite `paper.txt`
- Embedding kept as-is

**Rationale:** A paper's semantic fingerprint (what it's about) rarely changes significantly between versions. Re-embedding thousands of papers for minor revisions isn't worth the compute. The text file, however, should always reflect the latest version since users read and query it directly.

---

## Conference Publication Behavior

When a paper originally discovered on arXiv gets published at a conference (e.g., NeurIPS, ACL):

**What changes:**
- `published_venue` ← "neurips-2026"
- `published_url` ← link to conference page/PDF
- `updated_at` ← current timestamp
- Text file overwritten with content extracted from conference PDF

**What stays the same:**
- `paper_source` ← stays "arxiv" (original source)
- `paper_id` ← stays "2502.12345" (original ID)
- `announced_date` ← stays unchanged
- File path ← stays `data/arxiv/2026-02-21/2502.12345/paper.txt`
- `embedding_idx` ← stays unchanged (no re-embedding needed)

**Rationale:** The paper's identity in our system is based on where we first discovered it (typically arXiv). The file path is derived from this stable identity and never changes. When the conference version appears, we update the text to the polished conference PDF and record the venue, but the paper doesn't move or get re-embedded. This avoids duplication and keeps paths predictable.

---

## Scripts

### `init_db.py` (new)
- Creates `data/papers.db` with schema above
- Idempotent (safe to run multiple times)

### `ingest.py`
- Fetches RSS feeds for configured categories
- For each paper:
  - INSERT into `papers` table
  - ON CONFLICT: UPDATE metadata, version, updated_date
  - If `--extract-text`: download PDF, extract text, save to path, set `text_extracted=1`
- Text always re-extracted on version update (overwrite file)
- Embedding NOT cleared on version update

### `enrich.py`
- Queries DB for papers needing citation data
- Fetches from Semantic Scholar + OpenAlex APIs
- Updates `citations_s2`, `citations_oa` columns directly
- No separate files

### `embed.py`
- Queries DB: `WHERE embedding_idx IS NULL AND abstract IS NOT NULL`
- Generates SPECTER embeddings from title + abstract
- Appends vectors to FAISS index
- Updates `embedding_idx` column with position
- Optional `--umap` flag runs UMAP projection after embedding
- Also accepts `--umap-neighbors` and `--umap-min-dist` params

### `project.py`
- Standalone UMAP projection (also callable via `embed.py --umap`)
- Loads all embeddings from FAISS index
- Runs UMAP to project 768-dim → 2D
- Updates `umap_x`, `umap_y` columns for all papers
- Refits on all papers each run (coordinates may shift)
- No model saved — always recomputes

### `verify_embeddings.py`
- Verifies embedding_idx ↔ FAISS mapping integrity
- Re-embeds sample papers and checks cosine similarity
- Reports any mismatches or out-of-bounds indices

### `search.py`
- Takes query text, generates embedding
- Searches FAISS index, gets positions
- Queries DB: `WHERE embedding_idx IN (pos1, pos2, ...)`
- Returns full paper metadata

### `filter.py`
- Queries DB for papers by date range, categories, etc.
- Applies scoring (citations, interest model)
- Outputs filtered list for reporting

### `report.py`
- Takes filtered papers
- Generates markdown report with GitHub issue links
- Queries DB for full metadata as needed

### `migrate.py` (one-time)
- Reads existing `data/arxiv/raw/YYYY-MM-DD/<id>/metadata.json` files
- Inserts into new DB schema
- Moves `paper.txt` to new location
- Imports existing `paper_ids.json` → sets `embedding_idx` values
- Cleans up old structure after verification

---

## Embedding ↔ DB Relationship

- FAISS index is append-only; positions are stable
- `embedding_idx` = position in FAISS index (0, 1, 2, ...)
- Search flow: query → FAISS returns positions → `SELECT * FROM papers WHERE embedding_idx IN (...)`
- New papers get the next available position

---

## Operational Notes

### SQLite WAL Mode

Connections use `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` for better concurrency (readers + one writer). Expect `papers.db-wal` and `papers.db-shm` files alongside `papers.db`.

### Embedding Atomicity Marker

`embed.py` writes a marker file (`data/embeddings/embed_in_progress.json`) before adding vectors to FAISS and updating DB. If the process crashes mid‑write, the marker remains and **embed refuses to append** unless `--rebuild` is used. This prevents silent FAISS/DB divergence.

---

## Extensibility

The schema is easy to extend:

**Adding scalar or text data (e.g., new citation sources):**
```sql
ALTER TABLE papers ADD COLUMN citations_scite INTEGER;
ALTER TABLE papers ADD COLUMN scite_supporting TEXT;
```

**Adding relational data (e.g., paper similarity graphs):**
```sql
CREATE TABLE paper_relations (
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    relation     TEXT NOT NULL,   -- "cites", "similar", etc.
    source_tool  TEXT,            -- "connected_papers", "litmaps"
    weight       REAL,
    UNIQUE(source_id, target_id, relation, source_tool)
);
```

No migration complexity. SQLite handles schema evolution gracefully.

---

## Migration (Completed 2026-02-21)

Migration from JSON-based structure to SQLite-centric was completed:
1. `init_db.py` created schema
2. `migrate.py` moved 439 papers from `metadata.json` files to DB
3. `paper_ids.json` mappings transferred to `embedding_idx` column
4. Old `data/arxiv/raw/` structure deleted
5. `verify_embeddings.py` confirmed all mappings correct

---

## Summary: Before vs After

| Before | After |
|--------|-------|
| `metadata.json` per paper | SQLite `papers` table |
| `citations.json` per paper | `citations_s2`, `citations_oa` columns |
| `paper_ids.json` for FAISS mapping | `embedding_idx` column |
| `data/arxiv/raw/{date}/{id}/` | `data/arxiv/{date}/{id}/paper.txt` |
| Query = scan JSON files | Query = SQL |
| No full-text search | FTS5 on title + abstract |

---

## Design Decisions & Trade-offs

This section documents intentional design choices and things we explicitly decided NOT to implement.

### UMAP Non-Determinism (Accepted)

**Behavior:** UMAP coordinates (`umap_x`, `umap_y`) may shift between runs, even for papers that haven't changed. This is because:
- UMAP uses stochastic initialization
- We refit on all papers each run (no saved model)
- New papers change the embedding landscape

**Why it's OK:**
- The 2D projection is for visualization/exploration, not for stable identity
- Relative clustering is preserved (similar papers stay near each other)
- Saving a UMAP model adds complexity and doesn't meaningfully improve UX
- Dashboard updates coordinates on each view anyway

**Alternative considered:** Save fitted UMAP model and only transform new points. Rejected because:
- UMAP `transform()` quality degrades for points far from training distribution
- Would need periodic refitting anyway
- Adds model versioning complexity

### Re-Embedding on Version Updates (Skipped)

**Behavior:** When a paper gets a new version (v2, v3, etc.), we update metadata and re-extract text, but we do NOT re-embed.

**Rationale:** A paper's semantic fingerprint rarely changes significantly between versions. Most updates are typo fixes, bibliography additions, or minor clarifications. Re-embedding thousands of papers for minor revisions isn't worth the compute. The abstract (used for embedding) almost never changes substantively.

### Re-Enrichment Workflow

**Behavior:** Citation enrichment (`enrich.py`) is skipped in daily runs (`--skip-enrich`) but can be run manually or periodically.

**Why skip daily:**
- New papers have 0 citations on day 1
- Semantic Scholar / OpenAlex take days to index new papers
- Running enrichment daily wastes API calls

**Recommended workflow:**
- Weekly batch: `enrich.py --update` to refresh all papers
- Or monthly: focus on papers older than N days
- Enrichment is useful for the "Popular" stream (citation-based ranking)

### Deduplication

**Behavior:** Papers are deduplicated by `(paper_source, paper_id)`. If the same paper appears in multiple RSS feeds (e.g., cs.AI and cs.LG), it's stored once.

**How it works:**
- `UNIQUE(paper_source, paper_id)` constraint in SQLite
- `ON CONFLICT DO UPDATE` merges categories if needed
- First ingestion wins for `announced_date`

**Cross-source deduplication (NOT implemented):**
- If a paper appears on arXiv AND bioRxiv with different IDs, they'd be separate entries
- Could link via DOI in future, but not a current priority
- arXiv-only focus makes this moot for now

### Text Extraction Failures (Graceful Degradation)

**Behavior:** If PDF download or text extraction fails:
1. Paper is still added to database with full metadata (title, abstract, authors)
2. `text_extracted = 0` flag is set
3. Paper still gets embedded (using abstract, not full text)
4. Paper appears in searches and filters normally

**Why this is fine:**
- Abstract is sufficient for semantic embedding (SPECTER was trained on abstracts)
- Full text is a nice-to-have for deep reading, not required for discovery
- Failures are rare (arXiv is reliable)
- Can query `WHERE text_extracted = 0` to find and retry failures

### PDF Storage (Delete After Extraction)

**Behavior:** PDFs are deleted immediately after text extraction.

**Rationale:**
- PDF: ~3MB average; Text: ~75KB average (40x savings)
- At 1500 papers/day: 4.5GB/day vs 110MB/day
- PDFs can always be re-downloaded from arXiv if needed
- Full text is sufficient for search and reading

### Rate Limiting in enrich.py (Basic)

**Behavior:** Fixed delay between API calls + 5s sleep on 429. No exponential backoff.

**Why not backoff:**
- Enrichment runs manually, not in critical path
- If rate limited, just wait and retry
- Adding `backoff` library for rarely-used code isn't worth it
- Human can monitor and adjust if needed

### Race Conditions / File Locking (Not Implemented)

**Behavior:** No file locking between concurrent runs.

**Why not needed:**
- Daily brief runs at 8am, embeddings at 11:50pm (16 hours apart)
- Dashboard only reads (doesn't write embeddings)
- Manual runs are operator-controlled
- SQLite handles concurrent reads fine

### PDF Validation Before Extraction (Not Implemented)

**Behavior:** No explicit PDF validation (magic bytes, file size check) before extraction.

**Why not needed:**
- PyMuPDF (fitz) handles corrupt PDFs gracefully (throws exception)
- We already wrap extraction in try/except
- arXiv PDFs are reliable
- Extra validation code for rare edge case isn't worth it

---

## Health Monitoring

### Healthchecks.io Integration

Each cron job pings healthchecks.io on completion:

| Job | Ping URL | Schedule |
|-----|----------|----------|
| Daily Brief | `https://hc-ping.com/a8625459-cc2d-4232-8441-d4091de62f2a` | 8am Mon-Fri |
| Embeddings | `https://hc-ping.com/a3bc824a-c526-4365-a6c0-6faaaed8098f` | 11:50pm Mon-Fri |

**How it works:**
- Job succeeds → pings URL → check stays green
- Job fails → pings `/fail` endpoint → check turns red, alert sent
- Job doesn't run → no ping → grace period expires → alert sent

**Alerts:** Sent to Telegram group (Cron-status topic) via healthchecks.io integration.

### Telegram Notifications

Both cron jobs send Telegram messages:
- **Success:** Summary of papers ingested/embedded
- **Failure:** Error details

This provides immediate visibility without checking healthchecks.io dashboard.

### Backup

Cron job configurations are saved in `cron-jobs-backup.json` for easy recreation after container reinstalls.

---

## Multi-Agent Configuration

Scripts support per-agent configuration via the `DAILY_BRIEFS_CONFIG` environment variable.

### Config File Structure

```yaml
# config.yaml
paths:
  root: /path/to/data/directory
  db: data/papers.db          # relative to root
  text: data/text/
  embeddings: data/embeddings/
  filtered: data/filtered/
  reports: reports/

categories:
  tier1: [cs.AI, cs.CL, cs.LG, ...]
  tier2: [cs.CV, ...]
  tier3: [cs.RO, ...]

interests:
  keywords: [language model, transformer, ...]
  project_contexts: [...]

filtering:
  tier1_min_score: 0.1
  # ...
```

### Config Resolution

1. Check `DAILY_BRIEFS_CONFIG` env var
2. If not set, fall back to `{script_dir}/../config.yaml`

### Wrapper Scripts

Each agent has a wrapper in their `bin/` directory:

```bash
#!/bin/bash
# workspace-turtle/bin/daily-briefs

SCRIPT_DIR="/path/to/shared/scripts"
CONFIG="/path/to/agent/config.yaml"

SCRIPT_NAME="$1"
shift

exec env \
  DAILY_BRIEFS_CONFIG="$CONFIG" \
  python "$SCRIPT_DIR/${SCRIPT_NAME}.py" "$@"
```

**Usage:**
```bash
daily-briefs ingest --date 2026-02-22
daily-briefs embed --date 2026-02-22
daily-briefs search -q "transformer attention"
```

### Agent Isolation

Each agent gets:
- **Own config file** - custom categories, interests, thresholds
- **Own data directory** - separate database, embeddings, reports
- **Shared scripts** - single codebase, no duplication
