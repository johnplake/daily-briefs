#!/usr/bin/env python3
"""
Paper filtering and ranking script.

Filters papers into three streams:
1. Popular - High citation velocity, trending
2. Interest - Matches user profile keywords/embeddings
3. Serendipity - Near-misses and random samples

Also selects feedback candidates:
- Near-misses (barely didn't make the cut)
- Random negatives (for calibration)
"""

import argparse
import json
import random
import re
import sqlite3
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

console = Console()


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_papers_for_date(conn: sqlite3.Connection, date: str) -> list:
    """Get all papers ingested on a specific date."""
    cursor = conn.execute("""
        SELECT arxiv_id, title, abstract, primary_category, categories,
               s2_citation_count, s2_influential_citations, openalex_cited_by_count,
               local_path, authors
        FROM papers
        WHERE ingest_date = ?
    """, (date,))
    
    columns = [desc[0] for desc in cursor.description]
    papers = []
    for row in cursor.fetchall():
        paper = dict(zip(columns, row))
        # Parse JSON fields
        paper["categories"] = json.loads(paper["categories"]) if paper["categories"] else []
        paper["authors"] = json.loads(paper["authors"]) if paper["authors"] else []
        papers.append(paper)
    
    return papers


def get_tier(category: str, config: dict) -> int:
    """Determine which tier a category belongs to (1, 2, 3, or 0 if not found)."""
    categories = config.get("categories", {})
    
    if category in categories.get("tier1", []):
        return 1
    elif category in categories.get("tier2", []):
        return 2
    elif category in categories.get("tier3", []):
        return 3
    return 0


def compute_keyword_score(paper: dict, keywords: list) -> float:
    """
    Compute keyword match score for a paper.
    
    Simple TF-based scoring with title weighted higher than abstract.
    """
    title = paper["title"].lower()
    abstract = paper["abstract"].lower()
    
    title_matches = 0
    abstract_matches = 0
    
    for keyword in keywords:
        kw_lower = keyword.lower()
        # Count occurrences
        title_matches += len(re.findall(r'\b' + re.escape(kw_lower) + r'\b', title))
        abstract_matches += len(re.findall(r'\b' + re.escape(kw_lower) + r'\b', abstract))
    
    # Title matches weighted 3x
    score = (title_matches * 3 + abstract_matches) / max(len(keywords), 1)
    
    return min(score, 1.0)  # Cap at 1.0


def compute_popularity_score(paper: dict) -> float:
    """
    Compute popularity score based on citation metrics.
    
    For new papers, this will often be 0. We use influential citations
    as a stronger signal when available.
    """
    citations = paper.get("s2_citation_count") or 0
    influential = paper.get("s2_influential_citations") or 0
    openalex_citations = paper.get("openalex_cited_by_count") or 0
    
    # Normalize (these are low for new papers)
    # Use log scale to prevent outliers from dominating
    import math
    
    citation_score = math.log1p(citations) / 10  # log(1 + x), normalized
    influential_score = math.log1p(influential) / 5  # Weighted higher
    openalex_score = math.log1p(openalex_citations) / 10
    
    # Combined score (influential weighted heavily)
    score = 0.3 * citation_score + 0.5 * influential_score + 0.2 * openalex_score
    
    return min(score, 1.0)


def compute_combined_score(paper: dict, config: dict) -> dict:
    """
    Compute all scores for a paper.
    
    Returns dict with individual scores and combined score.
    """
    interests = config.get("interests", {})
    keywords = interests.get("keywords", [])
    project_contexts = interests.get("project_contexts", [])
    
    # All keywords and project terms
    all_terms = keywords + project_contexts
    
    keyword_score = compute_keyword_score(paper, all_terms)
    popularity_score = compute_popularity_score(paper)
    
    # Tier affects threshold, not score directly
    tier = get_tier(paper["primary_category"], config)
    
    # Combined score for ranking
    combined = 0.6 * keyword_score + 0.4 * popularity_score
    
    return {
        "keyword_score": keyword_score,
        "popularity_score": popularity_score,
        "combined_score": combined,
        "tier": tier,
    }


def filter_papers(papers: list, config: dict) -> dict:
    """
    Filter papers into streams.
    
    Returns dict with:
    - popular: High popularity, worth knowing about
    - interest: Matches keywords/profile
    - serendipity: Random samples from lower-scored papers
    - near_misses: Barely didn't make it (for feedback)
    - random_negatives: Random low-scorers (for calibration)
    - all_scored: All papers with scores
    """
    filtering_config = config.get("filtering", {})
    tier1_threshold = filtering_config.get("tier1_min_score", 0.1)
    tier2_threshold = filtering_config.get("tier2_min_score", 0.5)
    tier3_threshold = filtering_config.get("tier3_min_score", 0.8)
    serendipity_count = filtering_config.get("serendipity_count", 5)
    near_miss_count = filtering_config.get("near_miss_count", 3)
    random_negative_count = filtering_config.get("random_negative_count", 2)
    
    # Score all papers
    scored_papers = []
    for paper in papers:
        scores = compute_combined_score(paper, config)
        paper.update(scores)
        scored_papers.append(paper)
    
    # Determine threshold per paper based on tier
    def get_threshold(paper):
        tier = paper["tier"]
        if tier == 1:
            return tier1_threshold
        elif tier == 2:
            return tier2_threshold
        elif tier == 3:
            return tier3_threshold
        return 0.9  # Unknown category = very high bar
    
    # Separate papers
    popular = []
    interest = []
    rejected = []
    near_misses = []
    
    for paper in scored_papers:
        threshold = get_threshold(paper)
        
        # Check if passes threshold
        if paper["combined_score"] >= threshold:
            # Classify into popular vs interest
            if paper["popularity_score"] > paper["keyword_score"]:
                popular.append(paper)
            else:
                interest.append(paper)
        else:
            # Check if near miss (within 20% of threshold)
            if paper["combined_score"] >= threshold * 0.8:
                near_misses.append(paper)
            else:
                rejected.append(paper)
    
    # Sort by score
    popular.sort(key=lambda x: x["combined_score"], reverse=True)
    interest.sort(key=lambda x: x["combined_score"], reverse=True)
    near_misses.sort(key=lambda x: x["combined_score"], reverse=True)
    
    # Select serendipity (random from rejected, but not lowest)
    mid_rejected = [p for p in rejected if p["combined_score"] > 0.05]
    serendipity = random.sample(mid_rejected, min(serendipity_count, len(mid_rejected)))
    
    # Select random negatives (truly low scorers)
    low_rejected = [p for p in rejected if p["combined_score"] <= 0.05]
    random_negatives = random.sample(low_rejected, min(random_negative_count, len(low_rejected)))
    
    # Trim near misses
    near_misses = near_misses[:near_miss_count]
    
    return {
        "popular": popular,
        "interest": interest,
        "serendipity": serendipity,
        "near_misses": near_misses,
        "random_negatives": random_negatives,
        "all_scored": scored_papers,
        "total_passed": len(popular) + len(interest),
        "total_rejected": len(rejected) + len(near_misses),
    }


def save_filtered_results(results: dict, output_path: Path):
    """Save filtered results to JSON."""
    # Convert to serializable format
    output = {
        "popular": results["popular"],
        "interest": results["interest"],
        "serendipity": results["serendipity"],
        "near_misses": results["near_misses"],
        "random_negatives": results["random_negatives"],
        "stats": {
            "total_passed": results["total_passed"],
            "total_rejected": results["total_rejected"],
            "popular_count": len(results["popular"]),
            "interest_count": len(results["interest"]),
            "serendipity_count": len(results["serendipity"]),
        }
    }
    
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)


def print_summary(results: dict):
    """Print filtering summary."""
    table = Table(title="Filtering Results")
    table.add_column("Stream", style="cyan")
    table.add_column("Count", style="green")
    
    table.add_row("Popular", str(len(results["popular"])))
    table.add_row("Interest", str(len(results["interest"])))
    table.add_row("Serendipity", str(len(results["serendipity"])))
    table.add_row("Near Misses", str(len(results["near_misses"])))
    table.add_row("Random Negatives", str(len(results["random_negatives"])))
    table.add_row("─" * 15, "─" * 5)
    table.add_row("Total Passed", str(results["total_passed"]))
    table.add_row("Total Rejected", str(results["total_rejected"]))
    
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Filter papers into streams")
    parser.add_argument("--date", required=True, help="Date to filter (YYYY-MM-DD)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--output", help="Output JSON path (default: data/filtered/YYYY-MM-DD.json)")
    args = parser.parse_args()
    
    console.print(f"[bold green]Filtering papers for {args.date}[/bold green]")
    
    # Load config
    config = load_config(args.config)
    
    # Setup paths
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    
    if not db_path.exists():
        console.print("[red]Database not found. Run ingest.py first.[/red]")
        return
    
    conn = sqlite3.connect(db_path)
    
    # Get papers
    papers = get_papers_for_date(conn, args.date)
    console.print(f"Papers to filter: {len(papers)}")
    
    if not papers:
        console.print("[yellow]No papers found for this date.[/yellow]")
        return
    
    # Filter
    results = filter_papers(papers, config)
    
    # Print summary
    print_summary(results)
    
    # Save results
    output_dir = project_root / "data" / "filtered"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else output_dir / f"{args.date}.json"
    
    save_filtered_results(results, output_path)
    console.print(f"\n[bold green]✓ Saved filtered results to {output_path}[/bold green]")
    
    conn.close()


if __name__ == "__main__":
    main()
