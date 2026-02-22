#!/usr/bin/env python3
"""
Semantic search over paper embeddings.

Supports:
- Query search: Find papers matching a text query
- Similar papers: Find papers similar to a given paper
- Full-text search: Keyword search in titles and abstracts
"""

import argparse
import json
import sqlite3
from pathlib import Path

import faiss
from rich.console import Console
from rich.table import Table
from sentence_transformers import SentenceTransformer

from config import PROJECT_ROOT, DB_PATH, EMBEDDINGS_DIR, get_db_connection

console = Console()

# Use EMBEDDINGS_DIR from config
INDEX_DIR = EMBEDDINGS_DIR

MODEL_NAME = "sentence-transformers/allenai-specter"


def load_index() -> faiss.Index | None:
    """Load FAISS index."""
    index_path = INDEX_DIR / "faiss.index"
    
    if not index_path.exists():
        return None
    
    return faiss.read_index(str(index_path))


def get_papers_by_embedding_idx(conn: sqlite3.Connection, indices: list) -> dict:
    """Get paper metadata by embedding_idx values."""
    if not indices:
        return {}
    
    placeholders = ",".join("?" * len(indices))
    cursor = conn.execute(
        f"""SELECT embedding_idx, paper_id, title, authors, primary_category, 
                   announced_date, arxiv_url, citations_s2
            FROM papers 
            WHERE embedding_idx IN ({placeholders})
              AND hidden = 0""",
        indices
    )
    
    results = {}
    for row in cursor.fetchall():
        results[row["embedding_idx"]] = dict(row)
    return results


def get_paper_by_id(conn: sqlite3.Connection, paper_id: str) -> dict | None:
    """Get paper by paper_id."""
    cursor = conn.execute(
        "SELECT * FROM papers WHERE paper_id = ? AND hidden = 0",
        (paper_id,)
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def search_by_query(query: str, model: SentenceTransformer, index: faiss.Index,
                    conn: sqlite3.Connection, k: int = 10) -> list:
    """Search for papers matching a text query."""
    # Encode query
    query_embedding = model.encode([query]).astype('float32')
    faiss.normalize_L2(query_embedding)
    
    # Search FAISS
    scores, indices = index.search(query_embedding, k)
    
    # Get paper metadata
    valid_indices = [int(idx) for idx in indices[0] if idx >= 0]
    papers_by_idx = get_papers_by_embedding_idx(conn, valid_indices)
    
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx >= 0 and idx in papers_by_idx:
            paper = papers_by_idx[idx]
            paper["score"] = float(score)
            results.append(paper)
    
    return results


def search_similar(paper_id: str, index: faiss.Index, conn: sqlite3.Connection,
                   k: int = 10) -> list:
    """Find papers similar to a given paper."""
    paper = get_paper_by_id(conn, paper_id)
    
    if paper is None:
        console.print(f"[yellow]Paper {paper_id} not found in database[/yellow]")
        return []
    
    if paper["embedding_idx"] is None:
        console.print(f"[yellow]Paper {paper_id} has no embedding[/yellow]")
        return []
    
    # Reconstruct the paper's embedding
    embedding = index.reconstruct(paper["embedding_idx"]).reshape(1, -1)
    
    # Search (k+1 because the paper itself will be in results)
    scores, indices = index.search(embedding, k + 1)
    
    # Get paper metadata (excluding the query paper)
    valid_indices = [int(idx) for idx in indices[0] if idx >= 0 and idx != paper["embedding_idx"]]
    papers_by_idx = get_papers_by_embedding_idx(conn, valid_indices[:k])
    
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx >= 0 and idx != paper["embedding_idx"] and idx in papers_by_idx:
            paper_result = papers_by_idx[idx]
            paper_result["score"] = float(score)
            results.append(paper_result)
    
    return results[:k]


def search_fulltext(query: str, conn: sqlite3.Connection, k: int = 10) -> list:
    """Full-text search using FTS5."""
    cursor = conn.execute(
        """SELECT p.*, bm25(papers_fts) as score 
           FROM papers p
           JOIN papers_fts fts ON p.id = fts.rowid
           WHERE papers_fts MATCH ?
           ORDER BY score
           LIMIT ?""",
        (query, k)
    )
    
    return [dict(row) for row in cursor.fetchall()]


def display_results(results: list, show_score: bool = True):
    """Display search results in a table."""
    table = Table(title="Search Results")
    if show_score:
        table.add_column("Score", style="cyan", width=6)
    table.add_column("Paper ID", style="green", width=12)
    table.add_column("Title", style="white", max_width=55)
    table.add_column("Category", style="yellow", width=8)
    table.add_column("Date", style="blue", width=10)
    
    for r in results:
        row_data = []
        if show_score:
            row_data.append(f"{r.get('score', 0):.3f}")
        row_data.extend([
            r.get("paper_id", ""),
            (r.get("title", "")[:55] + "...") if len(r.get("title", "")) > 55 else r.get("title", ""),
            r.get("primary_category", ""),
            r.get("announced_date", ""),
        ])
        table.add_row(*row_data)
    
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Search paper embeddings")
    parser.add_argument("--query", "-q", help="Semantic search query")
    parser.add_argument("--similar", "-s", help="Find papers similar to this paper ID")
    parser.add_argument("--fulltext", "-f", help="Full-text keyword search")
    parser.add_argument("--k", type=int, default=10, help="Number of results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    
    if not args.query and not args.similar and not args.fulltext:
        console.print("[red]Provide --query, --similar, or --fulltext[/red]")
        return
    
    conn = get_db_connection()
    
    # Handle full-text search (doesn't need embeddings)
    if args.fulltext:
        results = search_fulltext(args.fulltext, conn, args.k)
        if not results:
            console.print("[yellow]No results found[/yellow]")
            return
        
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            display_results(results, show_score=True)
        conn.close()
        return
    
    # Load FAISS index for semantic search
    index = load_index()
    
    if index is None:
        console.print("[red]No embedding index found. Run embed.py first.[/red]")
        return
    
    console.print(f"[cyan]Index contains {index.ntotal} vectors[/cyan]")
    
    # Semantic search
    if args.query:
        console.print(f"[cyan]Loading model for query encoding...[/cyan]")
        model = SentenceTransformer(MODEL_NAME)
        results = search_by_query(args.query, model, index, conn, args.k)
    else:
        results = search_similar(args.similar, index, conn, args.k)
    
    conn.close()
    
    if not results:
        console.print("[yellow]No results found[/yellow]")
        return
    
    # Output
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        display_results(results)


if __name__ == "__main__":
    main()
