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
import math
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from config import CONFIG, PROJECT_ROOT, DB_PATH, FILTERED_DIR, FILTERING, get_db_connection, validate_date
from logging_config import setup_logging
from utils import safe_json_load

console = Console()
logger = setup_logging("filter")

UNKNOWN_CATEGORIES: set[str] = set()

# Load filtering weights from config
CITATION_WEIGHT_S2 = FILTERING["citation_weight_s2"]
CITATION_WEIGHT_OA = FILTERING["citation_weight_oa"]
KEYWORD_WEIGHT = FILTERING["keyword_weight"]
POPULARITY_WEIGHT = FILTERING["popularity_weight"]
NEAR_MISS_MULTIPLIER = FILTERING["near_miss_multiplier"]
NEAR_MISS_THRESHOLD = FILTERING["near_miss_threshold"]


def get_papers_for_date(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Get all papers announced on a specific date.
    
    Returns list of paper dicts for iteration/filtering.
    """
    cursor = conn.execute("""
        SELECT paper_id, title, abstract, primary_category, categories,
               citations_s2, citations_oa, authors, arxiv_url, announced_date
        FROM papers
        WHERE announced_date = ?
          AND hidden = 0
    """, (date,))
    
    papers = []
    for row in cursor.fetchall():
        paper = dict(row)
        # Parse JSON fields
        if paper["authors"]:
            paper["authors"] = safe_json_load(
                paper["authors"],
                default=[],
                warn_fn=lambda m: console.print(f"[yellow]{m}[/yellow]")
            )
        else:
            paper["authors"] = []
        
        # Categories is JSON array
        paper["categories_list"] = safe_json_load(
            paper["categories"],
            default=[],
            warn_fn=lambda m: console.print(f"[yellow]{m}[/yellow]")
        )
        
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

    if category and category not in UNKNOWN_CATEGORIES:
        UNKNOWN_CATEGORIES.add(category)
        logger.warning(f"Unknown category not found in config tiers: {category}")
    return 0


def compute_keyword_score(paper: dict[str, Any], keywords: list[str]) -> float:
    """
    Compute keyword match score for a paper.
    Simple TF-based scoring with title weighted higher than abstract.
    """
    title = (paper["title"] or "").lower()
    abstract = (paper["abstract"] or "").lower()
    
    title_matches = 0
    abstract_matches = 0
    
    for keyword in keywords:
        kw_lower = keyword.lower()
        title_matches += len(re.findall(r'\b' + re.escape(kw_lower) + r'\b', title))
        abstract_matches += len(re.findall(r'\b' + re.escape(kw_lower) + r'\b', abstract))
    
    # Title matches weighted 3x
    score = (title_matches * 3 + abstract_matches) / max(len(keywords), 1)
    
    return min(score, 1.0)


def compute_popularity_score(paper: dict[str, Any]) -> float:
    """
    Compute popularity score based on citation metrics.
    For new papers, this will often be 0.
    """
    citations_s2 = paper.get("citations_s2") or 0
    citations_oa = paper.get("citations_oa") or 0
    
    # Use log scale to prevent outliers from dominating
    s2_score = math.log1p(citations_s2) / 10
    oa_score = math.log1p(citations_oa) / 10
    
    # Combined score (weights from config)
    score = CITATION_WEIGHT_S2 * s2_score + CITATION_WEIGHT_OA * oa_score
    
    return min(score, 1.0)


def compute_combined_score(paper: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """
    Compute all scores for a paper.
    Returns dict with individual scores and combined score.
    """
    interests = config.get("interests", {})
    keywords = interests.get("keywords", [])
    project_contexts = interests.get("project_contexts", [])
    
    all_terms = keywords + project_contexts
    
    keyword_score = compute_keyword_score(paper, all_terms)
    popularity_score = compute_popularity_score(paper)
    
    tier = get_tier(paper["primary_category"], config)
    
    # Combined score for ranking (weights from config)
    combined = KEYWORD_WEIGHT * keyword_score + POPULARITY_WEIGHT * popularity_score
    
    return {
        "keyword_score": keyword_score,
        "popularity_score": popularity_score,
        "combined_score": combined,
        "tier": tier,
    }


def filter_papers(papers: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
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
        scored_paper = {**paper, **scores}
        scored_papers.append(scored_paper)
    
    def get_threshold(paper):
        tier = paper["tier"]
        if tier == 1:
            return tier1_threshold
        elif tier == 2:
            return tier2_threshold
        elif tier == 3:
            return tier3_threshold
        return 0.9
    
    # Separate papers
    popular = []
    interest = []
    rejected = []
    near_misses = []
    
    for paper in scored_papers:
        threshold = get_threshold(paper)
        
        if paper["combined_score"] >= threshold:
            if paper["popularity_score"] > paper["keyword_score"]:
                popular.append(paper)
            else:
                interest.append(paper)
        else:
            if paper["combined_score"] >= threshold * NEAR_MISS_MULTIPLIER:
                near_misses.append(paper)
            else:
                rejected.append(paper)
    
    # Sort by score
    popular.sort(key=lambda x: x["combined_score"], reverse=True)
    interest.sort(key=lambda x: x["combined_score"], reverse=True)
    near_misses.sort(key=lambda x: x["combined_score"], reverse=True)
    
    # Select serendipity (random from rejected, but not lowest)
    mid_rejected = [p for p in rejected if p["combined_score"] > NEAR_MISS_THRESHOLD]
    serendipity = random.sample(mid_rejected, min(serendipity_count, len(mid_rejected)))
    
    # Select random negatives (truly low scorers)
    low_rejected = [p for p in rejected if p["combined_score"] <= NEAR_MISS_THRESHOLD]
    random_negatives = random.sample(low_rejected, min(random_negative_count, len(low_rejected)))
    
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


def save_filtered_results(results: dict[str, Any], output_path: Path) -> None:
    """Save filtered results to JSON."""
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
        json.dump(output, f, indent=2, default=str)


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
    
    # Log summary for cron runs
    logger.info(
        "Filter summary: total=%d, passed=%d, rejected=%d, popular=%d, interest=%d, serendipity=%d, near_misses=%d, random_negatives=%d",
        results.get("total_papers", 0),
        results.get("total_passed", 0),
        results.get("total_rejected", 0),
        len(results.get("popular", [])),
        len(results.get("interest", [])),
        len(results.get("serendipity", [])),
        len(results.get("near_misses", [])),
        len(results.get("random_negatives", []))
    )


def main():
    parser = argparse.ArgumentParser(description="Filter papers into streams")
    parser.add_argument("--date", required=True, help="Date to filter (YYYY-MM-DD)")
    parser.add_argument("--output", help="Output JSON path (default: data/filtered/YYYY-MM-DD.json)")
    parser.add_argument("--dry-run", action="store_true", help="Run filtering without writing output")
    args = parser.parse_args()
    
    # Validate date
    target_date = validate_date(args.date)
    
    console.print(f"[bold green]Filtering papers for {target_date}[/bold green]")
    
    # Use shared config (set DAILY_BRIEFS_CONFIG env var to override)
    config = CONFIG
    conn = get_db_connection()
    try:
        papers = get_papers_for_date(conn, target_date)
        console.print(f"Papers to filter: {len(papers)}")
        
        if not papers:
            console.print("[yellow]No papers found for this date.[/yellow]")
            return
        
        results = filter_papers(papers, config)
        print_summary(results)
        
        # Save results
        if args.dry_run:
            console.print("[yellow]Dry run - not saving filtered results.[/yellow]")
        else:
            FILTERED_DIR.mkdir(parents=True, exist_ok=True)
            output_path = Path(args.output) if args.output else FILTERED_DIR / f"{target_date}.json"
            
            save_filtered_results(results, output_path)
            console.print(f"\n[bold green]✓ Saved filtered results to {output_path}[/bold green]")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
