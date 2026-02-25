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

## Cron Jobs: Remove LLM Dependency for Deterministic Tasks

**Problem:** The embeddings cron job uses `agentTurn` (isolated session) to spawn a full LLM agent that just runs shell commands:
1. `cd` to project dir
2. Run `embed.py --umap`
3. Check exit code
4. Curl healthcheck
5. Send Telegram notification

This is an expensive bash script interpreter. The task is completely deterministic — no interpretation needed.

**Options:**
1. **Add `--notify` flag to `embed.py`** — script handles healthcheck ping + Telegram notification itself (eliminates agent entirely)
2. **Create a shell wrapper script** — `run_embed.sh` does everything, cron just executes it
3. **Use `systemEvent` to main session** — cheaper model, but still uses LLM unnecessarily

**Recommendation:** Option 1 is cleanest. Scripts should be self-contained and handle their own notifications. The healthcheck URL and Telegram chat ID can come from config or env vars.

**Applies to:** Both `embed.py` and `run_daily.py` cron jobs.

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
