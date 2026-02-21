#!/usr/bin/env python3
"""
Paper embedding script.

Generates SPECTER2 embeddings for papers and builds a FAISS index
for fast similarity search.

Embeddings are computed from title + abstract (or full text if available).
"""

import argparse
import json
import sqlite3
from pathlib import Path

import faiss
import numpy as np
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from sentence_transformers import SentenceTransformer

console = Console()

# SPECTER2 model for scientific papers
MODEL_NAME = "allenai/specter2"
EMBEDDING_DIM = 768


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_papers_to_embed(conn: sqlite3.Connection, existing_ids: set, date_filter: str = None) -> list:
    """Get papers that need embedding."""
    query = "SELECT arxiv_id, title, abstract, local_path FROM papers"
    if date_filter:
        query += f" WHERE ingest_date = '{date_filter}'"
    
    cursor = conn.execute(query)
    papers = []
    for row in cursor.fetchall():
        arxiv_id, title, abstract, local_path = row
        if arxiv_id not in existing_ids:
            papers.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "local_path": local_path,
            })
    return papers


def get_paper_text(paper: dict) -> str:
    """
    Get text to embed for a paper.
    
    Uses title + abstract by default.
    If full text exists, uses title + first 5000 chars of full text.
    """
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    
    # Check for extracted full text
    local_path = paper.get("local_path")
    if local_path:
        text_path = Path(local_path) / "paper.txt"
        if text_path.exists():
            try:
                with open(text_path, "r", encoding="utf-8") as f:
                    full_text = f.read()[:5000]  # First 5000 chars
                return f"{title}\n\n{full_text}"
            except Exception:
                pass
    
    # Fallback to title + abstract
    return f"{title}\n\n{abstract}"


def load_existing_index(index_dir: Path) -> tuple:
    """
    Load existing FAISS index and paper ID mapping.
    
    Returns (index, paper_ids, id_to_idx) or (None, [], {}) if not found.
    """
    index_path = index_dir / "faiss.index"
    ids_path = index_dir / "paper_ids.json"
    
    if index_path.exists() and ids_path.exists():
        try:
            index = faiss.read_index(str(index_path))
            with open(ids_path) as f:
                paper_ids = json.load(f)
            id_to_idx = {pid: idx for idx, pid in enumerate(paper_ids)}
            console.print(f"[cyan]Loaded existing index with {len(paper_ids)} papers[/cyan]")
            return index, paper_ids, id_to_idx
        except Exception as e:
            console.print(f"[yellow]Failed to load existing index: {e}[/yellow]")
    
    return None, [], {}


def save_index(index: faiss.Index, paper_ids: list, index_dir: Path):
    """Save FAISS index and paper ID mapping."""
    index_dir.mkdir(parents=True, exist_ok=True)
    
    index_path = index_dir / "faiss.index"
    ids_path = index_dir / "paper_ids.json"
    
    faiss.write_index(index, str(index_path))
    with open(ids_path, "w") as f:
        json.dump(paper_ids, f)


def main():
    parser = argparse.ArgumentParser(description="Generate paper embeddings")
    parser.add_argument("--date", help="Only embed papers from this date (YYYY-MM-DD)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size for embedding")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild entire index from scratch")
    args = parser.parse_args()
    
    console.print("[bold green]Paper Embedding with SPECTER2[/bold green]")
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    index_dir = project_root / "data" / "embeddings"
    
    if not db_path.exists():
        console.print("[red]Database not found. Run ingest.py first.[/red]")
        return
    
    # Load existing index (unless rebuilding)
    if args.rebuild:
        index, paper_ids, id_to_idx = None, [], {}
        console.print("[yellow]Rebuilding index from scratch[/yellow]")
    else:
        index, paper_ids, id_to_idx = load_existing_index(index_dir)
    
    # Get papers to embed
    conn = sqlite3.connect(db_path)
    papers = get_papers_to_embed(conn, set(id_to_idx.keys()), args.date)
    conn.close()
    
    if not papers:
        console.print("[yellow]No new papers to embed.[/yellow]")
        return
    
    console.print(f"Papers to embed: {len(papers)}")
    
    # Load model
    console.print(f"[cyan]Loading model: {MODEL_NAME}[/cyan]")
    model = SentenceTransformer(MODEL_NAME)
    
    # Prepare texts
    texts = [get_paper_text(p) for p in papers]
    new_ids = [p["arxiv_id"] for p in papers]
    
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
            progress.update(task, advance=len(batch), description=f"Embedding {i + len(batch)}/{len(texts)}...")
    
    # Combine embeddings
    new_embeddings = np.vstack(all_embeddings).astype('float32')
    
    # Normalize for cosine similarity (FAISS IndexFlatIP)
    faiss.normalize_L2(new_embeddings)
    
    # Create or update index
    if index is None:
        # Create new index (Inner Product = cosine similarity on normalized vectors)
        index = faiss.IndexFlatIP(EMBEDDING_DIM)
    
    # Add new embeddings
    index.add(new_embeddings)
    paper_ids.extend(new_ids)
    
    # Save
    save_index(index, paper_ids, index_dir)
    
    console.print(f"\n[bold green]✓ Embedded {len(papers)} papers[/bold green]")
    console.print(f"[bold green]✓ Index now contains {len(paper_ids)} papers[/bold green]")
    console.print(f"[bold green]✓ Saved to {index_dir}[/bold green]")


if __name__ == "__main__":
    main()
