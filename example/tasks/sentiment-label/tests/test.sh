#!/bin/bash
# Verifier: keyword_pattern — checks sentiment classification output

OUTPUT_FILE="/logs/agent/output.txt"
REWARD_FILE="/logs/verifier/reward.txt"
mkdir -p /logs/verifier

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "0" > "$REWARD_FILE"
    exit 0
fi

SCORE=0
TOTAL=3

# Check 1: Sentiment is "positive" (case-insensitive)
if grep -qi "Sentiment:.*positive" "$OUTPUT_FILE"; then
    SCORE=$((SCORE + 1))
fi

# Check 2: Confidence field is present with valid value
if grep -qi "Confidence:.*\(high\|medium\|low\)" "$OUTPUT_FILE"; then
    SCORE=$((SCORE + 1))
fi

# Check 3: Key reason field is present and non-empty
if grep -qi "Key reason:" "$OUTPUT_FILE"; then
    SCORE=$((SCORE + 1))
fi

echo "scale=2; $SCORE / $TOTAL" | bc > "$REWARD_FILE"
