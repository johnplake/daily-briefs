#!/bin/bash
# Daily arXiv Brief cron script
# Runs ingest → filter → report, commits, pings healthcheck, sends Telegram
#
# System cron entry (8am CST = 14:00 UTC):
#   0 14 * * 1-5 /home/node/.openclaw/workspace/Projects/daily-briefs/scripts/cron-daily-brief.sh >> /tmp/daily-brief.log 2>&1

set -uo pipefail

PROJECT_DIR="/home/node/.openclaw/workspace/Projects/daily-briefs"
HC_URL="https://hc-ping.com/a8625459-cc2d-4232-8441-d4091de62f2a"
TELEGRAM_CHAT="8441537510"
TIMEOUT_SECONDS=7200  # 2 hours
TODAY=$(date +%Y-%m-%d)

# Read bot token from openclaw config
TELEGRAM_TOKEN=$(jq -r '.channels.telegram.token' ~/.openclaw/openclaw.json)

send_telegram() {
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT" \
        -d text="$1" \
        -d parse_mode="Markdown" > /dev/null
}

fail() {
    echo "$(date -Iseconds) FAILED: $1"
    curl -fsS --retry 3 "$HC_URL/fail"
    send_telegram "❌ Daily Brief FAILED: $1"
    exit 1
}

cd "$PROJECT_DIR"

echo "$(date -Iseconds) Starting daily brief for $TODAY..."

# Step 1: Ingest
echo "$(date -Iseconds) Step 1: Ingest..."
if ! INGEST_OUT=$(timeout "$TIMEOUT_SECONDS" .venv/bin/python scripts/ingest.py --extract-text 2>&1); then
    fail "Ingest failed: ${INGEST_OUT:0:200}"
fi
INSERTED=$(echo "$INGEST_OUT" | grep -oP 'Inserted: \K\d+' || echo "0")
echo "$(date -Iseconds) Ingest complete: inserted=$INSERTED"

# Step 2: Filter
echo "$(date -Iseconds) Step 2: Filter..."
if ! FILTER_OUT=$(timeout "$TIMEOUT_SECONDS" .venv/bin/python scripts/filter.py --date "$TODAY" 2>&1); then
    fail "Filter failed: ${FILTER_OUT:0:200}"
fi
PASSED=$(echo "$FILTER_OUT" | grep -oP 'Total Passed\s+│\s+\K\d+' || echo "?")
INTEREST=$(echo "$FILTER_OUT" | grep -oP 'Interest\s+│\s+\K\d+' || echo "?")
SERENDIPITY=$(echo "$FILTER_OUT" | grep -oP 'Serendipity\s+│\s+\K\d+' || echo "?")
echo "$(date -Iseconds) Filter complete: passed=$PASSED, interest=$INTEREST, serendipity=$SERENDIPITY"

# Step 3: Report
echo "$(date -Iseconds) Step 3: Report..."
if ! REPORT_OUT=$(.venv/bin/python scripts/report.py --date "$TODAY" 2>&1); then
    fail "Report failed: ${REPORT_OUT:0:200}"
fi
echo "$(date -Iseconds) Report generated"

# Step 4: Commit and push
echo "$(date -Iseconds) Step 4: Commit and push..."
REPORT_FILE="reports/${TODAY}.md"
if [ -f "$REPORT_FILE" ]; then
    git add "$REPORT_FILE"
    if git diff --cached --quiet; then
        echo "$(date -Iseconds) No changes to commit"
    else
        git commit -m "Daily report ${TODAY}"
        if ! git push; then
            fail "Git push failed"
        fi
        echo "$(date -Iseconds) Pushed to remote"
    fi
else
    fail "Report file not found: $REPORT_FILE"
fi

# Step 5: Success - ping healthcheck and notify
curl -fsS --retry 3 "$HC_URL"
send_telegram "✅ Daily Brief complete ($TODAY)
• Papers ingested: $INSERTED
• Papers selected: $PASSED
  - Interest: $INTEREST
  - Serendipity: $SERENDIPITY"

echo "$(date -Iseconds) Daily brief complete!"
