# Embeddings Visualization with Dash + Plotly

Interactive 2D visualization of paper embeddings.

## Features

✅ **Interactive scatter plot** - zoom, pan, lasso select  
✅ **Instant filtering** - by stream (Popular/Interest/Serendipity) and date  
✅ **Hover details** - see paper title, authors, arXiv ID, score  
✅ **Click for full details** - sidebar shows complete paper info + abstract  
✅ **Color-coded by stream** - visual clustering  

## Quick Start

### 1. Install dependencies

```bash
cd Projects/daily-briefs
.venv/bin/pip install -r requirements-viz.txt
```

### 2. Prepare data (if you have real embeddings)

```bash
# From high-dimensional embeddings + metadata → 2D visualization data
.venv/bin/python scripts/prepare_viz_data.py \
  --embeddings data/embeddings.npy \
  --metadata data/papers.jsonl \
  --method tsne \
  --perplexity 30
```

**Input files:**
- `data/embeddings.npy` - NumPy array of shape `(n_papers, embedding_dim)`
- `data/papers.jsonl` - One JSON object per line with paper metadata

**Output:**
- `data/embeddings_2d.json` - 2D coordinates + metadata for visualization

### 3. Run the app

```bash
.venv/bin/python app.py
```

Open http://localhost:8050 in your browser.

## Using Demo Data

If you don't have embeddings yet, `app.py` will auto-generate 300 synthetic papers for demo purposes.

## Customization

### Change dimensionality reduction

```bash
# Use PCA instead of t-SNE (faster but less clustering)
python scripts/prepare_viz_data.py --method pca

# Adjust t-SNE perplexity (controls cluster size)
python scripts/prepare_viz_data.py --method tsne --perplexity 50
```

### Styling

Edit `app.py`:
- Line 180-185: Color scheme for streams
- Line 194-198: Marker size, opacity
- Line 205-209: Layout background colors

### Add more filters

Add new dropdowns/sliders in the layout (line 90-110) and connect with `@app.callback`.

## Data Format

`data/embeddings_2d.json` structure:

```json
[
  {
    "x": 0.234,
    "y": -1.567,
    "title": "Paper title",
    "authors": "Author et al.",
    "arxiv_id": "2601.00123",
    "date": "2026-01-15",
    "stream": "Popular",
    "categories": "cs.LG",
    "score": 0.85,
    "abstract": "Paper abstract..."
  },
  ...
]
```

## Integration with Daily Briefs Pipeline

To generate embeddings for visualization:

1. Run your embedding script (`scripts/embed.py`)
2. Run `prepare_viz_data.py` to reduce to 2D
3. Launch `app.py` to visualize

Example workflow:

```bash
# Generate embeddings for today's papers
.venv/bin/python scripts/embed.py

# Reduce to 2D
.venv/bin/python scripts/prepare_viz_data.py

# Visualize
.venv/bin/python app.py
```

## Production Deployment

For deployment (not just local):

```bash
# Install gunicorn
pip install gunicorn

# Run with gunicorn
gunicorn app:server -b 0.0.0.0:8050
```

Or use any WSGI server (the Dash app exposes `server` = Flask app).

## Tips

- **Performance:** For >5000 papers, consider using Datashader or WebGL scatter
- **Perplexity:** Start with 30, increase for larger datasets (50-100 for 10k+ papers)
- **PCA first:** For very high-dim embeddings (>1000), do PCA→100 then t-SNE→2
- **Update frequency:** Regenerate 2D embeddings weekly (t-SNE is stochastic)

## Troubleshooting

**"No module named 'sklearn'"**
```bash
pip install scikit-learn
```

**"ValueError: perplexity must be less than n_samples"**
Reduce `--perplexity` or ensure you have enough papers.

**App is slow with many papers**
- Use PCA instead of t-SNE
- Reduce marker size in scatter plot
- Consider downsampling for initial view
