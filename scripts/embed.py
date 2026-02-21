#!/usr/bin/env python3
"""
Paper embedding script.

Generates SPECTER embeddings for papers and builds a FAISS index
for fast similarity search.

Embeddings are computed from title + abstract.
The embedding_idx column in the database maps papers to FAISS index positions.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def validate_date(date_str: str) -> str:
    """Validate date format YYYY-MM-DD. Returns the date or exits with error."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        print(f"Error: Invalid date format '{date_str}'. Expected YYYY-MM-DD.")
        sys.exit(1)

import faiss
import numpy as np
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from sentence_transformers import SentenceTransformer

console = Console()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "papers.db"
INDEX_DIR = PROJECT_ROOT / "data" / "embeddings"

# SPECTER model for scientific papers
MODEL_NAME = "sentence-transformers/allenai-specter"
EMBEDDING_DIM = 768


def get_db_connection() -> sqlite3.Connection:
    """Get database connection."""
    if not DB_PATH.exists():
        raise RuntimeError(f"Database not found at {DB_PATH}. Run init_db.py first.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_current_index_size() -> int:
    """Get the current size of the FAISS index (number of vectors)."""
    index_path = INDEX_DIR / "faiss.index"
    if index_path.exists():
        try:
            index = faiss.read_index(str(index_path))
            return index.ntotal
        except Exception:
            return 0
    return 0


def get_papers_to_embed(conn: sqlite3.Connection, date_filter: str = None,
                        limit: int = None) -> list:
    """Get papers that need embedding (have text but no embedding_idx)."""
    query = """
        SELECT id, paper_id, title, abstract, paper_source, announced_date
        FROM papers
        WHERE embedding_idx IS NULL
          AND (abstract IS NOT NULL AND abstract != '')
    """
    
    if date_filter:
        query += f" AND announced_date = '{date_filter}'"
    
    query += " ORDER BY announced_date DESC, id"
    
    if limit:
        query += f" LIMIT {limit}"
    
    cursor = conn.execute(query)
    return cursor.fetchall()


def get_paper_text(paper: dict) -> str:
    """
    Get text to embed for a paper.
    Uses title + abstract (SPECTER is trained on this).
    """
    title = paper["title"] or ""
    abstract = paper["abstract"] or ""
    
    # SPECTER expects title [SEP] abstract format
    return f"{title} [SEP] {abstract}"


def load_or_create_index() -> faiss.Index:
    """Load existing FAISS index or create new one."""
    index_path = INDEX_DIR / "faiss.index"
    
    if index_path.exists():
        try:
            index = faiss.read_index(str(index_path))
            console.print(f"[cyan]Loaded existing index with {index.ntotal} vectors[/cyan]")
            return index
        except Exception as e:
            console.print(f"[yellow]Failed to load index: {e}. Creating new.[/yellow]")
    
    # Create new index (Inner Product = cosine similarity on normalized vectors)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    console.print("[cyan]Created new FAISS index[/cyan]")
    return index


def save_index(index: faiss.Index):
    """Save FAISS index."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    index_path = INDEX_DIR / "faiss.index"
    faiss.write_index(index, str(index_path))


def main():
    parser = argparse.ArgumentParser(description="Generate paper embeddings")
    parser.add_argument("--date", help="Only embed papers from this date (YYYY-MM-DD)")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size for embedding")
    parser.add_argument("--limit", type=int, help="Limit number of papers to embed")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild entire index from scratch")
    args = parser.parse_args()
    
    console.print("[bold green]Paper Embedding with SPECTER[/bold green]")
    
    conn = get_db_connection()
    
    # Handle rebuild
    if args.rebuild:
        console.print("[yellow]Rebuilding index from scratch[/yellow]")
        # Clear all embedding_idx values
        conn.execute("UPDATE papers SET embedding_idx = NULL")
        conn.commit()
        # Delete existing index
        index_path = INDEX_DIR / "faiss.index"
        if index_path.exists():
            index_path.unlink()
    
    # Validate date if provided
    date_filter = validate_date(args.date) if args.date else None
    
    # Get current index size (this will be the starting position for new embeddings)
    start_idx = get_current_index_size()
    
    # Get papers to embed
    papers = get_papers_to_embed(conn, date_filter, args.limit)
    
    if not papers:
        console.print("[yellow]No papers need embedding.[/yellow]")
        conn.close()
        return
    
    console.print(f"Papers to embed: {len(papers)}")
    console.print(f"Starting index position: {start_idx}")
    
    # Load model
    console.print(f"[cyan]Loading model: {MODEL_NAME}[/cyan]")
    model = SentenceTransformer(MODEL_NAME)
    
    # Prepare texts
    paper_data = [dict(row) for row in papers]
    texts = [get_paper_text(p) for p in paper_data]
    
    # Generate embeddings in batches
    console.print(f"[cyan]Generating embeddings (batch size: {args.batch_size})...[/cyan]")
    
    all_embeddings = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Embedding...", total=len(texts))
        
        for i in range(0, len(texts), args.batch_size):
            batch = texts[i:i + args.batch_size]
            batch_embeddings = model.encode(batch, show_progress_bar=False)
            all_embeddings.append(batch_embeddings)
            progress.update(task, advance=len(batch), 
                          description=f"Embedding {min(i + args.batch_size, len(texts))}/{len(texts)}...")
    
    # Combine embeddings
    new_embeddings = np.vstack(all_embeddings).astype('float32')
    
    # Normalize for cosine similarity
    faiss.normalize_L2(new_embeddings)
    
    # Load or create index
    index = load_or_create_index()
    
    # Add new embeddings
    index.add(new_embeddings)
    
    # Update database with embedding indices
    console.print("[cyan]Updating database...[/cyan]")
    for i, paper in enumerate(paper_data):
        embedding_idx = start_idx + i
        conn.execute(
            "UPDATE papers SET embedding_idx = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (embedding_idx, paper["id"])
        )
    
    conn.commit()
    conn.close()
    
    # Save index
    save_index(index)
    
    console.print(f"\n[bold green]✓ Embedded {len(papers)} papers[/bold green]")
    console.print(f"[bold green]✓ Index now contains {index.ntotal} vectors[/bold green]")
    console.print(f"[bold green]✓ Saved to {INDEX_DIR}[/bold green]")


if __name__ == "__main__":
    main()
