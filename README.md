# Daily Briefs

Automated arXiv paper ingestion, filtering, and daily briefing system.

## Overview

This system:
1. **Sources** papers from arXiv RSS feeds daily (all specified categories, no misses)
2. **Extracts** full text from PDFs (saves ~75KB/paper instead of ~3MB)
3. **Enriches** with citation data via Semantic Scholar + OpenAlex
4. **Embeds** papers using SPECTER for semantic search
5. **Filters** into 3 streams: Popular, Interest-based, Serendipity
6. **Reports** daily markdown briefs with TLDRs and feedback links

## Directory Structure

```
daily-briefs/
├── config.yaml                  # Categories, keywords, thresholds
├── requirements.txt             # Python dependencies
├── data/
│   ├── arxiv/raw/               # Daily paper storage
│   │   └── YYYY-MM-DD/
│   │       └── <arxiv-id>/
│   │           ├── metadata.json    # arXiv metadata
│   │           ├── paper.txt        # Extracted full text
│   │           └── citations.json   # S2 + OpenAlex citation data
│   ├── filtered/                # Daily filtered results
│   │   └── YYYY-MM-DD.json
│   ├── index/
│   │   └── papers.db            # SQLite index
│   └── embeddings/
│       ├── faiss.index          # SPECTER embeddings (~4KB/paper)
│       └── paper_ids.json       # arxiv_id ↔ index mapping
├── reports/
│   └── YYYY-MM-DD.md            # Daily briefs
├── scripts/
│   ├── ingest.py                # arXiv RSS ingestion + text extraction
│   ├── enrich.py                # Citation metadata (S2 + OpenAlex)
│   ├── embed.py                 # SPECTER embeddings + FAISS index
│   ├── search.py                # Semantic search (query or similar)
│   ├── filter.py                # 3-stream filtering
│   ├── report.py                # Markdown report generation
│   ├── run_daily.py             # Full pipeline runner
│   └── backfill_text.py         # Backfill text for existing papers
├── feedback/
│   └── ratings.csv              # Collected feedback
└── .github/
    └── ISSUE_TEMPLATE/
        └── paper-feedback.yml   # Feedback issue template
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

### Daily Pipeline (via cron)
```bash
# Full pipeline: ingest → filter → report
.venv/bin/python scripts/ingest.py --extract-text --no-source
.venv/bin/python scripts/filter.py --date $(date +%Y-%m-%d)
.venv/bin/python scripts/report.py --date $(date +%Y-%m-%d)

# Or use the runner:
.venv/bin/python scripts/run_daily.py
```

### Embeddings (separate cron, 11:50pm Mon-Fri)
```bash
.venv/bin/python scripts/embed.py
```

### Citation Enrichment (optional, can re-run to update)
```bash
.venv/bin/python scripts/enrich.py --date 2026-02-21
.venv/bin/python scripts/enrich.py --update  # Re-enrich all
```

### Semantic Search
```bash
# Search by query
.venv/bin/python scripts/search.py --query "transformer attention mechanism"

# Find similar papers
.venv/bin/python scripts/search.py --similar 2602.16802
```

### Backfill Text (for papers ingested before text extraction)
```bash
.venv/bin/python scripts/backfill_text.py
```

## Cron Schedule

| Job | Schedule (CST) | What |
|-----|----------------|------|
| Daily Brief | 8am Mon-Fri | Ingest → filter → report → Telegram summary |
| Embeddings | 11:50pm Mon-Fri | Embed new papers into FAISS index |

Note: arXiv announces papers Sun-Thu at 8pm ET. Running at 8am CST captures the previous night's announcement.

## Setup

```bash
cd Projects/daily-briefs

# Create environment
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

## Storage Estimates

| Asset | Size/paper | Daily (~1500 papers) | Monthly |
|-------|------------|----------------------|---------|
| metadata.json | ~5KB | ~7MB | ~150MB |
| paper.txt | ~75KB | ~110MB | ~2.2GB |
| citations.json | ~1KB | ~1.5MB | ~30MB |
| FAISS embedding | ~4KB | ~6MB | ~120MB |
| **Total** | ~85KB | ~125MB | ~2.5GB |

## Feedback

Papers in daily briefs include feedback links (GitHub Issues). Rate papers:
- 👍 Useful
- 🤷 Marginal
- 👎 Not useful

Feedback improves the filtering model over time.

## License

Private repository.
