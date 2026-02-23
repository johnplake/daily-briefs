#!/usr/bin/env python3
"""
Sanity check script for daily-briefs.

Verifies:
- Database exists and is readable
- Paper counts and data integrity
- FAISS index exists and matches DB
- UMAP coordinates present for embedded papers
- FTS index is in sync

Usage:
    daily-briefs check           # Run all checks
    daily-briefs check --fix     # Attempt to fix issues (where possible)
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import faiss
from rich.console import Console
from rich.table import Table

from config import DB_PATH, EMBEDDINGS_DIR, TEXT_DIR, get_db_connection
from logging_config import setup_logging

console = Console()
logger = setup_logging("check")


def check_database() -> tuple[bool, dict]:
    """Check database exists and is readable."""
    issues = []
    stats = {}
    
    if not DB_PATH.exists():
        issues.append(f"Database not found: {DB_PATH}")
        return False, {"issues": issues}
    
    try:
        conn = get_db_connection()
        
        # Count papers
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        stats["total_papers"] = total
        
        # Papers with embeddings
        with_emb = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE embedding_idx IS NOT NULL"
        ).fetchone()[0]
        stats["papers_with_embeddings"] = with_emb
        
        # Papers with UMAP
        with_umap = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE umap_x IS NOT NULL AND umap_y IS NOT NULL"
        ).fetchone()[0]
        stats["papers_with_umap"] = with_umap
        
        # Hidden papers
        hidden = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE hidden = 1"
        ).fetchone()[0]
        stats["hidden_papers"] = hidden
        
        # Papers with text
        with_text = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE text_extracted = 1"
        ).fetchone()[0]
        stats["papers_with_text"] = with_text
        
        # Check for NULL abstracts (shouldn't embed these)
        null_abstract = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE abstract IS NULL OR abstract = ''"
        ).fetchone()[0]
        stats["papers_without_abstract"] = null_abstract
        
        # Check embedding_idx range
        max_idx = conn.execute(
            "SELECT MAX(embedding_idx) FROM papers WHERE embedding_idx IS NOT NULL"
        ).fetchone()[0]
        min_idx = conn.execute(
            "SELECT MIN(embedding_idx) FROM papers WHERE embedding_idx IS NOT NULL"
        ).fetchone()[0]
        stats["embedding_idx_range"] = (min_idx, max_idx)
        
        # Check for duplicate embedding_idx
        dups = conn.execute("""
            SELECT embedding_idx, COUNT(*) as cnt 
            FROM papers 
            WHERE embedding_idx IS NOT NULL 
            GROUP BY embedding_idx 
            HAVING cnt > 1
        """).fetchall()
        if dups:
            issues.append(f"Duplicate embedding_idx values: {len(dups)}")
            stats["duplicate_embedding_idx"] = len(dups)
        
        # Check for gaps in embedding_idx
        if max_idx is not None and with_emb > 0:
            expected = max_idx - min_idx + 1 if min_idx is not None else 0
            if expected != with_emb:
                issues.append(f"Gaps in embedding_idx: expected {expected}, have {with_emb}")
        
        conn.close()
        
    except Exception as e:
        issues.append(f"Database error: {e}")
        return False, {"issues": issues}
    
    stats["issues"] = issues
    return len(issues) == 0, stats


def check_faiss_index() -> tuple[bool, dict]:
    """Check FAISS index exists and matches DB."""
    issues = []
    stats = {}
    
    index_path = EMBEDDINGS_DIR / "faiss.index"
    
    if not index_path.exists():
        issues.append(f"FAISS index not found: {index_path}")
        return False, {"issues": issues}
    
    try:
        index = faiss.read_index(str(index_path))
        stats["faiss_vectors"] = index.ntotal
        stats["embedding_dim"] = index.d
        
        # Compare with DB
        conn = get_db_connection()
        db_count = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE embedding_idx IS NOT NULL"
        ).fetchone()[0]
        max_idx = conn.execute(
            "SELECT MAX(embedding_idx) FROM papers WHERE embedding_idx IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        
        stats["db_embedded_count"] = db_count
        
        # Check counts match
        if db_count != index.ntotal:
            issues.append(
                f"Count mismatch: DB has {db_count} embedded papers, "
                f"FAISS has {index.ntotal} vectors"
            )
        
        # Check max index is within bounds
        if max_idx is not None and max_idx >= index.ntotal:
            issues.append(
                f"Out of bounds: max embedding_idx={max_idx}, "
                f"but FAISS only has {index.ntotal} vectors"
            )
            
    except Exception as e:
        issues.append(f"FAISS error: {e}")
        return False, {"issues": issues}
    
    stats["issues"] = issues
    return len(issues) == 0, stats


def check_umap_sync() -> tuple[bool, dict]:
    """Check UMAP coordinates are in sync with embeddings."""
    issues = []
    stats = {}
    
    try:
        conn = get_db_connection()
        
        # Papers with embedding but no UMAP
        missing_umap = conn.execute("""
            SELECT COUNT(*) FROM papers 
            WHERE embedding_idx IS NOT NULL 
              AND (umap_x IS NULL OR umap_y IS NULL)
        """).fetchone()[0]
        stats["missing_umap"] = missing_umap
        
        if missing_umap > 0:
            issues.append(f"{missing_umap} papers have embeddings but no UMAP coordinates")
        
        # Papers with UMAP but no embedding (shouldn't happen)
        orphan_umap = conn.execute("""
            SELECT COUNT(*) FROM papers 
            WHERE embedding_idx IS NULL 
              AND (umap_x IS NOT NULL OR umap_y IS NOT NULL)
        """).fetchone()[0]
        stats["orphan_umap"] = orphan_umap
        
        if orphan_umap > 0:
            issues.append(f"{orphan_umap} papers have UMAP coordinates but no embedding")
        
        conn.close()
        
    except Exception as e:
        issues.append(f"UMAP check error: {e}")
        return False, {"issues": issues}
    
    stats["issues"] = issues
    return len(issues) == 0, stats


def check_fts_sync() -> tuple[bool, dict]:
    """Check FTS index is in sync with papers table."""
    issues = []
    stats = {}
    
    try:
        conn = get_db_connection()
        
        # Count papers
        paper_count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        
        # Count FTS entries
        fts_count = conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0]
        
        stats["paper_count"] = paper_count
        stats["fts_count"] = fts_count
        
        if paper_count != fts_count:
            issues.append(
                f"FTS out of sync: {paper_count} papers, {fts_count} FTS entries"
            )
        
        conn.close()
        
    except Exception as e:
        issues.append(f"FTS check error: {e}")
        return False, {"issues": issues}
    
    stats["issues"] = issues
    return len(issues) == 0, stats


def check_text_files() -> tuple[bool, dict]:
    """Check that text_extracted=1 papers actually have text files."""
    issues = []
    stats = {}
    
    try:
        conn = get_db_connection()
        cursor = conn.execute(
            "SELECT paper_id, paper_source, announced_date FROM papers WHERE text_extracted = 1"
        )
        papers = cursor.fetchall()
        missing = 0
        for row in papers:
            text_path = TEXT_DIR / row["paper_source"] / row["announced_date"] / row["paper_id"] / "paper.txt"
            if not text_path.exists():
                missing += 1
        stats["papers_with_flag"] = len(papers)
        stats["missing_files"] = missing
        if missing > 0:
            issues.append(f"{missing} papers have text_extracted=1 but file is missing")
        conn.close()
    except Exception as e:
        issues.append(f"Text file check error: {e}")
        return False, {"issues": issues}
    
    stats["issues"] = issues
    return len(issues) == 0, stats


def run_all_checks() -> dict:
    """Run all sanity checks and return results."""
    results = {}
    
    console.print("\n[bold cyan]Running sanity checks...[/bold cyan]\n")
    
    # Database
    console.print("[cyan]Checking database...[/cyan]")
    ok, stats = check_database()
    results["database"] = {"ok": ok, **stats}
    status = "[green]✓ OK[/green]" if ok else "[red]✗ ISSUES[/red]"
    console.print(f"  Database: {status}")
    
    # FAISS
    console.print("[cyan]Checking FAISS index...[/cyan]")
    ok, stats = check_faiss_index()
    results["faiss"] = {"ok": ok, **stats}
    status = "[green]✓ OK[/green]" if ok else "[red]✗ ISSUES[/red]"
    console.print(f"  FAISS: {status}")
    
    # UMAP
    console.print("[cyan]Checking UMAP sync...[/cyan]")
    ok, stats = check_umap_sync()
    results["umap"] = {"ok": ok, **stats}
    status = "[green]✓ OK[/green]" if ok else "[red]✗ ISSUES[/red]"
    console.print(f"  UMAP: {status}")
    
    # FTS
    console.print("[cyan]Checking FTS sync...[/cyan]")
    ok, stats = check_fts_sync()
    results["fts"] = {"ok": ok, **stats}
    status = "[green]✓ OK[/green]" if ok else "[red]✗ ISSUES[/red]"
    console.print(f"  FTS: {status}")

    # Text files
    console.print("[cyan]Checking text files...[/cyan]")
    ok, stats = check_text_files()
    results["text_files"] = {"ok": ok, **stats}
    status = "[green]✓ OK[/green]" if ok else "[red]✗ ISSUES[/red]"
    console.print(f"  Text files: {status}")
    
    return results


def print_summary(results: dict):
    """Print detailed summary of check results."""
    console.print("\n[bold]Summary:[/bold]")
    
    # Stats table
    table = Table(title="Database Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    
    db = results.get("database", {})
    table.add_row("Total papers", str(db.get("total_papers", "?")))
    table.add_row("With embeddings", str(db.get("papers_with_embeddings", "?")))
    table.add_row("With UMAP coords", str(db.get("papers_with_umap", "?")))
    table.add_row("With extracted text", str(db.get("papers_with_text", "?")))
    table.add_row("Hidden", str(db.get("hidden_papers", "?")))
    table.add_row("Without abstract", str(db.get("papers_without_abstract", "?")))
    
    faiss_stats = results.get("faiss", {})
    table.add_row("FAISS vectors", str(faiss_stats.get("faiss_vectors", "?")))
    table.add_row("Embedding dim", str(faiss_stats.get("embedding_dim", "?")))
    
    console.print(table)
    
    # Issues
    all_issues = []
    for check_name, check_results in results.items():
        issues = check_results.get("issues", [])
        for issue in issues:
            all_issues.append(f"[{check_name}] {issue}")
    
    if all_issues:
        console.print("\n[bold red]Issues found:[/bold red]")
        for issue in all_issues:
            console.print(f"  • {issue}")
            logger.warning(issue)
    else:
        console.print("\n[bold green]✓ All checks passed![/bold green]")
        logger.info("All sanity checks passed")


def main():
    parser = argparse.ArgumentParser(description="Run sanity checks on daily-briefs data")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix issues")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only show issues")
    args = parser.parse_args()
    
    logger.info("Starting sanity checks")
    
    results = run_all_checks()
    
    if not args.quiet:
        print_summary(results)
    
    # Check if any failed
    all_ok = all(r.get("ok", False) for r in results.values())
    
    if not all_ok:
        logger.error("Sanity checks failed")
        if args.fix:
            console.print("\n[yellow]--fix not yet implemented. Manual intervention required.[/yellow]")
        sys.exit(1)
    
    logger.info("Sanity checks completed successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
