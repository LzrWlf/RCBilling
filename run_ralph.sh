#!/bin/bash
# Ralph Wiggum Loop for RCBilling invoice scraping bug
# Runs Claude Code iteratively until invoices are found or max iterations reached.
#
# Usage: ./run_ralph.sh [max_iterations]
# Default: 5 iterations max

set -uo pipefail

MAX_ITERATIONS=${1:-5}
ITERATION=0
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$PROJECT_DIR/LOOP_LOG.md"

cd "$PROJECT_DIR"

echo "=========================================="
echo "  Ralph Wiggum Loop - Invoice Scrape Fix"
echo "  Max iterations: $MAX_ITERATIONS"
echo "=========================================="
echo ""

while [ $ITERATION -lt $MAX_ITERATIONS ]; do
    ITERATION=$((ITERATION + 1))

    # Check if previous iteration succeeded
    if [ -f "$LOG_FILE" ] && tail -1 "$LOG_FILE" | grep -q "SUCCESS"; then
        echo "SUCCESS detected in LOOP_LOG.md — stopping loop"
        break
    fi

    echo "--- Iteration $ITERATION / $MAX_ITERATIONS --- ($(date))"

    # Run Claude Code with the prompt (--print mode = non-interactive)
    cat "$PROJECT_DIR/PROMPT.md" | claude --print 2>&1 || true

    echo ""

    # Check for success after this iteration
    if [ -f "$LOG_FILE" ] && tail -1 "$LOG_FILE" | grep -q "SUCCESS"; then
        echo ""
        echo "=========================================="
        echo "  BUG FIXED after $ITERATION iteration(s)!"
        echo "=========================================="
        break
    fi

    # Brief pause between iterations
    sleep 3
done

if [ $ITERATION -ge $MAX_ITERATIONS ] && ! tail -1 "$LOG_FILE" 2>/dev/null | grep -q "SUCCESS"; then
    echo ""
    echo "=========================================="
    echo "  Max iterations ($MAX_ITERATIONS) reached"
    echo "  Check LOOP_LOG.md for details"
    echo "=========================================="
fi

echo ""
echo "Loop finished at $(date)"
