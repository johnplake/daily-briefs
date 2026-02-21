#!/usr/bin/env python3
"""
arXiv paper ingestion script.

Downloads papers from arXiv RSS feeds for specified categories.
Handles deduplication and downloads PDF + source archives.

RSS feeds are more reliable than API date queries for daily updates.
"""

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# arXiv endpoints
ARXIV_RSS = "https://export.arxiv.org/rss/{}"
ARXIV_PDF = "https://arxiv.org/pdf/{}.pdf"
ARXIV_SOURCE = "https://arxiv.org/e-print/{}"

# Rate limiting
RATE_LIMIT_SECONDS = 1


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


def fetch_rss_feed(category: str, max_retries: int = 3) -> list:
    """
    Fetch papers from arXiv RSS feed for a category.
    
    Returns list of parsed entries.
    """
    url = ARXIV_RSS.format(category)
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 429:
                wait_time = (2 ** attempt) * 5
                console.print(f"[yellow]Rate limited on {category}. Waiting {wait_time}s...[/yellow]")
                time.sleep(wait_time)
                continue
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            return feed.get("entries", [])
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                console.print(f"[yellow]Error fetching {category}: {e}. Retrying in {wait_time}s...[/yellow]")
                time.sleep(wait_time)
            else:
                console.print(f"[red]Failed to fetch {category} after {max_retries} attempts[/red]")
                return []
    
    return []


def extract_arxiv_id(entry: dict) -> str:
    """Extract arXiv ID from entry link or id."""
    # RSS entries have link like https://arxiv.org/abs/2602.16714
    link = entry.get("link", entry.get("id", ""))
    
    # Extract ID from URL
    if "/abs/" in link:
        arxiv_id = link.split("/abs/")[-1]
    elif "arxiv.org" in link:
        arxiv_id = link.split("/")[-1]
    else:
        arxiv_id = link
    
    # Remove version suffix if present
    if "v" in arxiv_id and arxiv_id[-2] == "v":
        arxiv_id = arxiv_id.rsplit("v", 1)[0]
    
    return arxiv_id


def parse_entry(entry: dict, category: str) -> dict:
    """Parse a single RSS entry into our metadata format."""
    arxiv_id = extract_arxiv_id(entry)
    
    # Get description/abstract
    description = entry.get("description", entry.get("summary", ""))
    
    # Clean up description (remove "arXiv:... Announce Type: ..." prefix)
    if "Abstract:" in description:
        description = description.split("Abstract:", 1)[1].strip()
    elif "Abstract" in description:
        description = description.split("Abstract", 1)[1].strip()
    
    # Parse authors
    authors = []
    if "authors" in entry:
        for author in entry.get("authors", []):
            authors.append(author.get("name", str(author)))
    elif "author" in entry:
        authors = [entry["author"]]
    elif "dc_creator" in entry:
        # Some RSS feeds use dc:creator
        creators = entry.get("dc_creator", "")
        if isinstance(creators, str):
            authors = [a.strip() for a in creators.split(",")]
        else:
            authors = creators
    
    # Get categories from tags
    categories = [category]  # Primary category from the feed we're fetching
    for tag in entry.get("tags", []):
        term = tag.get("term", str(tag))
        if term and term not in categories:
            categories.append(term)
    
    # Publication date
    published = entry.get("published", entry.get("pubDate", ""))
    
    return {
        "arxiv_id": arxiv_id,
        "title": entry.get("title", "").replace("\n", " ").strip(),
        "abstract": description.replace("\n", " ").strip(),
        "authors": authors,
        "primary_category": category,
        "categories": categories,
        "published": published,
        "updated": entry.get("updated", published),
        "doi": entry.get("arxiv_doi", ""),
        "journal_ref": entry.get("arxiv_journal_ref", ""),
        "comment": entry.get("arxiv_comment", ""),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "source_url": f"https://arxiv.org/e-print/{arxiv_id}",
    }


def fetch_all_papers(categories: list) -> list:
    """
    Fetch all papers from RSS feeds for given categories.
    
    Returns deduplicated list of paper metadata.
    """
    all_papers = {}
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching categories...", total=len(categories))
        
        for category in categories:
            progress.update(task, description=f"Fetching {category}...")
            
            entries = fetch_rss_feed(category)
            
            for entry in entries:
                paper = parse_entry(entry, category)
                arxiv_id = paper["arxiv_id"]
                
                if arxiv_id in all_papers:
                    # Merge categories if we've seen this paper before
                    existing = all_papers[arxiv_id]
                    for cat in paper["categories"]:
                        if cat not in existing["categories"]:
                            existing["categories"].append(cat)
                else:
                    all_papers[arxiv_id] = paper
            
            console.print(f"  {category}: {len(entries)} papers (total unique: {len(all_papers)})")
            
            progress.advance(task)
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


def extract_text_from_pdf(pdf_path: Path) -> str | None:
    """Extract text from PDF using PyMuPDF (fitz)."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)
    except Exception as e:
        console.print(f"[yellow]Failed to extract text from {pdf_path}: {e}[/yellow]")
        return None


def save_paper(paper: dict, base_dir: Path, download_pdf: bool = True, download_source: bool = True, extract_text: bool = False, max_source_mb: int = 200) -> Path:
    """
    Save paper metadata and optionally download PDF + source.
    
    If extract_text=True, downloads PDF, extracts text, saves .txt, deletes PDF.
    
    Returns path to paper directory.
    """
    paper_dir = base_dir / paper["arxiv_id"].replace("/", "_")
    paper_dir.mkdir(parents=True, exist_ok=True)
    
    # Save metadata
    metadata_path = paper_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(paper, f, indent=2)
    
    # Handle text extraction mode
    if extract_text:
        text_path = paper_dir / "paper.txt"
        if not text_path.exists():
            # Download PDF to temp location
            pdf_path = paper_dir / "paper.pdf"
            if download_file(paper["pdf_url"], pdf_path):
                # Extract text
                text = extract_text_from_pdf(pdf_path)
                if text:
                    with open(text_path, "w", encoding="utf-8") as f:
                        f.write(text)
                # Delete PDF to save space
                try:
                    pdf_path.unlink()
                except Exception:
                    pass
            time.sleep(0.5)  # Be nice to arXiv
    
    # Download PDF (only if not in extract_text mode)
    elif download_pdf:
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
    parser = argparse.ArgumentParser(description="Ingest arXiv papers from RSS feeds")
    parser.add_argument("--date", help="Date label for storage (YYYY-MM-DD). Default: today")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF download")
    parser.add_argument("--no-source", action="store_true", help="Skip source download")
    parser.add_argument("--extract-text", action="store_true", help="Extract text from PDFs and save as .txt (deletes PDFs to save space)")
    parser.add_argument("--categories", help="Comma-separated list of categories (overrides config)")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Only fetch specific tier")
    args = parser.parse_args()
    
    # Determine date
    if args.date:
        date_dir = args.date
    else:
        date_dir = datetime.now().strftime("%Y-%m-%d")
    
    console.print(f"[bold green]arXiv RSS Ingestion for {date_dir}[/bold green]")
    
    # Load config
    config = load_config(args.config)
    
    # Determine categories
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",")]
    elif args.tier:
        categories = config["categories"].get(f"tier{args.tier}", [])
    else:
        categories = get_all_categories(config)
    
    console.print(f"Categories: {len(categories)} total")
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    raw_dir = project_root / "data" / "arxiv" / "raw" / date_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = project_root / "data" / "index" / "papers.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Fetch papers
    papers = fetch_all_papers(categories)
    
    console.print(f"\n[bold]Total unique papers: {len(papers)}[/bold]")
    
    if not papers:
        console.print("[yellow]No papers found.[/yellow]")
        return
    
    # Initialize database
    conn = init_database(db_path)
    
    # Save papers
    storage_config = config.get("storage", {})
    extract_text = args.extract_text or storage_config.get("extract_text", False)
    download_pdf = storage_config.get("download_pdf", True) and not args.no_pdf and not extract_text
    download_source = storage_config.get("download_source", True) and not args.no_source
    max_source_mb = storage_config.get("max_source_size_mb", 200)
    
    if extract_text:
        console.print("[cyan]Text extraction mode: PDFs will be converted to .txt[/cyan]")
    
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
                extract_text=extract_text,
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
