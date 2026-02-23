# Daily Briefs

Automated arXiv paper ingestion, filtering, and daily briefing system.

## Overview

This system:
1. **Sources** papers from arXiv RSS feeds daily (43 categories across 3 tiers)
2. **Extracts** full text from PDFs (saves ~75KB/paper instead of ~3MB)
3. **Enriches** with citation data via Semantic Scholar + OpenAlex
4. **Embeds** papers using SPECTER for semantic search
5. **Projects** to 2D via UMAP for visualization
6. **Filters** into 3 streams: Popular, Interest-based, Serendipity
7. **Reports** daily markdown briefs with TLDRs and feedback links

## Architecture

**SQLite-centric** — the database is the single source of truth. See `ARCHITECTURE.md` for full details.

```
data/
├── papers.db                    # SQLite database (all metadata)
├── {source}/{date}/{id}/        # Extracted text files (e.g., arxiv/2026-02-23/2602.17053/)
│   └── paper.txt                # Extracted full text (~75KB)
└── embeddings/
    └── faiss.index              # SPECTER vectors (768-dim)
```

## Category Tiers

### Tier 1 (Primary - high recall)
cs.AI, cs.CL, cs.CY, cs.LG, cs.NE, stat.ML

### Tier 2 (Secondary - high precision)
cs.IR, cond-mat.dis-nn, cs.CE, cs.CV, cs.SI, cs.ET, cs.DL, cs.GT, cs.HC, cs.MA, cs.OH, stat.TH, cs.GL, physics.hist-ph, physics.soc-ph, math.DS, math.IT, math.MG, math.OC, math.PR, math.ST

### Tier 3 (Tertiary - archive only)
cs.RO, cs.PL, cs.SY, q-bio.NC, q-bio.PE, q-bio.OT, q-bio.QM, econ.EM, econ.GN, econ.TH, eess.SY, eess.SP, cond-mat.stat-mech, nlin.AO, nlin.CD, nlin.CG

## Filtering Streams

1. **Popular** - High citation velocity, trending in field
2. **Interest** - Matches keywords/project contexts in config.yaml
3. **Serendipity** - Random samples from near-misses

Plus feedback candidates:
- **Near-misses** - Papers that barely didn't make the cut
- **Random negatives** - For calibration

## Usage

### Daily Pipeline (cron at 8am Mon-Fri)
```bash
.venv/bin/python scripts/ingest.py --extract-text
.venv/bin/python scripts/filter.py --date $(date +%Y-%m-%d)
.venv/bin/python scripts/report.py --date $(date +%Y-%m-%d)
```

### Embeddings + UMAP (cron at 11:50pm Mon-Fri)
```bash
# Generate SPECTER embeddings and project to 2D
.venv/bin/python scripts/embed.py --umap

# Or separately:
.venv/bin/python scripts/embed.py           # embeddings only
.venv/bin/python scripts/project.py         # UMAP only
```

### Citation Enrichment
```bash
.venv/bin/python scripts/enrich.py --date 2026-02-21
.venv/bin/python scripts/enrich.py --update  # Re-enrich all
.venv/bin/python scripts/enrich.py --dry-run # Fetch without DB writes
```

### Semantic Search
```bash
# Search by query
.venv/bin/python scripts/search.py --query "transformer attention mechanism"

# Find similar papers
.venv/bin/python scripts/search.py --similar 2602.16802
```

### Verify Embedding Integrity
```bash
.venv/bin/python scripts/verify_embeddings.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `ingest.py` | Fetch RSS feeds, download PDFs, extract text |
| `enrich.py` | Add citation counts from S2 + OpenAlex |
| `embed.py` | Generate SPECTER embeddings, optionally run UMAP |
| `project.py` | UMAP 2D projection (standalone) |
| `search.py` | Semantic search (query or similar papers) |
| `filter.py` | Apply filtering streams for a date |
| `report.py` | Generate markdown report |
| `init_db.py` | Initialize SQLite schema |
| `migrate_categories_to_json.py` | One-time migration: categories string → JSON |
| `rebuild_fts.py` | Emergency rebuild of FTS index |
| `verify_embeddings.py` | Check embedding_idx ↔ FAISS consistency |
| `check.py` | Full sanity checks (DB, FAISS, UMAP, FTS) |
| `utils.py` | Shared helpers (safe_json_load, etc.) |

## Cron Schedule

| Job | Schedule (CST) | What |
|-----|----------------|------|
| Daily Brief | 8am Mon-Fri | Ingest → filter → report → Telegram summary |
| Embeddings | 11:50pm Mon-Fri | `embed.py --umap` (SPECTER + 2D projection) |

Note: arXiv announces papers Sun-Thu at 8pm ET. Running at 8am CST captures the previous night's announcement.

### Health Monitoring

Both jobs are monitored via [healthchecks.io](https://healthchecks.io):
- Jobs ping on success → dashboard stays green
- Jobs fail or don't run → alerts sent to Telegram

See `cron-jobs-backup.json` for job configs (recreate after container reinstall).

## Setup

```bash
cd Projects/daily-briefs

# Create environment (using uv)
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Initialize database
.venv/bin/python scripts/init_db.py
```

## Storage Estimates

| Asset | Size/paper | Daily (~1500 papers) | Monthly |
|-------|------------|----------------------|---------|
| SQLite row | ~2KB | ~3MB | ~60MB |
| paper.txt | ~75KB | ~110MB | ~2.2GB |
| FAISS embedding | ~4KB | ~6MB | ~120MB |
| **Total** | ~81KB | ~120MB | ~2.4GB |

## Database Schema

Key columns in `papers` table:
- `paper_source`, `paper_id`, `announced_date` — identity
- `title`, `abstract`, `authors`, `categories` — metadata (authors/categories stored as JSON arrays)
- `citations_s2`, `citations_oa` — citation counts
- `embedding_idx` — position in FAISS index
- `umap_x`, `umap_y` — 2D coordinates for visualization
- `text_extracted` — whether paper.txt exists

Full-text search via FTS5 on title + abstract.

See `ARCHITECTURE.md` for complete schema.

## Feedback

Papers in daily briefs include feedback links (GitHub Issues). Rate papers:
- 👍 Useful
- 🤷 Marginal  
- 👎 Not useful

Feedback improves the filtering model over time.

## Design Decisions

Key trade-offs documented in `ARCHITECTURE.md`:

| Decision | Choice | Rationale |
|----------|--------|-----------|
| UMAP coordinates | Non-deterministic | Refit all papers each run; stable clustering matters, not exact coords |
| Version updates | Don't re-embed | Semantic fingerprint rarely changes; save compute |
| Text extraction fail | Graceful degradation | Paper still gets metadata + abstract embedding |
| PDF storage | Delete after extraction | 40x space savings; can re-download if needed |
| Rate limiting | Basic (no backoff) | Enrichment is manual; human can monitor |
| File locking | None | Jobs 16h apart; no concurrent writes |
| PDF validation | None | PyMuPDF handles errors; arXiv is reliable |

## Troubleshooting

### Papers missing embeddings
```sql
SELECT paper_id, title FROM papers WHERE embedding_idx IS NULL AND abstract IS NOT NULL;
```
Run `embed.py` to generate missing embeddings.

### Text extraction failures
```sql
SELECT paper_id, title FROM papers WHERE text_extracted = 0;
```
These papers still have metadata and abstract embeddings; full text just unavailable.

### FAISS / DB mismatch
```bash
.venv/bin/python scripts/verify_embeddings.py
```
Reports any embedding_idx values that don't match FAISS index.

## License

Private repository.
