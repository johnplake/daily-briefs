#!/usr/bin/env python3
"""
Semantic search over paper embeddings.

Supports:
- Query search: Find papers matching a text query
- Similar papers: Find papers similar to a given paper
"""

import argparse
import json
import sqlite3
from pathlib import Path

import faiss
import numpy as np
from rich.console import Console
from rich.table import Table
from sentence_transformers import SentenceTransformer

console = Console()

MODEL_NAME = "sentence-transformers/allenai-specter"


def load_index(index_dir: Path) -> tuple:
    """Load FAISS index and paper ID mapping."""
    index_path = index_dir / "faiss.index"
    ids_path = index_dir / "paper_ids.json"
    
    if not index_path.exists() or not ids_path.exists():
        return None, [], {}
    
    index = faiss.read_index(str(index_path))
    with open(ids_path) as f:
        paper_ids = json.load(f)
    id_to_idx = {pid: idx for idx, pid in enumerate(paper_ids)}
    
    return index, paper_ids, id_to_idx


def get_paper_metadata(conn: sqlite3.Connection, arxiv_ids: list) -> dict:
    """Get metadata for papers by arxiv_id."""
    placeholders = ",".join("?" * len(arxiv_ids))
    cursor = conn.execute(
        f"SELECT arxiv_id, title, authors, primary_category FROM papers WHERE arxiv_id IN ({placeholders})",
        arxiv_ids
    )
    
    results = {}
    for row in cursor.fetchall():
        arxiv_id, title, authors, category = row
        results[arxiv_id] = {
            "title": title,
            "authors": json.loads(authors) if authors else [],
            "category": category,
        }
    return results


def search_by_query(query: str, model: SentenceTransformer, index: faiss.Index, 
                    paper_ids: list, k: int = 10) -> list:
    """Search for papers matching a text query."""
    # Encode query
    query_embedding = model.encode([query]).astype('float32')
    faiss.normalize_L2(query_embedding)
    
    # Search
    scores, indices = index.search(query_embedding, k)
    
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx >= 0:  # Valid index
            results.append({
                "arxiv_id": paper_ids[idx],
                "score": float(score),
            })
    
    return results


def search_similar(arxiv_id: str, index: faiss.Index, paper_ids: list, 
                   id_to_idx: dict, k: int = 10) -> list:
    """Find papers similar to a given paper."""
    if arxiv_id not in id_to_idx:
        return []
    
    idx = id_to_idx[arxiv_id]
    
    # Reconstruct the paper's embedding
    embedding = index.reconstruct(idx).reshape(1, -1)
    
    # Search (k+1 because the paper itself will be in results)
    scores, indices = index.search(embedding, k + 1)
    
    results = []
    for score, result_idx in zip(scores[0], indices[0]):
        if result_idx >= 0 and result_idx != idx:  # Skip self
            results.append({
                "arxiv_id": paper_ids[result_idx],
                "score": float(score),
            })
    
    return results[:k]


def display_results(results: list, metadata: dict):
    """Display search results in a table."""
    table = Table(title="Search Results")
    table.add_column("Score", style="cyan", width=6)
    table.add_column("arXiv ID", style="green", width=12)
    table.add_column("Title", style="white", max_width=60)
    table.add_column("Category", style="yellow", width=10)
    
    for r in results:
        arxiv_id = r["arxiv_id"]
        score = f"{r['score']:.3f}"
        meta = metadata.get(arxiv_id, {})
        title = meta.get("title", "Unknown")[:60]
        category = meta.get("category", "")
        
        table.add_row(score, arxiv_id, title, category)
    
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Search paper embeddings")
    parser.add_argument("--query", "-q", help="Text query to search for")
    parser.add_argument("--similar", "-s", help="Find papers similar to this arXiv ID")
    parser.add_argument("--k", type=int, default=10, help="Number of results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    if not args.query and not args.similar:
        console.print("[red]Provide --query or --similar[/red]")
        return
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    index_dir = project_root / "data" / "embeddings"
    
    # Load index
    index, paper_ids, id_to_idx = load_index(index_dir)
    
    if index is None:
        console.print("[red]No embedding index found. Run embed.py first.[/red]")
        return
    
    console.print(f"[cyan]Index contains {len(paper_ids)} papers[/cyan]")
    
    # Search
    if args.query:
        console.print(f"[cyan]Loading model for query encoding...[/cyan]")
        model = SentenceTransformer(MODEL_NAME)
        results = search_by_query(args.query, model, index, paper_ids, args.k)
    else:
        results = search_similar(args.similar, index, paper_ids, id_to_idx, args.k)
        if not results:
            console.print(f"[yellow]Paper {args.similar} not found in index[/yellow]")
            return
    
    # Get metadata
    conn = sqlite3.connect(db_path)
    arxiv_ids = [r["arxiv_id"] for r in results]
    metadata = get_paper_metadata(conn, arxiv_ids)
    conn.close()
    
    # Output
    if args.json:
        for r in results:
            r.update(metadata.get(r["arxiv_id"], {}))
        print(json.dumps(results, indent=2))
    else:
        display_results(results, metadata)


if __name__ == "__main__":
    main()
