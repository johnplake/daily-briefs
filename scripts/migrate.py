#!/usr/bin/env python3
"""
Migration script: Convert from old JSON-based structure to SQLite-centric architecture.

Migrates:
1. metadata.json files → papers table
2. paper.txt files → new location (data/arxiv/{date}/{id}/paper.txt)
3. paper_ids.json → embedding_idx column
4. citations.json → citations_s2, citations_oa columns

Usage:
    python migrate.py                    # Dry run
    python migrate.py --execute          # Actually migrate
    python migrate.py --execute --cleanup  # Migrate and delete old files
"""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
OLD_RAW_DIR = PROJECT_ROOT / "data" / "arxiv" / "raw"
OLD_DB_PATH = PROJECT_ROOT / "data" / "index" / "papers.db"
OLD_EMBEDDINGS_DIR = PROJECT_ROOT / "data" / "embeddings"

NEW_DB_PATH = PROJECT_ROOT / "data" / "papers.db"
NEW_DATA_DIR = PROJECT_ROOT / "data" / "arxiv"


def get_db_connection() -> sqlite3.Connection:
    """Get database connection."""
    if not NEW_DB_PATH.exists():
        raise RuntimeError(f"New database not found at {NEW_DB_PATH}. Run init_db.py first.")
    conn = sqlite3.connect(NEW_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_old_papers() -> list:
    """Find all papers in the old structure."""
    papers = []
    
    if not OLD_RAW_DIR.exists():
        return papers
    
    for date_dir in OLD_RAW_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        
        date = date_dir.name  # e.g., "2026-02-21"
        
        for paper_dir in date_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            
            metadata_path = paper_dir / "metadata.json"
            if metadata_path.exists():
                papers.append({
                    "date": date,
                    "paper_dir": paper_dir,
                    "metadata_path": metadata_path,
                    "text_path": paper_dir / "paper.txt",
                    "citations_path": paper_dir / "citations.json",
                })
    
    return papers


def load_old_embedding_map() -> dict:
    """Load paper_ids.json to get embedding indices."""
    ids_path = OLD_EMBEDDINGS_DIR / "paper_ids.json"
    
    if ids_path.exists():
        with open(ids_path) as f:
            paper_ids = json.load(f)
        # Create reverse mapping: arxiv_id -> index
        return {pid: idx for idx, pid in enumerate(paper_ids)}
    
    return {}


def migrate_paper(conn: sqlite3.Connection, paper_info: dict, embedding_map: dict,
                  execute: bool = False) -> dict:
    """Migrate a single paper."""
    result = {
        "paper_id": None,
        "status": "skipped",
        "text_moved": False,
    }
    
    # Load metadata
    with open(paper_info["metadata_path"]) as f:
        meta = json.load(f)
    
    arxiv_id = meta.get("arxiv_id", "")
    result["paper_id"] = arxiv_id
    
    if not arxiv_id:
        result["status"] = "error: no arxiv_id"
        return result
    
    # Load citations if available
    citations_s2 = None
    citations_oa = None
    if paper_info["citations_path"].exists():
        with open(paper_info["citations_path"]) as f:
            cites = json.load(f)
        citations_s2 = cites.get("s2_citation_count")
        citations_oa = cites.get("openalex_cited_by_count")
    
    # Get embedding index
    embedding_idx = embedding_map.get(arxiv_id)
    
    # Check if text exists
    text_extracted = paper_info["text_path"].exists()
    
    # Prepare data
    data = {
        "paper_source": "arxiv",
        "paper_id": arxiv_id,
        "announced_date": paper_info["date"],
        "title": meta.get("title", ""),
        "abstract": meta.get("abstract", ""),
        "authors": json.dumps(meta.get("authors", [])),
        "primary_category": meta.get("primary_category", ""),
        "categories": " ".join(meta.get("categories", [])),
        "version": 1,
        "submitted_date": None,
        "updated_date": None,
        "arxiv_url": meta.get("abs_url", f"https://arxiv.org/abs/{arxiv_id}"),
        "pdf_url": meta.get("pdf_url", f"https://arxiv.org/pdf/{arxiv_id}.pdf"),
        "doi": meta.get("doi", ""),
        "citations_s2": citations_s2,
        "citations_oa": citations_oa,
        "embedding_idx": embedding_idx,
        "text_extracted": 1 if text_extracted else 0,
    }
    
    if execute:
        # Insert into database
        try:
            conn.execute("""
                INSERT OR REPLACE INTO papers (
                    paper_source, paper_id, announced_date,
                    title, abstract, authors,
                    primary_category, categories,
                    version, submitted_date, updated_date,
                    arxiv_url, pdf_url, doi,
                    citations_s2, citations_oa,
                    embedding_idx, text_extracted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data["paper_source"], data["paper_id"], data["announced_date"],
                data["title"], data["abstract"], data["authors"],
                data["primary_category"], data["categories"],
                data["version"], data["submitted_date"], data["updated_date"],
                data["arxiv_url"], data["pdf_url"], data["doi"],
                data["citations_s2"], data["citations_oa"],
                data["embedding_idx"], data["text_extracted"],
            ))
            result["status"] = "migrated"
        except Exception as e:
            result["status"] = f"error: {e}"
            return result
        
        # Move text file if it exists
        if text_extracted:
            new_text_dir = NEW_DATA_DIR / data["announced_date"] / data["paper_id"]
            new_text_path = new_text_dir / "paper.txt"
            
            if not new_text_path.exists():
                new_text_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paper_info["text_path"], new_text_path)
                result["text_moved"] = True
    else:
        result["status"] = "would_migrate"
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Migrate to SQLite-centric architecture")
    parser.add_argument("--execute", action="store_true", help="Actually perform migration")
    parser.add_argument("--cleanup", action="store_true", help="Delete old files after migration")
    args = parser.parse_args()
    
    console.print("[bold green]Migration: JSON → SQLite[/bold green]")
    
    if not args.execute:
        console.print("[yellow]DRY RUN - use --execute to actually migrate[/yellow]")
    
    # Find old papers
    papers = find_old_papers()
    console.print(f"Found {len(papers)} papers in old structure")
    
    if not papers:
        console.print("[yellow]No papers to migrate.[/yellow]")
        return
    
    # Load embedding map
    embedding_map = load_old_embedding_map()
    console.print(f"Loaded {len(embedding_map)} embedding mappings")
    
    # Get database connection
    conn = get_db_connection()
    
    # Migrate papers
    stats = {"migrated": 0, "text_moved": 0, "errors": 0}
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Migrating...", total=len(papers))
        
        for paper_info in papers:
            progress.update(task, description=f"Migrating {paper_info['paper_dir'].name}")
            
            result = migrate_paper(conn, paper_info, embedding_map, execute=args.execute)
            
            if "error" in result["status"]:
                stats["errors"] += 1
                console.print(f"[red]Error: {result['paper_id']}: {result['status']}[/red]")
            elif result["status"] == "migrated":
                stats["migrated"] += 1
                if result["text_moved"]:
                    stats["text_moved"] += 1
            
            progress.advance(task)
    
    if args.execute:
        conn.commit()
    conn.close()
    
    # Summary
    console.print("\n[bold]Migration Summary:[/bold]")
    console.print(f"  Papers migrated: {stats['migrated']}")
    console.print(f"  Text files moved: {stats['text_moved']}")
    console.print(f"  Errors: {stats['errors']}")
    
    if args.execute and args.cleanup and stats["errors"] == 0:
        console.print("\n[yellow]Cleaning up old files...[/yellow]")
        
        # Delete old raw directory
        if OLD_RAW_DIR.exists():
            shutil.rmtree(OLD_RAW_DIR)
            console.print(f"  Deleted: {OLD_RAW_DIR}")
        
        # Delete old paper_ids.json
        old_ids_path = OLD_EMBEDDINGS_DIR / "paper_ids.json"
        if old_ids_path.exists():
            old_ids_path.unlink()
            console.print(f"  Deleted: {old_ids_path}")
        
        # Delete old index database
        if OLD_DB_PATH.exists():
            OLD_DB_PATH.unlink()
            console.print(f"  Deleted: {OLD_DB_PATH}")
        
        console.print("[green]Cleanup complete.[/green]")
    elif args.execute and args.cleanup and stats["errors"] > 0:
        console.print("[red]Skipping cleanup due to errors.[/red]")
    
    if not args.execute:
        console.print("\n[yellow]Run with --execute to perform migration.[/yellow]")


if __name__ == "__main__":
    main()
