#!/usr/bin/env python3
"""
Report generation script.

Generates daily markdown reports from filtered results.
Each paper includes:
- Title, authors, arXiv link
- TLDR / key contribution
- Why it was selected
- Feedback link (GitHub Issue)
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml
from rich.console import Console

from config import CONFIG, PROJECT_ROOT, FILTERED_DIR, REPORTS_DIR, REPORT, validate_date

console = Console()

# Report settings from config
GITHUB_REPO = CONFIG.get("github", {}).get("repo", "johnplake/daily-briefs")
MAX_AUTHORS = REPORT["max_authors"]


def load_filtered_results(results_path: Path) -> dict:
    """Load filtered results from JSON."""
    with open(results_path) as f:
        return json.load(f)


def generate_tldr(paper: dict) -> str:
    """
    Generate a TLDR for a paper.
    For MVP: Extract first 2 sentences of abstract.
    Future: Use LLM for proper summarization.
    """
    abstract = paper.get("abstract", "") or ""
    
    sentences = abstract.replace("\n", " ").split(". ")
    
    if len(sentences) >= 2:
        tldr = ". ".join(sentences[:2]) + "."
    else:
        tldr = abstract[:300] + "..." if len(abstract) > 300 else abstract
    
    return tldr


def generate_selection_reason(paper: dict) -> str:
    """Generate explanation for why paper was selected."""
    reasons = []
    
    keyword_score = paper.get("keyword_score", 0)
    popularity_score = paper.get("popularity_score", 0)
    tier = paper.get("tier", 0)
    
    if keyword_score > 0.3:
        reasons.append("Strong keyword match with your interests")
    elif keyword_score > 0.1:
        reasons.append("Matches some of your interest keywords")
    
    if popularity_score > 0.3:
        reasons.append("Getting attention in the field")
    
    tier_names = {1: "primary", 2: "secondary", 3: "tertiary"}
    if tier in tier_names:
        reasons.append(f"From {tier_names[tier]} category ({paper.get('primary_category', 'unknown')})")
    
    if not reasons:
        reasons.append("Selected for serendipity")
    
    return "; ".join(reasons)


def generate_feedback_url(paper: dict, stream: str, date: str) -> str | None:
    """Generate GitHub Issue URL for feedback. Returns None if github.repo not configured."""
    if not GITHUB_REPO:
        return None
    
    # Sanitize inputs (defense in depth - data comes from arXiv but could have weird chars)
    paper_id = re.sub(r'[\r\n]', '', paper.get("paper_id", "unknown"))
    title = re.sub(r'[\r\n]', ' ', paper.get("title", "Unknown") or "Unknown")[:200]
    
    issue_title = quote(f"[Feedback] {paper_id}")
    
    body = f"""**Paper ID:** {paper_id}
**Title:** {title}
**Date:** {date}
**Stream:** {stream}

**Rating:** 
- [ ] 👍 Useful
- [ ] 🤷 Marginal  
- [ ] 👎 Not useful

**Notes:**
"""
    issue_body = quote(body)
    
    return f"https://github.com/{GITHUB_REPO}/issues/new?title={issue_title}&body={issue_body}&labels=feedback"


def format_authors(authors: list, max_authors: int = None) -> str:
    """Format author list for display."""
    if max_authors is None:
        max_authors = MAX_AUTHORS
    if not authors:
        return "Unknown"
    
    if isinstance(authors[0], dict):
        names = [a.get("name", "") for a in authors]
    else:
        names = authors
    
    if len(names) <= max_authors:
        return ", ".join(names)
    else:
        return ", ".join(names[:max_authors]) + f" et al. ({len(names)} authors)"


def generate_report(results: dict, date: str, config: dict) -> str:
    """Generate markdown report from filtered results."""
    report_config = config.get("report", {})
    max_per_stream = report_config.get("max_papers_per_stream", 10)
    
    lines = []
    
    # Header
    lines.append(f"# Daily Brief: {date}")
    lines.append("")
    lines.append(f"*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}*")
    lines.append("")
    
    # Stats
    stats = results.get("stats", {})
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total papers processed:** {stats.get('total_passed', 0) + stats.get('total_rejected', 0)}")
    lines.append(f"- **Papers selected:** {stats.get('total_passed', 0)}")
    lines.append(f"  - Popular: {stats.get('popular_count', 0)}")
    lines.append(f"  - Interest: {stats.get('interest_count', 0)}")
    lines.append(f"  - Serendipity: {stats.get('serendipity_count', 0)}")
    lines.append("")
    
    # Streams
    streams = [
        ("popular", "🔥 Popular", "Trending and getting attention in the field"),
        ("interest", "🎯 Interest Match", "Matches your keywords and research interests"),
        ("serendipity", "🎲 Serendipity", "Random picks you might find interesting"),
    ]
    
    for stream_key, stream_title, stream_desc in streams:
        papers = results.get(stream_key, [])[:max_per_stream]
        
        if not papers:
            continue
        
        lines.append(f"## {stream_title}")
        lines.append("")
        lines.append(f"*{stream_desc}*")
        lines.append("")
        
        for i, paper in enumerate(papers, 1):
            paper_id = paper.get("paper_id", "unknown")
            title = paper.get("title", "Unknown") or "Unknown"
            authors = format_authors(paper.get("authors", []))
            arxiv_url = paper.get("arxiv_url", f"https://arxiv.org/abs/{paper_id}")
            pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
            
            tldr = generate_tldr(paper)
            reason = generate_selection_reason(paper)
            feedback_url = generate_feedback_url(paper, stream_key.capitalize(), date)
            
            lines.append(f"### {i}. [{title}]({arxiv_url})")
            lines.append("")
            lines.append(f"**{paper_id}** | {authors}")
            lines.append("")
            lines.append(f"> {tldr}")
            lines.append("")
            lines.append(f"**Why selected:** {reason}")
            lines.append("")
            if feedback_url:
                lines.append(f"📄 [PDF]({pdf_url}) | 💬 [Feedback]({feedback_url})")
            else:
                lines.append(f"📄 [PDF]({pdf_url})")
            lines.append("")
            lines.append("---")
            lines.append("")
    
    # Feedback candidates
    lines.append("## 📝 Feedback Candidates")
    lines.append("")
    lines.append("*These papers were selected for calibration feedback. Please rate them too!*")
    lines.append("")
    
    # Near misses
    near_misses = results.get("near_misses", [])
    if near_misses:
        lines.append("### Near Misses")
        lines.append("")
        lines.append("*Papers that almost made the cut. Were we right to exclude them?*")
        lines.append("")
        
        for paper in near_misses:
            paper_id = paper.get("paper_id", "unknown")
            title = paper.get("title", "Unknown") or "Unknown"
            arxiv_url = paper.get("arxiv_url", f"https://arxiv.org/abs/{paper_id}")
            feedback_url = generate_feedback_url(paper, "Near-miss", date)
            
            if feedback_url:
                lines.append(f"- [{title}]({arxiv_url}) ([Feedback]({feedback_url}))")
            else:
                lines.append(f"- [{title}]({arxiv_url})")
        
        lines.append("")
    
    # Random negatives
    random_negatives = results.get("random_negatives", [])
    if random_negatives:
        lines.append("### Random Negatives")
        lines.append("")
        lines.append("*Random low-scored papers for calibration. Should any of these have been included?*")
        lines.append("")
        
        for paper in random_negatives:
            paper_id = paper.get("paper_id", "unknown")
            title = paper.get("title", "Unknown") or "Unknown"
            arxiv_url = paper.get("arxiv_url", f"https://arxiv.org/abs/{paper_id}")
            feedback_url = generate_feedback_url(paper, "Random-negative", date)
            
            if feedback_url:
                lines.append(f"- [{title}]({arxiv_url}) ([Feedback]({feedback_url}))")
            else:
                lines.append(f"- [{title}]({arxiv_url})")
        
        lines.append("")
    
    # Footer
    lines.append("---")
    lines.append("")
    lines.append("*Generated by [daily-briefs](https://github.com/johnplake/daily-briefs)*")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate daily report")
    parser.add_argument("--date", required=True, help="Date for report (YYYY-MM-DD)")
    parser.add_argument("--input", help="Input filtered JSON (default: data/filtered/YYYY-MM-DD.json)")
    parser.add_argument("--output", help="Output report path (default: reports/YYYY-MM-DD.md)")
    args = parser.parse_args()
    
    # Validate date
    target_date = validate_date(args.date)
    
    console.print(f"[bold green]Generating report for {target_date}[/bold green]")
    
    # Use shared config (set DAILY_BRIEFS_CONFIG env var to override)
    config = CONFIG
    
    input_path = Path(args.input) if args.input else FILTERED_DIR / f"{target_date}.json"
    output_path = Path(args.output) if args.output else REPORTS_DIR / f"{target_date}.md"
    
    if not input_path.exists():
        console.print(f"[red]Filtered results not found: {input_path}[/red]")
        console.print("[yellow]Run filter.py first.[/yellow]")
        return
    
    results = load_filtered_results(input_path)
    report = generate_report(results, target_date, config)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)
    
    console.print(f"\n[bold green]✓ Report saved to {output_path}[/bold green]")
    
    # Print preview
    console.print("\n[bold]Preview (first 50 lines):[/bold]")
    for line in report.split("\n")[:50]:
        console.print(line)


if __name__ == "__main__":
    main()
