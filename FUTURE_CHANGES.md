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

(Placeholder — add as we go)
