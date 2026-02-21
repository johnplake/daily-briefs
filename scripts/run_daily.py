#!/usr/bin/env python3
"""
Daily pipeline runner.

Runs the full pipeline:
1. Ingest papers from arXiv (with text extraction)
2. Enrich metadata via APIs (citations)
3. Filter into streams
4. Generate report

Can be run via cron or manually.
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

console = Console()


def run_step(script: str, args: list, step_name: str) -> bool:
    """Run a pipeline step and report status."""
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]Step: {step_name}[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
    
    cmd = [sys.executable, script] + args
    result = subprocess.run(cmd, cwd=Path(script).parent.parent)
    
    if result.returncode != 0:
        console.print(f"\n[bold red]✗ {step_name} failed with code {result.returncode}[/bold red]")
        return False
    
    console.print(f"\n[bold green]✓ {step_name} complete[/bold green]")
    return True


def main():
    parser = argparse.ArgumentParser(description="Run daily brief pipeline")
    parser.add_argument("--date", help="Date to process (YYYY-MM-DD). Default: today")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion step")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip enrichment step")
    parser.add_argument("--skip-filter", action="store_true", help="Skip filtering step")
    parser.add_argument("--skip-report", action="store_true", help="Skip report generation")
    parser.add_argument("--no-text", action="store_true", help="Skip text extraction")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3], help="Only process specific tier")
    args = parser.parse_args()
    
    # Determine date
    if args.date:
        target_date = args.date
    else:
        target_date = datetime.now().strftime("%Y-%m-%d")
    
    console.print(f"[bold magenta]Daily Brief Pipeline for {target_date}[/bold magenta]")
    
    scripts_dir = Path(__file__).parent
    
    # Step 1: Ingest
    if not args.skip_ingest:
        ingest_args = ["--date", target_date]
        if not args.no_text:
            ingest_args.append("--extract-text")
        if args.tier:
            ingest_args.extend(["--tier", str(args.tier)])
        
        if not run_step(str(scripts_dir / "ingest.py"), ingest_args, "Ingestion"):
            return 1
    
    # Step 2: Enrich
    if not args.skip_enrich:
        enrich_args = ["--date", target_date]
        
        if not run_step(str(scripts_dir / "enrich.py"), enrich_args, "Enrichment"):
            console.print("[yellow]Enrichment failed, continuing anyway...[/yellow]")
    
    # Step 3: Filter
    if not args.skip_filter:
        filter_args = ["--date", target_date]
        
        if not run_step(str(scripts_dir / "filter.py"), filter_args, "Filtering"):
            return 1
    
    # Step 4: Report
    if not args.skip_report:
        report_args = ["--date", target_date]
        
        if not run_step(str(scripts_dir / "report.py"), report_args, "Report Generation"):
            return 1
    
    console.print(f"\n[bold green]{'='*60}[/bold green]")
    console.print(f"[bold green]Pipeline complete for {target_date}[/bold green]")
    console.print(f"[bold green]{'='*60}[/bold green]")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
