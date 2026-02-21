# Daily Briefs

Automated arXiv paper ingestion, filtering, and daily briefing system.

## Overview

This system:
1. **Sources** papers from arXiv daily (with no misses)
2. **Enriches** metadata via Semantic Scholar + OpenAlex
3. **Filters** into 3 streams: Popular, Interest-based, Serendipity
4. **Reports** daily markdown briefs with TLDRs and rationale

## Directory Structure

```
daily-briefs/
├── config.yaml              # Categories, keywords, settings
├── data/
│   ├── arxiv/
│   │   ├── raw/             # Daily downloads (PDF, source, metadata)
│   │   │   └── YYYY-MM-DD/
│   │   │       └── <arxiv-id>/
│   │   │           ├── paper.pdf
│   │   │           ├── source.tar.gz
│   │   │           └── metadata.json
│   │   └── enriched/        # Enriched metadata (S2, OpenAlex)
│   └── index/
│       ├── papers.db        # SQLite index
│       └── papers.csv       # CSV export
├── reports/
│   └── YYYY-MM-DD.md        # Daily briefs
├── scripts/
│   ├── ingest.py            # arXiv ingestion
│   ├── enrich.py            # Metadata enrichment
│   ├── filter.py            # Filtering/ranking
│   └── report.py            # Report generation
├── templates/
│   └── daily_report.md      # Jinja2 template
└── feedback/
    └── ratings.csv          # Collected feedback
```

## Category Tiers

### Tier 1 (Primary - high recall)
- cs.AI, cs.CL, cs.CY, cs.LG, cs.NE, stat.ML

### Tier 2 (Secondary - high precision)
- cs.IR, cond-mat.dis-nn, cs.CE, cs.CV, cs.SI, cs.ET, cs.DL
- cs.GT, cs.HC, cs.MA, cs.OH, stat.TH, cs.GL
- physics.hist-ph, physics.soc-ph
- math.DS, math.IT, math.MG, math.OC, math.PR, math.ST

### Tier 3 (Tertiary - archive only)
- cs.RO, cs.PL, cs.SY
- q-bio.NC, q-bio.PE, q-bio.OT, q-bio.QM
- econ.EM, econ.GN, econ.TH
- eess.SY, eess.SP
- cond-mat.stat-mech
- nlin.AO, nlin.CD, nlin.CG

## Filtering Streams

1. **Popular** - High citation velocity, trending in field
2. **Interest** - Matches user profile, project keywords, embeddings
3. **Serendipity** - Near-misses, adjacent categories, random sampling

## Feedback Loop

Papers in daily briefs link to GitHub Issues for feedback:
- 👍 Useful
- 👎 Not useful
- 🤷 Marginal

We also sample:
- Papers that barely missed the filter
- Random negatives (for calibration)

## Usage

```bash
# Daily ingestion (run via cron)
python scripts/ingest.py --date 2026-02-21

# Enrich metadata
python scripts/enrich.py --date 2026-02-21

# Generate filtered report
python scripts/filter.py --date 2026-02-21
python scripts/report.py --date 2026-02-21
```

## Setup

```bash
# Create environment
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

## License

Private repository.
