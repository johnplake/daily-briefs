#!/usr/bin/env python3
"""
arXiv paper ingestion script.

Downloads papers from arXiv API for specified categories and date range.
Handles pagination, deduplication, and downloads PDF + source archives.
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# arXiv API base
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/{}.pdf"
ARXIV_SOURCE = "https://arxiv.org/e-print/{}"

# Rate limiting: arXiv asks for 3 second delay between requests
RATE_LIMIT_SECONDS = 3


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_all_categories(config: dict) -> list:
    """Get all categories from all tiers."""
    cats = []
    for tier in ["tier1", "tier2", "tier3"]:
        cats.extend(config["categories"].get(tier, []))
    return cats


def build_query(categories: list, date_str: str) -> str:
    """
    Build arXiv API query for given categories and date.
    
    Date format: YYYYMMDD
    Query format: (cat:cs.AI OR cat:cs.LG) AND submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359]
    """
    cat_query = " OR ".join(f"cat:{cat}" for cat in categories)
    date_query = f"submittedDate:[{date_str}0000 TO {date_str}2359]"
    return f"({cat_query}) AND {date_query}"


def fetch_arxiv_page(query: str, start: int = 0, max_results: int = 500) -> dict:
    """
    Fetch a single page of results from arXiv API.
    
    Returns parsed feed with entries.
    """
    params = {
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "ascending",
    }
    url = f"{ARXIV_API}?{urlencode(params)}"
    
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    
    return feedparser.parse(response.content)


def parse_entry(entry: dict) -> dict:
    """Parse a single arXiv entry into our metadata format."""
    # Extract arXiv ID from entry.id (format: http://arxiv.org/abs/XXXX.XXXXX)
    arxiv_id = entry.id.split("/abs/")[-1]
    
    # Handle versioned IDs (e.g., 2402.12345v1)
    arxiv_id_base = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
    
    # Get all categories
    categories = [tag.term for tag in entry.get("tags", [])]
    primary_category = entry.get("arxiv_primary_category", {}).get("term", categories[0] if categories else "")
    
    # Get authors
    authors = [author.get("name", "") for author in entry.get("authors", [])]
    
    # Get links
    links = {link.get("type", link.get("title", "unknown")): link.href for link in entry.get("links", [])}
    pdf_url = links.get("application/pdf", f"https://arxiv.org/pdf/{arxiv_id_base}.pdf")
    
    return {
        "arxiv_id": arxiv_id_base,
        "arxiv_id_versioned": arxiv_id,
        "title": entry.get("title", "").replace("\n", " ").strip(),
        "abstract": entry.get("summary", "").replace("\n", " ").strip(),
        "authors": authors,
        "primary_category": primary_category,
        "categories": categories,
        "published": entry.get("published", ""),
        "updated": entry.get("updated", ""),
        "doi": entry.get("arxiv_doi", ""),
        "journal_ref": entry.get("arxiv_journal_ref", ""),
        "comment": entry.get("arxiv_comment", ""),
        "pdf_url": pdf_url,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id_base}",
        "source_url": f"https://arxiv.org/e-print/{arxiv_id_base}",
    }


def fetch_all_papers(categories: list, date_str: str) -> list:
    """
    Fetch all papers for given categories and date with pagination.
    
    Returns deduplicated list of paper metadata.
    """
    query = build_query(categories, date_str)
    console.print(f"[bold]Query:[/bold] {query[:100]}...")
    
    all_papers = {}
    start = 0
    page_size = 500
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching papers...", total=None)
        
        while True:
            progress.update(task, description=f"Fetching papers (offset {start})...")
            
            feed = fetch_arxiv_page(query, start=start, max_results=page_size)
            entries = feed.get("entries", [])
            
            if not entries:
                break
            
            for entry in entries:
                paper = parse_entry(entry)
                # Deduplicate by arxiv_id (base, not versioned)
                all_papers[paper["arxiv_id"]] = paper
            
            console.print(f"  Fetched {len(entries)} papers (total unique: {len(all_papers)})")
            
            if len(entries) < page_size:
                break
            
            start += page_size
            time.sleep(RATE_LIMIT_SECONDS)
    
    return list(all_papers.values())


def download_file(url: str, dest_path: Path, timeout: int = 120) -> bool:
    """Download a file from URL to destination path."""
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        console.print(f"[red]Failed to download {url}: {e}[/red]")
        return False


def save_paper(paper: dict, base_dir: Path, download_pdf: bool = True, download_source: bool = True, max_source_mb: int = 200) -> Path:
    """
    Save paper metadata and optionally download PDF + source.
    
    Returns path to paper directory.
    """
    paper_dir = base_dir / paper["arxiv_id"].replace("/", "_")
    paper_dir.mkdir(parents=True, exist_ok=True)
    
    # Save metadata
    metadata_path = paper_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(paper, f, indent=2)
    
    # Download PDF
    if download_pdf:
        pdf_path = paper_dir / "paper.pdf"
        if not pdf_path.exists():
            download_file(paper["pdf_url"], pdf_path)
            time.sleep(0.5)  # Be nice to arXiv
    
    # Download source (with size check via HEAD request)
    if download_source:
        source_path = paper_dir / "source.tar.gz"
        if not source_path.exists():
            try:
                # Check size first
                head = requests.head(paper["source_url"], timeout=10)
                content_length = int(head.headers.get("content-length", 0))
                if content_length > 0 and content_length > max_source_mb * 1024 * 1024:
                    console.print(f"[yellow]Skipping source for {paper['arxiv_id']} ({content_length / 1024 / 1024:.1f} MB > {max_source_mb} MB)[/yellow]")
                else:
                    download_file(paper["source_url"], source_path)
                    time.sleep(0.5)
            except Exception:
                pass  # Source not always available
    
    return paper_dir


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database for paper index."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            primary_category TEXT,
            categories TEXT,
            published TEXT,
            updated TEXT,
            doi TEXT,
            journal_ref TEXT,
            comment TEXT,
            pdf_url TEXT,
            abs_url TEXT,
            source_url TEXT,
            ingest_date TEXT,
            local_path TEXT
        )
    """)
    conn.commit()
    return conn


def insert_paper(conn: sqlite3.Connection, paper: dict, local_path: str, ingest_date: str):
    """Insert paper into database."""
    conn.execute("""
        INSERT OR REPLACE INTO papers 
        (arxiv_id, title, abstract, authors, primary_category, categories, 
         published, updated, doi, journal_ref, comment, pdf_url, abs_url, 
         source_url, ingest_date, local_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        paper["arxiv_id"],
        paper["title"],
        paper["abstract"],
        json.dumps(paper["authors"]),
        paper["primary_category"],
        json.dumps(paper["categories"]),
        paper["published"],
        paper["updated"],
        paper.get("doi", ""),
        paper.get("journal_ref", ""),
        paper.get("comment", ""),
        paper["pdf_url"],
        paper["abs_url"],
        paper["source_url"],
        ingest_date,
        local_path,
    ))


def main():
    parser = argparse.ArgumentParser(description="Ingest arXiv papers")
    parser.add_argument("--date", help="Date to ingest (YYYY-MM-DD). Default: yesterday")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF download")
    parser.add_argument("--no-source", action="store_true", help="Skip source download")
    parser.add_argument("--overlap", action="store_true", help="Also fetch previous day (overlap for safety)")
    args = parser.parse_args()
    
    # Determine date
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        target_date = datetime.now() - timedelta(days=1)
    
    date_str = target_date.strftime("%Y%m%d")
    date_dir = target_date.strftime("%Y-%m-%d")
    
    console.print(f"[bold green]arXiv Ingestion for {date_dir}[/bold green]")
    
    # Load config
    config = load_config(args.config)
    categories = get_all_categories(config)
    console.print(f"Categories: {len(categories)} total across all tiers")
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    raw_dir = project_root / "data" / "arxiv" / "raw" / date_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = project_root / "data" / "index" / "papers.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Fetch papers
    dates_to_fetch = [date_str]
    if args.overlap:
        prev_date = target_date - timedelta(days=1)
        dates_to_fetch.append(prev_date.strftime("%Y%m%d"))
    
    all_papers = []
    for d in dates_to_fetch:
        papers = fetch_all_papers(categories, d)
        all_papers.extend(papers)
    
    # Deduplicate again (in case of overlap)
    papers_dict = {p["arxiv_id"]: p for p in all_papers}
    papers = list(papers_dict.values())
    
    console.print(f"\n[bold]Total unique papers: {len(papers)}[/bold]")
    
    if not papers:
        console.print("[yellow]No papers found for this date/categories.[/yellow]")
        return
    
    # Initialize database
    conn = init_database(db_path)
    
    # Save papers
    storage_config = config.get("storage", {})
    download_pdf = storage_config.get("download_pdf", True) and not args.no_pdf
    download_source = storage_config.get("download_source", True) and not args.no_source
    max_source_mb = storage_config.get("max_source_size_mb", 200)
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Saving papers...", total=len(papers))
        
        for i, paper in enumerate(papers):
            progress.update(task, description=f"Saving {paper['arxiv_id']} ({i+1}/{len(papers)})")
            
            paper_dir = save_paper(
                paper, 
                raw_dir, 
                download_pdf=download_pdf,
                download_source=download_source,
                max_source_mb=max_source_mb
            )
            
            insert_paper(conn, paper, str(paper_dir), date_dir)
            progress.advance(task)
    
    conn.commit()
    conn.close()
    
    console.print(f"\n[bold green]✓ Ingested {len(papers)} papers to {raw_dir}[/bold green]")
    console.print(f"[bold green]✓ Database updated at {db_path}[/bold green]")


if __name__ == "__main__":
    main()
