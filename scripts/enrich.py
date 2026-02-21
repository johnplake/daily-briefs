#!/usr/bin/env python3
"""
Citation metadata enrichment script.

Fetches citation counts from Semantic Scholar and OpenAlex.
Can be re-run to update citation counts over time.

Simplified version - embeddings handled separately by embed.py.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

import requests
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# API endpoints
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
OPENALEX_API = "https://api.openalex.org/works"

# Rate limits (requests per second)
S2_DELAY = 0.15  # ~6-7 requests/second
OPENALEX_DELAY = 0.1  # 10 requests/second


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_s2_citations(arxiv_id: str) -> dict | None:
    """Fetch citation data from Semantic Scholar."""
    url = f"{S2_API}/arXiv:{arxiv_id}"
    params = {"fields": "citationCount,influentialCitationCount,referenceCount"}
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            console.print(f"[yellow]S2 rate limited, waiting...[/yellow]")
            time.sleep(5)
            return None
        response.raise_for_status()
        data = response.json()
        return {
            "s2_citation_count": data.get("citationCount", 0),
            "s2_influential_citations": data.get("influentialCitationCount", 0),
            "s2_reference_count": data.get("referenceCount", 0),
        }
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]S2 error for {arxiv_id}: {e}[/yellow]")
        return None


def fetch_openalex_citations(arxiv_id: str) -> dict | None:
    """Fetch citation data from OpenAlex."""
    url = f"{OPENALEX_API}/https://arxiv.org/abs/{arxiv_id}"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return {
            "openalex_cited_by_count": data.get("cited_by_count", 0),
        }
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]OpenAlex error for {arxiv_id}: {e}[/yellow]")
        return None


def get_papers_to_enrich(conn: sqlite3.Connection, date_filter: str = None, 
                          force_update: bool = False) -> list:
    """Get papers that need citation enrichment."""
    if force_update:
        query = "SELECT arxiv_id, local_path FROM papers"
    else:
        query = "SELECT arxiv_id, local_path FROM papers WHERE enriched_at IS NULL"
    
    if date_filter:
        if "WHERE" in query:
            query += f" AND ingest_date = '{date_filter}'"
        else:
            query += f" WHERE ingest_date = '{date_filter}'"
    
    cursor = conn.execute(query)
    return cursor.fetchall()


def save_citations(paper_path: Path, citations: dict):
    """Save citation data to paper directory."""
    citations_path = paper_path / "citations.json"
    
    # Load existing or create new
    if citations_path.exists():
        with open(citations_path) as f:
            existing = json.load(f)
    else:
        existing = {}
    
    # Update with timestamp
    existing.update(citations)
    existing["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    with open(citations_path, "w") as f:
        json.dump(existing, f, indent=2)


def ensure_columns(conn: sqlite3.Connection):
    """Ensure citation columns exist in database."""
    columns = [
        ("s2_citation_count", "INTEGER"),
        ("s2_influential_citations", "INTEGER"),
        ("s2_reference_count", "INTEGER"),
        ("openalex_cited_by_count", "INTEGER"),
        ("enriched_at", "TEXT"),
    ]
    
    for col_name, col_type in columns:
        try:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    conn.commit()


def update_database(conn: sqlite3.Connection, arxiv_id: str, citations: dict):
    """Update database with citation data."""
    updates = ["enriched_at = ?"]
    values = [time.strftime("%Y-%m-%d %H:%M:%S")]
    
    for key, value in citations.items():
        if value is not None:
            updates.append(f"{key} = ?")
            values.append(value)
    
    values.append(arxiv_id)
    
    conn.execute(
        f"UPDATE papers SET {', '.join(updates)} WHERE arxiv_id = ?",
        values
    )


def main():
    parser = argparse.ArgumentParser(description="Enrich papers with citation data")
    parser.add_argument("--date", help="Only enrich papers from this date (YYYY-MM-DD)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--limit", type=int, help="Limit number of papers to process")
    parser.add_argument("--update", action="store_true", help="Update all papers (even if already enriched)")
    parser.add_argument("--no-s2", action="store_true", help="Skip Semantic Scholar")
    parser.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex")
    args = parser.parse_args()
    
    console.print("[bold green]Citation Enrichment[/bold green]")
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    
    if not db_path.exists():
        console.print("[red]Database not found. Run ingest.py first.[/red]")
        return
    
    conn = sqlite3.connect(db_path)
    ensure_columns(conn)
    
    # Get papers
    papers = get_papers_to_enrich(conn, args.date, args.update)
    
    if args.limit:
        papers = papers[:args.limit]
    
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
        
        for i, (arxiv_id, local_path) in enumerate(papers):
            progress.update(task, description=f"Enriching {arxiv_id} ({i+1}/{len(papers)})")
            
            citations = {}
            
            if use_s2:
                s2_data = fetch_s2_citations(arxiv_id)
                if s2_data:
                    citations.update(s2_data)
                time.sleep(S2_DELAY)
            
            if use_openalex:
                oa_data = fetch_openalex_citations(arxiv_id)
                if oa_data:
                    citations.update(oa_data)
                time.sleep(OPENALEX_DELAY)
            
            if citations:
                paper_path = Path(local_path)
                save_citations(paper_path, citations)
                update_database(conn, arxiv_id, citations)
                enriched_count += 1
            
            progress.advance(task)
    
    conn.commit()
    conn.close()
    
    console.print(f"\n[bold green]✓ Enriched {enriched_count}/{len(papers)} papers with citation data[/bold green]")


if __name__ == "__main__":
    main()
