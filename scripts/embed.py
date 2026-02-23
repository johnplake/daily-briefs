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
import json
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
MARKER_PATH = INDEX_DIR / "embed_in_progress.json"

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

    # Guardrail: if a previous embed crashed, force rebuild
    if MARKER_PATH.exists() and not args.rebuild:
        raise RuntimeError(
            f"Found stale marker {MARKER_PATH}. Previous embed may have crashed. "
            "Run embed.py --rebuild to recover."
        )
    if MARKER_PATH.exists() and args.rebuild:
        MARKER_PATH.unlink(missing_ok=True)

    conn = get_db_connection()
    try:
        # Handle rebuild
        if args.rebuild:
            console.print("[yellow]Rebuilding index from scratch[/yellow]")
            conn.execute("UPDATE papers SET embedding_idx = NULL, umap_x = NULL, umap_y = NULL")
            conn.commit()
            index_path = INDEX_DIR / "faiss.index"
            if index_path.exists():
                index_path.unlink()

        date_filter = validate_date(args.date) if args.date else None

        index = load_or_create_index()
        if not args.rebuild:
            validate_index_alignment(conn, index)

        start_idx = index.ntotal
        papers = get_papers_to_embed(conn, date_filter, args.limit)

        if not papers:
            console.print("[yellow]No papers need embedding.[/yellow]")
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
            return

        console.print(f"Papers to embed: {len(papers)}")
        console.print(f"Starting index position: {start_idx}")

        console.print(f"[cyan]Loading model: {MODEL_NAME}[/cyan]")
        model = SentenceTransformer(MODEL_NAME)

        paper_data = [dict(row) for row in papers]
        texts = [get_paper_text(p) for p in paper_data]

        console.print(f"[cyan]Generating embeddings (batch size: {args.batch_size})...[/cyan]")

        # Pre-allocate array to avoid holding all batches in memory twice
        new_embeddings = np.zeros((len(texts), EMBEDDING_DIM), dtype='float32')
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Embedding...", total=len(texts))
            for i in range(0, len(texts), args.batch_size):
                batch = texts[i:i + args.batch_size]
                batch_embeddings = model.encode(batch, show_progress_bar=False)
                new_embeddings[i:i + len(batch)] = batch_embeddings
                progress.update(task, advance=len(batch),
                               description=f"Embedding {min(i + args.batch_size, len(texts))}/{len(texts)}...")

        faiss.normalize_L2(new_embeddings)

        expected_total = index.ntotal + len(new_embeddings)
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARKER_PATH.write_text(json.dumps({"expected_total": expected_total}))

        try:
            index.add(new_embeddings)

            console.print("[cyan]Updating database...[/cyan]")
            for i, paper in enumerate(paper_data):
                embedding_idx = start_idx + i
                conn.execute(
                    "UPDATE papers SET embedding_idx = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (embedding_idx, paper["id"])
                )

            conn.commit()
            save_index(index)

            # Only clear marker after BOTH DB commit + index save succeed
            if MARKER_PATH.exists():
                MARKER_PATH.unlink(missing_ok=True)
        except Exception:
            # Leave marker to force --rebuild on next run
            raise

        console.print(f"\n[bold green]✓ Embedded {len(papers)} papers[/bold green]")
        console.print(f"[bold green]✓ Index now contains {index.ntotal} vectors[/bold green]")
        console.print(f"[bold green]✓ Saved to {INDEX_DIR}[/bold green]")

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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
