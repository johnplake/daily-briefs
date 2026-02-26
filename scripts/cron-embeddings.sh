#!/bin/bash
# Daily embeddings cron script
# Runs embed.py with UMAP, pings healthcheck, sends Telegram notification
#
# System cron entry (8pm CST = 02:00 UTC next day):
#   0 2 * * 2-6 /home/node/.openclaw/workspace/Projects/daily-briefs/scripts/cron-embeddings.sh >> /tmp/daily-embeddings.log 2>&1

set -uo pipefail

PROJECT_DIR="/home/node/.openclaw/workspace/Projects/daily-briefs"
HC_URL="https://hc-ping.com/a3bc824a-c526-4365-a6c0-6faaaed8098f"
TELEGRAM_CHAT="-1003700767295"
TELEGRAM_TOPIC="934"
TIMEOUT_SECONDS=7200  # 2 hours

# Read bot token from openclaw config
TELEGRAM_TOKEN=$(jq -r '.channels.telegram.token' ~/.openclaw/openclaw.json)

send_telegram() {
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT" \
        -d message_thread_id="$TELEGRAM_TOPIC" \
        -d text="$1" \
        -d parse_mode="Markdown" > /dev/null
}

cd "$PROJECT_DIR"

echo "$(date -Iseconds) Starting embeddings..."

# Run embeddings with timeout
if OUTPUT=$(timeout "$TIMEOUT_SECONDS" .venv/bin/python scripts/embed.py --umap 2>&1); then
    # Extract counts from output
    EMBEDDED=$(echo "$OUTPUT" | grep -oP 'Embedded \K\d+' || echo "0")
    PROJECTED=$(echo "$OUTPUT" | grep -oP 'Projected \K\d+' || echo "0")
    
    # Success
    curl -fsS --retry 3 "$HC_URL"
    send_telegram "✅ Embeddings complete
• Papers embedded: $EMBEDDED
• UMAP projections: $PROJECTED"
    echo "$(date -Iseconds) Success: embedded=$EMBEDDED, projected=$PROJECTED"
else
    EXIT_CODE=$?
    # Failure
    curl -fsS --retry 3 "$HC_URL/fail"
    
    if [ $EXIT_CODE -eq 124 ]; then
        send_telegram "❌ Embeddings FAILED (timeout after ${TIMEOUT_SECONDS}s)"
        echo "$(date -Iseconds) Failed: timeout"
    else
        # Truncate output for Telegram (max ~4000 chars)
        TRUNCATED="${OUTPUT:0:500}"
        send_telegram "❌ Embeddings FAILED (exit $EXIT_CODE)
\`\`\`
$TRUNCATED
\`\`\`"
        echo "$(date -Iseconds) Failed: exit code $EXIT_CODE"
        echo "$OUTPUT"
    fi
    exit 1
fi
