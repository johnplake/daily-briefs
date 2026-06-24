#!/bin/bash
# Daily arXiv Pipeline - Combined Script
# Runs the full daily pipeline: ingest → filter → report → embeddings → check
#
# System/OpenClaw cron entry (02:00 UTC, Mon-Fri):
#   0 2 * * 1-5 /home/node/.openclaw/workspace/Projects/daily-briefs/scripts/cron-daily-pipeline.sh >> /tmp/daily-pipeline.log 2>&1

set -uo pipefail

PROJECT_DIR="/home/node/.openclaw/workspace/Projects/daily-briefs"
TIMEOUT_SECONDS=14400  # 4 hours
TODAY=$(date -u +%Y-%m-%d)  # UTC to match arXiv announced_date

fail() {
    echo "$(date -Iseconds) FAILED: $1"
    exit 1
}

cd "$PROJECT_DIR"

echo "$(date -Iseconds) =========================================="
echo "$(date -Iseconds) Starting daily pipeline for $TODAY"
echo "$(date -Iseconds) =========================================="

# -----------------------------------------------------------------------------
# Step 1: Ingest (with text extraction)
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 1/6: Ingest..."
if ! INGEST_OUT=$(timeout 3600 .venv/bin/python scripts/ingest.py --extract-text 2>&1); then
    fail "${INGEST_OUT:0:200}" "Ingest"
fi
INSERTED=$(echo "$INGEST_OUT" | grep -oP 'Inserted: \K\d+' || echo "0")
TEXT_EXTRACTED=$(echo "$INGEST_OUT" | grep -oP 'Text extracted: \K\d+' || echo "0")
echo "$(date -Iseconds) Ingest complete: inserted=$INSERTED, text_extracted=$TEXT_EXTRACTED"

# -----------------------------------------------------------------------------
# Step 2: Filter
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 2/6: Filter..."
if ! FILTER_OUT=$(timeout 1800 .venv/bin/python scripts/filter.py --date "$TODAY" 2>&1); then
    fail "${FILTER_OUT:0:200}" "Filter"
fi
PASSED=$(echo "$FILTER_OUT" | grep -oP 'Total Passed\s+│\s+\K\d+' || echo "?")
INTEREST=$(echo "$FILTER_OUT" | grep -oP 'Interest\s+│\s+\K\d+' || echo "?")
SERENDIPITY=$(echo "$FILTER_OUT" | grep -oP 'Serendipity\s+│\s+\K\d+' || echo "?")
echo "$(date -Iseconds) Filter complete: passed=$PASSED, interest=$INTEREST, serendipity=$SERENDIPITY"

# -----------------------------------------------------------------------------
# Step 3: Report
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 3/6: Report..."
if ! REPORT_OUT=$(.venv/bin/python scripts/report.py --date "$TODAY" 2>&1); then
    fail "${REPORT_OUT:0:200}" "Report"
fi
echo "$(date -Iseconds) Report generated"

# -----------------------------------------------------------------------------
# Step 4: Commit and push report
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 4/6: Commit and push..."
REPORT_FILE="reports/${TODAY}.md"
if [ -f "$REPORT_FILE" ]; then
    git add "$REPORT_FILE"
    if git diff --cached --quiet; then
        echo "$(date -Iseconds) No changes to commit"
    else
        git commit -m "Daily report ${TODAY}"
        if ! git push; then
            fail "Git push failed" "Git Push"
        fi
        echo "$(date -Iseconds) Pushed to remote"
    fi
else
    fail "Report file not found: $REPORT_FILE" "Report"
fi

# -----------------------------------------------------------------------------
# Step 5: Embeddings + UMAP
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 5/6: Embeddings + UMAP..."
if ! EMBED_OUT=$(timeout 10800 .venv/bin/python scripts/embed.py --umap 2>&1); then
    fail "${EMBED_OUT:0:200}" "Embeddings"
fi
EMBEDDED=$(echo "$EMBED_OUT" | grep -oP 'Embedded \K\d+' || echo "0")
PROJECTED=$(echo "$EMBED_OUT" | grep -oP 'Projected \K\d+' || echo "0")
echo "$(date -Iseconds) Embeddings complete: embedded=$EMBEDDED, projected=$PROJECTED"

# -----------------------------------------------------------------------------
# Step 6: Sanity check
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) Step 6/6: Sanity check..."
if ! CHECK_OUT=$(.venv/bin/python scripts/check.py 2>&1); then
    fail "Sanity check failed" "Check"
fi
echo "$(date -Iseconds) Sanity check passed"

# -----------------------------------------------------------------------------
# Success!
# -----------------------------------------------------------------------------
echo "$(date -Iseconds) =========================================="
echo "$(date -Iseconds) Pipeline complete!"
echo "$(date -Iseconds) =========================================="

exit 0
