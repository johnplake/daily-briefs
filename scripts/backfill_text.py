#!/usr/bin/env python3
"""
Backfill text extraction for papers that have metadata but no paper.txt.

Downloads PDF, extracts text with PyMuPDF, saves .txt, deletes PDF.
"""

import argparse
import sqlite3
import time
from pathlib import Path

import fitz
import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def download_file(url: str, dest_path: Path, timeout: int = 120) -> bool:
    """Download a file from URL."""
    try:
        response = requests.get(url, timeout=timeout, stream=True)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        return False


def extract_text_from_pdf(pdf_path: Path) -> str | None:
    """Extract text from PDF using PyMuPDF."""
    try:
        doc = fitz.open(pdf_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)
    except Exception as e:
        console.print(f"[yellow]Extract failed: {e}[/yellow]")
        return None


def get_papers_without_text(conn: sqlite3.Connection, limit: int = None) -> list:
    """Get papers that need text extraction."""
    cursor = conn.execute("SELECT arxiv_id, local_path, pdf_url FROM papers")
    
    papers = []
    for arxiv_id, local_path, pdf_url in cursor.fetchall():
        text_path = Path(local_path) / "paper.txt"
        if not text_path.exists():
            papers.append({
                "arxiv_id": arxiv_id,
                "local_path": local_path,
                "pdf_url": pdf_url,
            })
    
    if limit:
        papers = papers[:limit]
    
    return papers


def main():
    parser = argparse.ArgumentParser(description="Backfill text extraction")
    parser.add_argument("--limit", type=int, help="Limit number of papers")
    parser.add_argument("--dry-run", action="store_true", help="Just count, don't process")
    args = parser.parse_args()
    
    console.print("[bold green]Text Extraction Backfill[/bold green]")
    
    project_root = Path(__file__).parent.parent
    db_path = project_root / "data" / "index" / "papers.db"
    
    conn = sqlite3.connect(db_path)
    papers = get_papers_without_text(conn, args.limit)
    conn.close()
    
    console.print(f"Papers needing text extraction: {len(papers)}")
    
    if args.dry_run or not papers:
        return
    
    success = 0
    failed = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting...", total=len(papers))
        
        for i, paper in enumerate(papers):
            progress.update(task, description=f"{paper['arxiv_id']} ({i+1}/{len(papers)})")
            
            paper_dir = Path(paper["local_path"])
            paper_dir.mkdir(parents=True, exist_ok=True)
            
            pdf_path = paper_dir / "paper.pdf"
            text_path = paper_dir / "paper.txt"
            
            # Download PDF
            if download_file(paper["pdf_url"], pdf_path):
                # Extract text
                text = extract_text_from_pdf(pdf_path)
                if text:
                    with open(text_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    success += 1
                else:
                    failed += 1
                
                # Delete PDF
                try:
                    pdf_path.unlink()
                except Exception:
                    pass
            else:
                failed += 1
            
            progress.advance(task)
            time.sleep(0.5)  # Be nice to arXiv
    
    console.print(f"\n[bold green]✓ Extracted: {success}[/bold green]")
    if failed:
        console.print(f"[yellow]Failed: {failed}[/yellow]")


if __name__ == "__main__":
    main()
