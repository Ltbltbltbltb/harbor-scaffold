#!/bin/bash
# Verifier: structured_text — checks word count analysis output

OUTPUT_FILE="/logs/agent/output.txt"
REWARD_FILE="/logs/verifier/reward.txt"
mkdir -p /logs/verifier

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "0" > "$REWARD_FILE"
    exit 0
fi

SCORE=0
TOTAL=3

# Check 1: Word count is 44
WORDS=$(python3 -c "
import re
with open('$OUTPUT_FILE') as f:
    text = f.read()
m = re.search(r'Words:\s*(\d+)', text)
print(m.group(1) if m else '')
" 2>/dev/null)
if [ "$WORDS" = "44" ]; then
    SCORE=$((SCORE + 1))
fi

# Check 2: Sentence count is 4
SENTENCES=$(python3 -c "
import re
with open('$OUTPUT_FILE') as f:
    text = f.read()
m = re.search(r'Sentences:\s*(\d+)', text)
print(m.group(1) if m else '')
" 2>/dev/null)
if [ "$SENTENCES" = "4" ]; then
    SCORE=$((SCORE + 1))
fi

# Check 3: Unique words label is present and is a number
HAS_UNIQUE=$(python3 -c "
import re
with open('$OUTPUT_FILE') as f:
    text = f.read()
m = re.search(r'Unique words:\s*(\d+)', text)
print('yes' if m else 'no')
" 2>/dev/null)
if [ "$HAS_UNIQUE" = "yes" ]; then
    SCORE=$((SCORE + 1))
fi

echo "scale=2; $SCORE / $TOTAL" | bc > "$REWARD_FILE"
