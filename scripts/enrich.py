#!/usr/bin/env python3
"""
Metadata enrichment script.

Enriches paper metadata with:
- Semantic Scholar: citations, references, fields of study, venue
- OpenAlex: additional citation data, author affiliations, concepts
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

# Rate limits
S2_RATE_LIMIT = 0.1  # 10 requests/second
OPENALEX_RATE_LIMIT = 0.1  # 10 requests/second


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def fetch_semantic_scholar(arxiv_id: str) -> dict | None:
    """
    Fetch paper metadata from Semantic Scholar.
    
    Returns enriched metadata or None if not found.
    """
    url = f"{S2_API}/arXiv:{arxiv_id}"
    params = {
        "fields": "paperId,title,abstract,citationCount,referenceCount,influentialCitationCount,fieldsOfStudy,venue,year,authors,citations.paperId,references.paperId"
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]S2 error for {arxiv_id}: {e}[/yellow]")
        return None


def fetch_openalex(arxiv_id: str) -> dict | None:
    """
    Fetch paper metadata from OpenAlex.
    
    Returns enriched metadata or None if not found.
    """
    # OpenAlex uses arXiv ID with prefix
    url = f"{OPENALEX_API}/https://arxiv.org/abs/{arxiv_id}"
    
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]OpenAlex error for {arxiv_id}: {e}[/yellow]")
        return None


def parse_s2_data(data: dict) -> dict:
    """Parse Semantic Scholar response into our format."""
    return {
        "s2_paper_id": data.get("paperId", ""),
        "s2_citation_count": data.get("citationCount", 0),
        "s2_reference_count": data.get("referenceCount", 0),
        "s2_influential_citations": data.get("influentialCitationCount", 0),
        "s2_fields_of_study": data.get("fieldsOfStudy", []),
        "s2_venue": data.get("venue", ""),
        "s2_year": data.get("year"),
        "s2_authors": [
            {"name": a.get("name", ""), "authorId": a.get("authorId", "")}
            for a in data.get("authors", [])
        ],
        "s2_citation_ids": [c.get("paperId", "") for c in data.get("citations", [])],
        "s2_reference_ids": [r.get("paperId", "") for r in data.get("references", [])],
    }


def parse_openalex_data(data: dict) -> dict:
    """Parse OpenAlex response into our format."""
    # Get concepts (topics)
    concepts = [
        {"name": c.get("display_name", ""), "score": c.get("score", 0)}
        for c in data.get("concepts", [])[:10]  # Top 10
    ]
    
    # Get author affiliations
    authorships = []
    for authorship in data.get("authorships", []):
        author = authorship.get("author", {})
        institutions = [
            inst.get("display_name", "")
            for inst in authorship.get("institutions", [])
        ]
        authorships.append({
            "name": author.get("display_name", ""),
            "orcid": author.get("orcid", ""),
            "institutions": institutions,
        })
    
    return {
        "openalex_id": data.get("id", ""),
        "openalex_doi": data.get("doi", ""),
        "openalex_cited_by_count": data.get("cited_by_count", 0),
        "openalex_concepts": concepts,
        "openalex_authorships": authorships,
        "openalex_primary_location": data.get("primary_location", {}).get("source", {}).get("display_name", ""),
        "openalex_type": data.get("type", ""),
        "openalex_open_access": data.get("open_access", {}).get("is_oa", False),
    }


def get_papers_to_enrich(conn: sqlite3.Connection, date_filter: str = None) -> list:
    """Get papers that need enrichment."""
    query = "SELECT arxiv_id, local_path FROM papers"
    if date_filter:
        query += f" WHERE ingest_date = '{date_filter}'"
    
    cursor = conn.execute(query)
    return cursor.fetchall()


def save_enrichment(paper_path: Path, s2_data: dict | None, openalex_data: dict | None):
    """Save enrichment data to paper directory."""
    enriched_path = paper_path / "enriched.json"
    
    # Load existing or create new
    if enriched_path.exists():
        with open(enriched_path) as f:
            existing = json.load(f)
    else:
        existing = {}
    
    # Update with new data
    if s2_data:
        existing["semantic_scholar"] = s2_data
        existing["s2_fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    if openalex_data:
        existing["openalex"] = openalex_data
        existing["openalex_fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    # Save
    with open(enriched_path, "w") as f:
        json.dump(existing, f, indent=2)


def update_database(conn: sqlite3.Connection, arxiv_id: str, s2_data: dict | None, openalex_data: dict | None):
    """Update database with enrichment summary."""
    # Add enrichment columns if they don't exist
    try:
        conn.execute("ALTER TABLE papers ADD COLUMN s2_citation_count INTEGER")
        conn.execute("ALTER TABLE papers ADD COLUMN s2_influential_citations INTEGER")
        conn.execute("ALTER TABLE papers ADD COLUMN openalex_cited_by_count INTEGER")
        conn.execute("ALTER TABLE papers ADD COLUMN enriched_at TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Columns already exist
    
    # Update paper
    updates = ["enriched_at = ?"]
    values = [time.strftime("%Y-%m-%d %H:%M:%S")]
    
    if s2_data:
        updates.append("s2_citation_count = ?")
        values.append(s2_data.get("s2_citation_count", 0))
        updates.append("s2_influential_citations = ?")
        values.append(s2_data.get("s2_influential_citations", 0))
    
    if openalex_data:
        updates.append("openalex_cited_by_count = ?")
        values.append(openalex_data.get("openalex_cited_by_count", 0))
    
    values.append(arxiv_id)
    
    conn.execute(
        f"UPDATE papers SET {', '.join(updates)} WHERE arxiv_id = ?",
        values
    )


def main():
    parser = argparse.ArgumentParser(description="Enrich paper metadata")
    parser.add_argument("--date", help="Only enrich papers from this date (YYYY-MM-DD)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--no-s2", action="store_true", help="Skip Semantic Scholar")
    parser.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex")
    parser.add_argument("--limit", type=int, help="Limit number of papers to process")
    args = parser.parse_args()
    
    console.print("[bold green]Metadata Enrichment[/bold green]")
    
    # Load config
    config = load_config(args.config)
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    
    if not db_path.exists():
        console.print("[red]Database not found. Run ingest.py first.[/red]")
        return
    
    conn = sqlite3.connect(db_path)
    
    # Get papers to enrich
    papers = get_papers_to_enrich(conn, args.date)
    
    if args.limit:
        papers = papers[:args.limit]
    
    console.print(f"Papers to enrich: {len(papers)}")
    
    if not papers:
        console.print("[yellow]No papers to enrich.[/yellow]")
        return
    
    # Check API settings
    api_config = config.get("apis", {})
    use_s2 = api_config.get("semantic_scholar", {}).get("enabled", True) and not args.no_s2
    use_openalex = api_config.get("openalex", {}).get("enabled", True) and not args.no_openalex
    
    if not use_s2 and not use_openalex:
        console.print("[yellow]No APIs enabled. Nothing to do.[/yellow]")
        return
    
    # Process papers
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching...", total=len(papers))
        
        for i, (arxiv_id, local_path) in enumerate(papers):
            progress.update(task, description=f"Enriching {arxiv_id} ({i+1}/{len(papers)})")
            
            paper_path = Path(local_path)
            
            s2_data = None
            openalex_data = None
            
            # Fetch from Semantic Scholar
            if use_s2:
                raw_s2 = fetch_semantic_scholar(arxiv_id)
                if raw_s2:
                    s2_data = parse_s2_data(raw_s2)
                time.sleep(S2_RATE_LIMIT)
            
            # Fetch from OpenAlex
            if use_openalex:
                raw_openalex = fetch_openalex(arxiv_id)
                if raw_openalex:
                    openalex_data = parse_openalex_data(raw_openalex)
                time.sleep(OPENALEX_RATE_LIMIT)
            
            # Save enrichment
            if s2_data or openalex_data:
                save_enrichment(paper_path, s2_data, openalex_data)
                update_database(conn, arxiv_id, s2_data, openalex_data)
            
            progress.advance(task)
    
    conn.commit()
    conn.close()
    
    console.print(f"\n[bold green]✓ Enriched {len(papers)} papers[/bold green]")


if __name__ == "__main__":
    main()
