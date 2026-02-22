#!/usr/bin/env python3
"""
Paper embedding script.

Generates SPECTER embeddings for papers and builds a FAISS index
for fast similarity search.

Embeddings are computed from title + abstract.
The embedding_idx column in the database maps papers to FAISS index positions.

Optionally runs UMAP projection to 2D after embedding (--umap flag).
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import faiss
import numpy as np
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from sentence_transformers import SentenceTransformer

from config import PROJECT_ROOT, DB_PATH, EMBEDDINGS_DIR, EMBEDDINGS, get_db_connection, validate_date

# Import UMAP functions from project.py
# NOTE: Avoid importing embed.py from project.py to prevent circular imports.
from project import load_embeddings_and_ids, run_umap, update_coordinates

console = Console()

# Use EMBEDDINGS_DIR from config
INDEX_DIR = EMBEDDINGS_DIR

# Embedding model for scientific papers
MODEL_NAME = EMBEDDINGS["model_name"]
EMBEDDING_DIM = EMBEDDINGS["dimension"]


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
    params = []
    
    if date_filter:
        query += " AND announced_date = ?"
        params.append(date_filter)
    
    query += " ORDER BY announced_date DESC, id"
    
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    
    cursor = conn.execute(query, params)
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


def validate_index_alignment(conn: sqlite3.Connection, index: faiss.Index) -> None:
    """Ensure DB embedding_idx values align with FAISS index (contiguous 0..n-1)."""
    n_vectors = index.ntotal
    
    cursor = conn.execute(
        "SELECT embedding_idx FROM papers WHERE embedding_idx IS NOT NULL ORDER BY embedding_idx"
    )
    rows = cursor.fetchall()
    
    if len(rows) != n_vectors:
        raise RuntimeError(
            f"DB/FAISS mismatch: {len(rows)} embeddings in DB vs {n_vectors} vectors in FAISS. "
            "Run embed.py --rebuild to fix."
        )
    
    if n_vectors == 0:
        return
    
    idxs = [row[0] for row in rows]
    if min(idxs) != 0 or max(idxs) != n_vectors - 1 or len(set(idxs)) != n_vectors:
        raise RuntimeError(
            "Non-contiguous embedding_idx values detected. Run embed.py --rebuild to fix."
        )


def main():
    parser = argparse.ArgumentParser(description="Generate paper embeddings")
    parser.add_argument("--date", help="Only embed papers from this date (YYYY-MM-DD)")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size for embedding")
    parser.add_argument("--limit", type=int, help="Limit number of papers to embed")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild entire index from scratch")
    parser.add_argument("--umap", action="store_true", help="Run UMAP projection after embedding")
    parser.add_argument("--umap-neighbors", type=int, default=15, help="UMAP n_neighbors (default: 15)")
    parser.add_argument("--umap-min-dist", type=float, default=0.1, help="UMAP min_dist (default: 0.1)")
    args = parser.parse_args()
    
    console.print("[bold green]Paper Embedding with SPECTER[/bold green]")
    
    conn = get_db_connection()
    
    # Handle rebuild
    if args.rebuild:
        console.print("[yellow]Rebuilding index from scratch[/yellow]")
        # Clear all embedding_idx and UMAP coordinates
        conn.execute("UPDATE papers SET embedding_idx = NULL, umap_x = NULL, umap_y = NULL")
        conn.commit()
        # Delete existing index
        index_path = INDEX_DIR / "faiss.index"
        if index_path.exists():
            index_path.unlink()
    
    # Validate date if provided
    date_filter = validate_date(args.date) if args.date else None
    
    # Load or create index
    index = load_or_create_index()
    
    # Guardrail: refuse to append if DB/index are out of sync (unless rebuild)
    if not args.rebuild:
        validate_index_alignment(conn, index)
    
    # Starting position for new embeddings
    start_idx = index.ntotal
    
    # Get papers to embed
    papers = get_papers_to_embed(conn, date_filter, args.limit)
    
    if not papers:
        console.print("[yellow]No papers need embedding.[/yellow]")
        
        # Still run UMAP if requested (useful for recomputing projections)
        if args.umap:
            console.print("\n[bold green]Running UMAP Projection[/bold green]")
            embeddings, paper_ids = load_embeddings_and_ids(conn)
            
            if len(embeddings) > 0:
                coords = run_umap(
                    embeddings,
                    n_neighbors=args.umap_neighbors,
                    min_dist=args.umap_min_dist
                )
                update_coordinates(conn, paper_ids, coords)
                console.print(f"[bold green]✓ Projected {len(paper_ids)} papers to 2D[/bold green]")
            else:
                console.print("[yellow]No embeddings to project.[/yellow]")
        
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
    
    # Save index
    save_index(index)
    
    console.print(f"\n[bold green]✓ Embedded {len(papers)} papers[/bold green]")
    console.print(f"[bold green]✓ Index now contains {index.ntotal} vectors[/bold green]")
    console.print(f"[bold green]✓ Saved to {INDEX_DIR}[/bold green]")
    
    # Close the original connection before UMAP (clean handoff)
    conn.close()
    
    # Run UMAP projection if requested
    if args.umap:
        console.print("\n[bold green]Running UMAP Projection[/bold green]")
        
        # Reconnect for UMAP
        conn = get_db_connection()
        
        # Load all embeddings (including newly added ones)
        embeddings, paper_ids = load_embeddings_and_ids(conn)
        
        if len(embeddings) > 0:
            # Run UMAP
            coords = run_umap(
                embeddings,
                n_neighbors=args.umap_neighbors,
                min_dist=args.umap_min_dist
            )
            
            # Update database
            update_coordinates(conn, paper_ids, coords)
            
            console.print(f"[bold green]✓ Projected {len(paper_ids)} papers to 2D[/bold green]")
            
            # Print coordinate ranges
            x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
            y_min, y_max = coords[:, 1].min(), coords[:, 1].max()
            console.print(f"  X range: [{x_min:.2f}, {x_max:.2f}]")
            console.print(f"  Y range: [{y_min:.2f}, {y_max:.2f}]")
        else:
            console.print("[yellow]No embeddings to project.[/yellow]")
        
        conn.close()


if __name__ == "__main__":
    main()
