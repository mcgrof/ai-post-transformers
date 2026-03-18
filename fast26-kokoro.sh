#!/bin/bash
# FAST26 Serial Podcast Pipeline — Kokoro TTS
# Generates all 6 FAST26 papers serially using Kokoro voices
set -e
cd ~/devel/ai-post-transformers
source .venv/bin/activate
source ~/.enhance-bash 2>/dev/null

# Force Kokoro backend
export PODCAST_TTS_BACKEND=kokoro

PAPERS=(
  "https://www.usenix.org/system/files/fast26-liu-yang.pdf"
  "https://www.usenix.org/system/files/fast26-hu-shipeng.pdf"
  "https://www.usenix.org/system/files/fast26-zheng.pdf"
  "https://www.usenix.org/system/files/fast26-liu-qingyuan.pdf"
  "https://www.usenix.org/system/files/fast26-an.pdf"
  "https://www.usenix.org/system/files/fast26-liu-yubo.pdf"
)

TOTAL=${#PAPERS[@]}
GENERATED=0
FAILED=0

echo "============================================"
echo "FAST26 Kokoro Podcast Pipeline"
echo "Papers: $TOTAL"
echo "TTS: Kokoro (bm_george + af_kore)"
echo "============================================"
echo ""

for i in "${!PAPERS[@]}"; do
  NUM=$((i + 1))
  URL="${PAPERS[$i]}"
  SLUG=$(basename "$URL" .pdf)

  echo ""
  echo "============================================"
  echo "[$NUM/$TOTAL] $SLUG"
  echo "URL: $URL"
  echo "Started: $(date)"
  echo "============================================"

  GOAL="This paper is from USENIX FAST'26. Cover it as part of our FAST'26 conference series."

  if .venv/bin/python gen-podcast.py \
    --goal "$GOAL" \
    "$URL" 2>&1; then
    GENERATED=$((GENERATED + 1))
    echo "  ✅ Done: $SLUG"
  else
    FAILED=$((FAILED + 1))
    echo "  ❌ Failed: $SLUG"
  fi

  # Pause between papers to avoid rate limits
  if [ $NUM -lt $TOTAL ]; then
    echo "  Pausing 30s..."
    sleep 30
  fi
done

echo ""
echo "============================================"
echo "FAST26 Pipeline Complete"
echo "Generated: $GENERATED / $TOTAL"
echo "Failed: $FAILED"
echo "Finished: $(date)"
echo "============================================"
