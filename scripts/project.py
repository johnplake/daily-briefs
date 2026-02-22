#!/usr/bin/env python3
"""
UMAP projection script.

Projects paper embeddings from 768-dim to 2D for visualization.
Refits on all papers each run (coordinates may shift between runs).

Stores results in umap_x, umap_y columns in the papers table.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import faiss
import numpy as np
from rich.console import Console
from umap import UMAP

from config import PROJECT_ROOT, DB_PATH, EMBEDDINGS_DIR, EMBEDDINGS, get_db_connection, validate_date

console = Console()

# Derived paths
INDEX_PATH = EMBEDDINGS_DIR / "faiss.index"

# UMAP defaults from config
UMAP_N_NEIGHBORS = EMBEDDINGS["umap_n_neighbors"]
UMAP_MIN_DIST = EMBEDDINGS["umap_min_dist"]
UMAP_RANDOM_STATE = EMBEDDINGS["umap_random_state"]


def load_embeddings_and_ids(conn: sqlite3.Connection) -> tuple[np.ndarray, list]:
    """
    Load all embeddings from FAISS and their corresponding paper IDs.
    
    Returns (embeddings array, list of (db_id, embedding_idx) tuples).
    """
    if not INDEX_PATH.exists():
        raise RuntimeError(f"FAISS index not found at {INDEX_PATH}. Run embed.py first.")
    
    # Load FAISS index
    index = faiss.read_index(str(INDEX_PATH))
    n_vectors = index.ntotal
    
    console.print(f"[cyan]Loaded FAISS index with {n_vectors} vectors[/cyan]")
    
    # Get paper IDs with their embedding indices
    cursor = conn.execute("""
        SELECT id, embedding_idx 
        FROM papers 
        WHERE embedding_idx IS NOT NULL
        ORDER BY embedding_idx
    """)
    papers = cursor.fetchall()
    
    if len(papers) != n_vectors:
        console.print(f"[red]Error: {len(papers)} papers with embeddings, but {n_vectors} vectors in FAISS index[/red]")
        console.print("[red]This indicates a mismatch between DB and index. Run embed.py --rebuild to fix.[/red]")
        raise RuntimeError(f"DB/FAISS mismatch: {len(papers)} papers vs {n_vectors} vectors")
    
    # Guardrail: ensure embedding_idx values are contiguous 0..n_vectors-1
    if n_vectors > 0:
        idxs = [row["embedding_idx"] for row in papers]
        if min(idxs) != 0 or max(idxs) != n_vectors - 1 or len(set(idxs)) != n_vectors:
            console.print("[red]Error: embedding_idx values are not contiguous (0..n-1).[/red]")
            console.print("[red]FAISS reconstruct_n assumes contiguous indices; run embed.py --rebuild to fix.[/red]")
            raise RuntimeError("Non-contiguous embedding_idx values detected")
    
    # Get all embeddings (safe now that indices are contiguous)
    embeddings = index.reconstruct_n(0, n_vectors)
    
    return embeddings, [(row["id"], row["embedding_idx"]) for row in papers]


def run_umap(embeddings: np.ndarray, n_neighbors: int = None, 
             min_dist: float = None, random_state: int = None) -> np.ndarray:
    """
    Run UMAP dimensionality reduction.
    
    Returns 2D coordinates array.
    """
    # Apply defaults from config
    if n_neighbors is None:
        n_neighbors = UMAP_N_NEIGHBORS
    if min_dist is None:
        min_dist = UMAP_MIN_DIST
    if random_state is None:
        random_state = UMAP_RANDOM_STATE
        
    console.print(f"[cyan]Running UMAP (n_neighbors={n_neighbors}, min_dist={min_dist})...[/cyan]")
    
    reducer = UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric='cosine',  # Good for normalized embeddings
        random_state=random_state,
        verbose=True
    )
    
    coords_2d = reducer.fit_transform(embeddings)
    
    return coords_2d


def update_coordinates(conn: sqlite3.Connection, paper_ids: list, coords: np.ndarray):
    """
    Update umap_x, umap_y columns in database.
    
    paper_ids: list of (db_id, embedding_idx) tuples
    coords: 2D array of shape (n_papers, 2)
    """
    console.print(f"[cyan]Updating {len(paper_ids)} papers with 2D coordinates...[/cyan]")
    
    cursor = conn.cursor()
    
    for (db_id, embedding_idx), (x, y) in zip(paper_ids, coords):
        cursor.execute(
            "UPDATE papers SET umap_x = ?, umap_y = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (float(x), float(y), db_id)
        )
    
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Project embeddings to 2D with UMAP")
    parser.add_argument("--n-neighbors", type=int, default=UMAP_N_NEIGHBORS, 
                        help=f"UMAP n_neighbors parameter (default: {UMAP_N_NEIGHBORS})")
    parser.add_argument("--min-dist", type=float, default=UMAP_MIN_DIST,
                        help=f"UMAP min_dist parameter (default: {UMAP_MIN_DIST})")
    parser.add_argument("--random-state", type=int, default=UMAP_RANDOM_STATE,
                        help=f"Random state for reproducibility (default: {UMAP_RANDOM_STATE})")
    args = parser.parse_args()
    
    console.print("[bold green]UMAP Projection[/bold green]")
    
    conn = get_db_connection()
    
    # Load embeddings
    embeddings, paper_ids = load_embeddings_and_ids(conn)
    
    if len(embeddings) == 0:
        console.print("[yellow]No embeddings found. Run embed.py first.[/yellow]")
        conn.close()
        return
    
    # Run UMAP
    coords = run_umap(
        embeddings, 
        n_neighbors=args.n_neighbors,
        min_dist=args.min_dist,
        random_state=args.random_state
    )
    
    # Update database
    update_coordinates(conn, paper_ids, coords)
    
    conn.close()
    
    console.print(f"\n[bold green]✓ Projected {len(paper_ids)} papers to 2D[/bold green]")
    
    # Print coordinate ranges for sanity check
    x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
    y_min, y_max = coords[:, 1].min(), coords[:, 1].max()
    console.print(f"  X range: [{x_min:.2f}, {x_max:.2f}]")
    console.print(f"  Y range: [{y_min:.2f}, {y_max:.2f}]")


if __name__ == "__main__":
    main()
