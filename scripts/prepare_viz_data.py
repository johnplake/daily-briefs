#!/usr/bin/env python3
"""
Convert embeddings and metadata into format for Dash visualization.

Reads:
- data/embeddings.npy (or .json) - high-dimensional embeddings
- data/papers.jsonl - paper metadata

Outputs:
- data/embeddings_2d.json - 2D embeddings + metadata for visualization
"""

import json
import numpy as np
from pathlib import Path
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
import argparse

def load_embeddings(path):
    """Load embeddings from .npy or .json file."""
    path = Path(path)
    if path.suffix == '.npy':
        return np.load(path)
    elif path.suffix == '.json':
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return np.array(data)
        elif isinstance(data, dict) and 'embeddings' in data:
            return np.array(data['embeddings'])
    raise ValueError(f"Unsupported format: {path.suffix}")

def load_metadata(path):
    """Load paper metadata from JSONL."""
    papers = []
    with open(path) as f:
        for line in f:
            papers.append(json.loads(line))
    return papers

def reduce_dimensions(embeddings, method='tsne', n_components=2, perplexity=30, random_state=42):
    """
    Reduce embeddings to 2D.
    
    Args:
        method: 'tsne' or 'pca'
        n_components: 2 for visualization
        perplexity: t-SNE perplexity (typical: 5-50)
    """
    print(f"Reducing {embeddings.shape} to 2D using {method.upper()}...")
    
    if method == 'tsne':
        reducer = TSNE(
            n_components=n_components,
            perplexity=min(perplexity, len(embeddings) - 1),
            random_state=random_state,
            n_jobs=-1
        )
    elif method == 'pca':
        reducer = PCA(n_components=n_components, random_state=random_state)
    else:
        raise ValueError(f"Unknown method: {method}")
    
    coords = reducer.fit_transform(embeddings)
    print(f"Done. Explained variance: {reducer.explained_variance_ratio_ if method == 'pca' else 'N/A'}")
    return coords

def combine_data(coords, papers):
    """Combine 2D coordinates with paper metadata."""
    assert len(coords) == len(papers), "Mismatch between embeddings and metadata"
    
    combined = []
    for (x, y), paper in zip(coords, papers):
        combined.append({
            'x': float(x),
            'y': float(y),
            'title': paper.get('title', 'Unknown'),
            'authors': paper.get('authors', 'Unknown'),
            'arxiv_id': paper.get('arxiv_id', paper.get('id', 'unknown')),
            'date': paper.get('published', paper.get('date', 'unknown')),
            'categories': paper.get('categories', 'unknown'),
            'stream': paper.get('stream', 'unknown'),
            'score': paper.get('score', 0.0),
            'abstract': paper.get('summary', paper.get('abstract', ''))[:500],  # Truncate
        })
    return combined

def main():
    parser = argparse.ArgumentParser(description='Prepare embeddings for visualization')
    parser.add_argument('--embeddings', default='data/embeddings.npy', help='Path to embeddings file')
    parser.add_argument('--metadata', default='data/papers.jsonl', help='Path to metadata file')
    parser.add_argument('--output', default='data/embeddings_2d.json', help='Output path')
    parser.add_argument('--method', choices=['tsne', 'pca'], default='tsne', help='Dimensionality reduction method')
    parser.add_argument('--perplexity', type=int, default=30, help='t-SNE perplexity')
    args = parser.parse_args()
    
    # Load data
    print(f"Loading embeddings from {args.embeddings}...")
    embeddings = load_embeddings(args.embeddings)
    print(f"  Shape: {embeddings.shape}")
    
    print(f"Loading metadata from {args.metadata}...")
    papers = load_metadata(args.metadata)
    print(f"  Loaded {len(papers)} papers")
    
    # Reduce dimensions
    coords = reduce_dimensions(embeddings, method=args.method, perplexity=args.perplexity)
    
    # Combine
    combined = combine_data(coords, papers)
    
    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(combined, f, indent=2)
    
    print(f"✅ Saved {len(combined)} papers to {output_path}")
    print(f"\nTo run the visualization:")
    print(f"  python app.py")
    print(f"  Then open http://localhost:8050")

if __name__ == '__main__':
    main()
