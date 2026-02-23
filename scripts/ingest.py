#!/usr/bin/env python3
"""
arXiv paper ingestion script.

Downloads papers from arXiv RSS feeds for specified categories.
Stores metadata in SQLite, extracted text in files.

RSS feeds are more reliable than API date queries for daily updates.
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from email.utils import parsedate_to_datetime

import feedparser
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import (
    CONFIG, PROJECT_ROOT, DB_PATH, APIS, TEXT_DIR,
    HTTP_RATE_LIMITED,
    get_db_connection, validate_date
)
from logging_config import setup_logging
from utils import safe_json_load

console = Console()
logger = setup_logging("ingest")
# NOTE: We log to file *and* print to console intentionally.
# Console output is for live runs; logs are for audit/debugging later.

def log_warn(msg: str):
    logger.warning(msg)
    console.print(f"[yellow]{msg}[/yellow]")

def log_error(msg: str):
    logger.error(msg)
    console.print(f"[red]{msg}[/red]")

# arXiv endpoints
ARXIV_RSS = "https://export.arxiv.org/rss/{}"

# Rate limiting (from config)
RATE_LIMIT_SECONDS = APIS["arxiv_rate_limit"]


def get_all_categories(config: dict) -> list:
    """Get all categories from all tiers."""
    cats = []
    for tier in ["tier1", "tier2", "tier3"]:
        cats.extend(config["categories"].get(tier, []))
    return cats


def fetch_rss_feed(category: str, max_retries: int = 3) -> list:
    """Fetch papers from arXiv RSS feed for a category."""
    url = ARXIV_RSS.format(category)
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == HTTP_RATE_LIMITED:
                wait_time = (2 ** attempt) * APIS["arxiv_retry_base_seconds"]
                log_warn(f"Rate limited on {category}. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            return feed.get("entries", [])
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                log_warn(f"Error fetching {category}: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                log_error(f"Failed to fetch {category} after {max_retries} attempts")
                return []
    
    return []


def extract_arxiv_id(entry: dict) -> tuple[str, int]:
    """
    Extract arXiv ID and version from entry link or id.
    
    Returns (base_id, version) e.g., ("2502.12345", 2)
    """
    link = entry.get("link", entry.get("id", ""))
    
    # Extract ID from URL
    if "/abs/" in link:
        arxiv_id = link.split("/abs/")[-1]
    elif "arxiv.org" in link:
        arxiv_id = link.split("/")[-1]
    else:
        arxiv_id = link
    
    # Parse version
    version = 1
    match = re.match(r"(.+)v(\d+)$", arxiv_id)
    if match:
        arxiv_id = match.group(1)
        version = int(match.group(2))
    
    return arxiv_id, version


def _parse_rss_date(value: str | None, label: str) -> str | None:
    """Parse RSS date strings into YYYY-MM-DD; return None if malformed."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError) as e:
        logger.warning(f"Failed to parse {label} date '{value}': {e}")
        return None


def parse_entry(entry: dict[str, Any], category: str, announced_date: str) -> dict[str, Any]:
    """Parse a single RSS entry into our metadata format."""
    arxiv_id, version = extract_arxiv_id(entry)
    
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
        creators = entry.get("dc_creator", "")
        if isinstance(creators, str):
            authors = [a.strip() for a in creators.split(",")]
        else:
            authors = creators
    
    # Get categories from tags
    categories = [category]
    for tag in entry.get("tags", []):
        term = tag.get("term", str(tag))
        if term and term not in categories:
            categories.append(term)
    
    # Parse dates (RSS may be RFC 2822)
    published = entry.get("published", entry.get("pubDate", ""))
    updated = entry.get("updated", published)
    
    submitted_date = _parse_rss_date(published, "published")
    updated_date = _parse_rss_date(updated, "updated")
    
    # DOI if present
    doi = entry.get("arxiv_doi", entry.get("doi", ""))
    
    return {
        "paper_source": "arxiv",
        "paper_id": arxiv_id,
        "announced_date": announced_date,
        "title": entry.get("title", "").replace("\n", " ").strip(),
        "abstract": description.replace("\n", " ").strip(),
        "authors": json.dumps(authors),
        "primary_category": category,
        "categories": json.dumps(categories),
        "version": version,
        "submitted_date": submitted_date,
        # Prefer parsed updated date; fallback to announced_date for version updates
        "updated_date": updated_date or (announced_date if version > 1 else None),
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "doi": doi,
    }


def fetch_all_papers(categories: list, announced_date: str) -> list:
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
                paper = parse_entry(entry, category, announced_date)
                paper_id = paper["paper_id"]
                
                if paper_id in all_papers:
                    # Merge categories if we've seen this paper before
                    existing = all_papers[paper_id]
                    existing_cats = set(safe_json_load(existing["categories"], default=[], warn_fn=logger.warning))
                    new_cats = set(safe_json_load(paper["categories"], default=[], warn_fn=logger.warning))
                    existing["categories"] = json.dumps(list(existing_cats | new_cats))
                    # Keep higher version
                    if paper["version"] > existing["version"]:
                        existing["version"] = paper["version"]
                else:
                    all_papers[paper_id] = paper
            
            console.print(f"  {category}: {len(entries)} papers (total unique: {len(all_papers)})")
            
            progress.advance(task)
            time.sleep(RATE_LIMIT_SECONDS)
    
    return list(all_papers.values())


def get_text_path(paper: dict) -> Path:
    """Get the path where paper text should be stored (uses configured TEXT_DIR)."""
    return (
        TEXT_DIR / paper["paper_source"] / 
        paper["announced_date"] / paper["paper_id"] / "paper.txt"
    )


def extract_text_from_pdf(pdf_path: Path) -> str | None:
    """Extract text from PDF using PyMuPDF (fitz)."""
    import fitz
    
    try:
        doc = fitz.open(pdf_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)
    except (fitz.FileDataError, fitz.EmptyFileError, fitz.FileNotFoundError) as e:
        # Expected PDF errors - log and continue
        log_warn(f"Invalid PDF {pdf_path}: {e}")
        return None
    except MemoryError:
        # Large PDF exhausted memory - log and continue (don't crash batch)
        log_error(f"Out of memory extracting {pdf_path}")
        return None
    except Exception as e:
        # Unexpected error - log loudly but continue batch processing
        logger.error(f"Unexpected error extracting {pdf_path}: {type(e).__name__}: {e}", exc_info=True)
        log_error(f"Unexpected error extracting {pdf_path}: {type(e).__name__}: {e}")
        return None


def download_and_extract_text(paper: dict[str, Any]) -> bool:
    """
    Download PDF, extract text, save to file, delete PDF.
    Returns True if text was extracted successfully.
    """
    text_path = get_text_path(paper)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Download PDF to temp location
    pdf_path = text_path.parent / "paper.pdf"
    
    try:
        response = requests.get(
            paper["pdf_url"],
            timeout=CONFIG.get("storage", {}).get("pdf_timeout_seconds", 60),
            stream=True
        )
        response.raise_for_status()
        
        with open(pdf_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Extract text
        text = extract_text_from_pdf(pdf_path)
        if text:
            with open(text_path, "w", encoding="utf-8") as f:
                f.write(text)
            # Delete PDF
            pdf_path.unlink(missing_ok=True)
            return True
        else:
            pdf_path.unlink(missing_ok=True)
            return False
            
    except Exception as e:
        log_warn(f"Failed to download {paper['paper_id']}: {e}")
        pdf_path.unlink(missing_ok=True)
        return False


def upsert_paper(conn: sqlite3.Connection, paper: dict[str, Any], text_extracted: bool) -> tuple[str, bool]:
    """
    Insert or update paper in database.
    
    Returns (action, needs_reextract) where:
      - action is "inserted", "updated", or "unchanged"
      - needs_reextract is True if this is a version update
    """
    cursor = conn.cursor()
    
    # First try an insert (safe under concurrency)
    cursor.execute("""
        INSERT INTO papers (
            paper_source, paper_id, announced_date,
            title, abstract, authors,
            primary_category, categories,
            version, submitted_date, updated_date,
            arxiv_url, pdf_url, doi,
            text_extracted
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_source, paper_id) DO NOTHING
    """, (
        paper["paper_source"],
        paper["paper_id"],
        paper["announced_date"],
        paper["title"],
        paper["abstract"],
        paper["authors"],
        paper["primary_category"],
        paper["categories"],
        paper["version"],
        paper["submitted_date"],
        paper["updated_date"],
        paper["arxiv_url"],
        paper["pdf_url"],
        paper["doi"],
        1 if text_extracted else 0,
    ))
    
    if cursor.rowcount == 1:
        return "inserted", False
    
    # Paper already exists - fetch current version
    cursor.execute(
        "SELECT id, version, text_extracted FROM papers WHERE paper_source = ? AND paper_id = ?",
        (paper["paper_source"], paper["paper_id"])
    )
    existing_id, existing_version, existing_text = cursor.fetchone()
    
    # Check if this is a newer version
    if paper["version"] > existing_version:
        # Version update - update metadata, keep embedding_idx
        # Note: We intentionally do NOT clear embedding_idx here.
        # The paper's semantic fingerprint rarely changes significantly between versions.
        cursor.execute("""
            UPDATE papers SET
                title = ?,
                abstract = ?,
                authors = ?,
                categories = ?,
                version = ?,
                updated_date = ?,
                doi = COALESCE(?, doi),
                text_extracted = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            paper["title"],
            paper["abstract"],
            paper["authors"],
            paper["categories"],
            paper["version"],
            # We intentionally set updated_date to announced_date (when we saw it)
            # rather than trusting upstream RSS updated timestamps.
            # This keeps the "seen" timeline consistent across sources.
            paper["announced_date"],
            paper["doi"],
            1 if text_extracted else existing_text,
            existing_id,
        ))
        return "updated", True  # needs_reextract = True for version updates
    
    # Same or older version - no update needed
    return "unchanged", False


def main():
    parser = argparse.ArgumentParser(description="Ingest arXiv papers from RSS feeds")
    parser.add_argument("--date", help="Date label for storage (YYYY-MM-DD). Default: today")
    parser.add_argument("--extract-text", action="store_true", help="Extract text from PDFs")
    parser.add_argument("--categories", help="Comma-separated list of categories (overrides config)")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Only fetch specific tier")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't save")
    args = parser.parse_args()
    
    # Determine date
    announced_date = validate_date(args.date) if args.date else datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f"Starting ingestion for {announced_date}")
    console.print(f"[bold green]arXiv RSS Ingestion for {announced_date}[/bold green]")
    
    # Use shared config (set DAILY_BRIEFS_CONFIG env var to override)
    config = CONFIG
    
    # Determine categories
    if args.categories:
        categories = [c.strip() for c in args.categories.split(",")]
    elif args.tier:
        categories = config["categories"].get(f"tier{args.tier}", [])
    else:
        categories = get_all_categories(config)
    
    console.print(f"Categories: {len(categories)} total")
    
    # Fetch papers
    papers = fetch_all_papers(categories, announced_date)
    
    console.print(f"\n[bold]Total unique papers: {len(papers)}[/bold]")
    
    if not papers:
        logger.info(f"No papers found for {announced_date}")
        console.print("[yellow]No papers found.[/yellow]")
        return
    
    if args.dry_run:
        console.print("[yellow]Dry run - not saving.[/yellow]")
        return
    
    # Get database connection
    conn = get_db_connection()
    try:
        # Process papers
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "text_extracted": 0}
        
        extract_text = args.extract_text or config.get("storage", {}).get("extract_text", False)
        
        if extract_text:
            console.print("[cyan]Text extraction enabled[/cyan]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Processing papers...", total=len(papers))
            
            for paper in papers:
                progress.update(task, description=f"Processing {paper['paper_id']}")
                
                text_extracted = False
                
                if extract_text:
                    text_path = get_text_path(paper)
                    
                    # Check if we need to extract (new paper or version update)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT version FROM papers WHERE paper_source = ? AND paper_id = ?",
                        (paper["paper_source"], paper["paper_id"])
                    )
                    existing = cursor.fetchone()
                    
                    needs_extract = (
                        existing is None or  # New paper
                        paper["version"] > existing[0] or  # Version update
                        not text_path.exists()  # Text file missing
                    )
                    
                    if needs_extract:
                        if download_and_extract_text(paper):
                            text_extracted = True
                            stats["text_extracted"] += 1
                        time.sleep(config.get("storage", {}).get("text_extract_delay", 0.5))
                    else:
                        text_extracted = text_path.exists()
                
                # Upsert to database
                action, _ = upsert_paper(conn, paper, text_extracted)
                stats[action] += 1
                
                progress.advance(task)
        
        conn.commit()
    finally:
        conn.close()
    
    # Log summary
    summary = f"Ingestion complete: inserted={stats['inserted']}, updated={stats['updated']}, unchanged={stats['unchanged']}"
    if extract_text:
        summary += f", text_extracted={stats['text_extracted']}"
    logger.info(summary)
    
    console.print(f"\n[bold green]✓ Ingestion complete[/bold green]")
    console.print(f"  Inserted: {stats['inserted']}")
    console.print(f"  Updated: {stats['updated']}")
    console.print(f"  Unchanged: {stats['unchanged']}")
    if extract_text:
        console.print(f"  Text extracted: {stats['text_extracted']}")


if __name__ == "__main__":
    main()
