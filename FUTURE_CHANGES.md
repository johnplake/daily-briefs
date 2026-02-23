# Future Changes / Ideas (Daily Briefs)

This file captures ideas and potential improvements for the future.

---

## Dashboard Scalability (10k–1M+ papers)

The current Dash/Plotly scatter plot renders **all papers with UMAP coords**. This is fine for hundreds/thousands, but will not scale to 10k–1M papers. When we reach larger scale, we will likely need to change the visualization strategy.

**Potential approaches:**
- **Server‑side aggregation / tiling** (send only visible points at current zoom level)
- **WebGL‑based rendering** (Plotly supports WebGL scatter for larger datasets, but still has limits)
- **Rasterized map tiles** (precompute density tiles, overlay interactive selection)
- **Progressive loading / LOD** (level‑of‑detail: show clusters or density when zoomed out; individual points only when zoomed in)
- **Spatial indexing** (e.g., HNSW or R‑tree for viewport queries)

**Inspiration:**
- https://bluesky-map.theo.io/
- https://joelgustafson.com/posts/2024-11-12/visualizing-13-million-bluesky-users/

---

## Other Potential Improvements

- **Add integration tests** (smoke test: init_db → ingest --dry-run → filter --dry-run → report --dry-run)
- Add DB integrity tests (FTS sync, FAISS size match)

---

## Backup Strategy (Document + Optional Automation)

**Goal:** Prevent catastrophic data loss from SQLite corruption or deletion.

### Recommended approach
- Create a `backups/` folder **outside git** (add to `.gitignore`).
- Use SQLite’s built‑in `.backup` command (safe with WAL):

```bash
mkdir -p backups
sqlite3 data/papers.db ".backup 'backups/papers-$(date +%F).db'"
```

- Keep last **N** backups (e.g., 7 or 30). Example cleanup:

```bash
ls -t backups/papers-*.db | tail -n +8 | xargs -r rm
```

### Automation (optional)
- Add a daily/weekly cron job to run the backup command.
- If using cloud storage, sync `backups/` to external storage.

---

## Multi‑Source Ingestion Refactor (ArXiv + others)

**Goal:** Support ingestion from multiple sources (bioRxiv, SSRN, Crossref, etc.) with a clean, pluggable architecture.

### Proposed structure
```
sources/
  arxiv.py
  biorxiv.py
  ssrn.py
```

### Source interface
Each source module implements:
```python
def fetch(source_config, date) -> list[dict]:
    # returns list of normalized paper dicts
```

### Normalized schema (all sources)
```python
{
  "paper_source": "arxiv",
  "paper_id": "...",
  "announced_date": "YYYY-MM-DD",
  "title": "...",
  "abstract": "...",
  "authors": JSON,
  "primary_category": "...",
  "categories": JSON,
  "arxiv_url/pdf_url/doi": ...
}
```

### Ingest pipeline
- `ingest.py` loops through configured sources
- Each source returns normalized dicts
- `upsert_paper()` handles storage and deduplication

### Date normalization helper
Add a generic helper:
```python
def normalize_date(date_str):
    # try RFC2822, then ISO, then YYYY/MM/DD, else None
```
This should live in `utils.py` and be reused by all sources.

### Notes
- Current code is **arXiv‑only** (RSS + arXiv metadata).
- Refactor first, then add new sources incrementally.
- Each source should own parsing + date normalization.
