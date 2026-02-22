#!/usr/bin/env python3
"""
Citation metadata enrichment script.

Fetches citation counts from Semantic Scholar and OpenAlex.
Updates citations_s2 and citations_oa columns in the database.

Can be re-run to update citation counts over time.
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import backoff
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import (
    DB_PATH, APIS,
    HTTP_NOT_FOUND, HTTP_RATE_LIMITED,
    get_db_connection, validate_date
)
from logging_config import setup_logging

console = Console()
logger = setup_logging("enrich")


class PaperNotFoundError(Exception):
    """Raised when a paper is not found (404). Should not retry."""
    pass


def _should_giveup(e: Exception) -> bool:
    """Don't retry on 404 (paper not found)."""
    if isinstance(e, PaperNotFoundError):
        return True
    if hasattr(e, 'response') and getattr(e.response, 'status_code', None) == HTTP_NOT_FOUND:
        return True
    return False


def _on_backoff(details: dict):
    """Log when backing off."""
    wait = details['wait']
    tries = details['tries']
    logger.warning(f"Backing off {wait:.1f}s after {tries} tries")

# API endpoints
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
OPENALEX_API = "https://api.openalex.org/works"

# Rate limits (from config)
S2_DELAY = APIS["s2_delay"]
OPENALEX_DELAY = APIS["oa_delay"]


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=3,
    giveup=_should_giveup,
    on_backoff=_on_backoff,
)
def _fetch_s2_citations_impl(arxiv_id: str) -> int:
    """Fetch citation count from Semantic Scholar (with retry)."""
    url = f"{S2_API}/arXiv:{arxiv_id}"
    params = {"fields": "citationCount"}
    
    response = requests.get(url, params=params, timeout=30)
    if response.status_code == HTTP_NOT_FOUND:
        raise PaperNotFoundError(f"Paper {arxiv_id} not found on S2")
    response.raise_for_status()
    data = response.json()
    return data.get("citationCount", 0)


def fetch_s2_citations(arxiv_id: str) -> int | None:
    """Fetch citation count from Semantic Scholar.
    
    NOTE: Returns None on errors to keep enrichment non-blocking.
    Enrichment is optional, so we don't raise unless it's a bug.
    """
    try:
        return _fetch_s2_citations_impl(arxiv_id)
    except PaperNotFoundError:
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"S2 error for {arxiv_id} after retries: {e}")
        console.print(f"[yellow]S2 error for {arxiv_id}: {e}[/yellow]")
        return None


@backoff.on_exception(
    backoff.expo,
    requests.exceptions.RequestException,
    max_tries=3,
    giveup=_should_giveup,
    on_backoff=_on_backoff,
)
def _fetch_openalex_citations_impl(arxiv_id: str) -> int:
    """Fetch citation count from OpenAlex (with retry)."""
    url = f"{OPENALEX_API}/https://arxiv.org/abs/{arxiv_id}"
    
    response = requests.get(url, timeout=30)
    if response.status_code == HTTP_NOT_FOUND:
        raise PaperNotFoundError(f"Paper {arxiv_id} not found on OpenAlex")
    response.raise_for_status()
    data = response.json()
    return data.get("cited_by_count", 0)


def fetch_openalex_citations(arxiv_id: str) -> int | None:
    """Fetch citation count from OpenAlex.
    
    NOTE: Returns None on errors to keep enrichment non-blocking.
    Enrichment is optional, so we don't raise unless it's a bug.
    """
    try:
        return _fetch_openalex_citations_impl(arxiv_id)
    except PaperNotFoundError:
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"OpenAlex error for {arxiv_id} after retries: {e}")
        console.print(f"[yellow]OpenAlex error for {arxiv_id}: {e}[/yellow]")
        return None


def get_papers_to_enrich(conn: sqlite3.Connection, date_filter: str = None,
                         force_update: bool = False, limit: int = None) -> list:
    """Get papers that need citation enrichment."""
    query = "SELECT id, paper_id FROM papers WHERE paper_source = 'arxiv'"
    
    if not force_update:
        query += " AND (citations_s2 IS NULL OR citations_oa IS NULL)"
    
    if date_filter:
        query += f" AND announced_date = '{date_filter}'"
    
    query += " ORDER BY announced_date DESC"
    
    if limit:
        query += f" LIMIT {limit}"
    
    cursor = conn.execute(query)
    return cursor.fetchall()


def update_citations(conn: sqlite3.Connection, paper_db_id: int, 
                     s2_count: int | None, oa_count: int | None):
    """Update citation counts in database."""
    updates = []
    values = []
    
    if s2_count is not None:
        updates.append("citations_s2 = ?")
        values.append(s2_count)
    
    if oa_count is not None:
        updates.append("citations_oa = ?")
        values.append(oa_count)
    
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(paper_db_id)
        
        conn.execute(
            f"UPDATE papers SET {', '.join(updates)} WHERE id = ?",
            values
        )


def main():
    parser = argparse.ArgumentParser(description="Enrich papers with citation data")
    parser.add_argument("--date", help="Only enrich papers from this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, help="Limit number of papers to process")
    parser.add_argument("--update", action="store_true", help="Update all papers (even if already enriched)")
    parser.add_argument("--no-s2", action="store_true", help="Skip Semantic Scholar")
    parser.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex")
    args = parser.parse_args()
    
    console.print("[bold green]Citation Enrichment[/bold green]")
    
    # Validate date if provided
    date_filter = validate_date(args.date) if args.date else None
    
    conn = get_db_connection()
    
    # Get papers
    papers = get_papers_to_enrich(conn, date_filter, args.update, args.limit)
    
    console.print(f"Papers to enrich: {len(papers)}")
    
    if not papers:
        console.print("[yellow]No papers need enrichment.[/yellow]")
        return
    
    use_s2 = not args.no_s2
    use_openalex = not args.no_openalex
    
    # Process papers
    enriched_count = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching...", total=len(papers))
        
        for i, row in enumerate(papers):
            paper_db_id = row["id"]
            paper_id = row["paper_id"]
            
            progress.update(task, description=f"Enriching {paper_id} ({i+1}/{len(papers)})")
            
            s2_count = None
            oa_count = None
            
            if use_s2:
                s2_count = fetch_s2_citations(paper_id)
                time.sleep(S2_DELAY)
            
            if use_openalex:
                oa_count = fetch_openalex_citations(paper_id)
                time.sleep(OPENALEX_DELAY)
            
            if s2_count is not None or oa_count is not None:
                update_citations(conn, paper_db_id, s2_count, oa_count)
                enriched_count += 1
            
            # Commit periodically
            if (i + 1) % 100 == 0:
                conn.commit()
            
            progress.advance(task)
    
    conn.commit()
    conn.close()
    
    console.print(f"\n[bold green]✓ Enriched {enriched_count}/{len(papers)} papers with citation data[/bold green]")


if __name__ == "__main__":
    main()
