#!/bin/bash
# Production Sound Library Setup
# Sets up royalty-free sounds for high-quality podcast episodes

set -e

SOUNDS_DIR="sounds"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Production Sound Library Setup"
echo "=========================================="
echo ""

# Check if sounds directory exists
if [ ! -d "$SOUNDS_DIR" ]; then
    mkdir -p "$SOUNDS_DIR"
    echo "[1] Created sounds directory"
fi

# Check what's already downloaded
echo ""
echo "[2] Checking existing sounds:"
SOUND_COUNT=$(ls "$SOUNDS_DIR"/*.mp3 2>/dev/null | wc -l)
if [ "$SOUND_COUNT" -gt 0 ]; then
    echo "    Found $SOUND_COUNT sound files"
    echo ""
    ls -1 "$SOUNDS_DIR"/*.mp3 | sed 's/^/      /'
else
    echo "    No sound files found yet"
fi

echo ""
echo "[3] Setup Options:"
echo ""
echo "    A) Use Test Sounds (Fast, for development)"
echo "       - Run: python3 generate_test_sounds.py"
echo "       - Pros: Instant, no manual downloads"
echo "       - Cons: Synthetic sounds, not production quality"
echo ""
echo "    B) Manual Download (Recommended, ~30 min)"
echo "       - See: SOUND_DOWNLOADS_MANUAL.md"
echo "       - Sources: Incompetech, Freesound, Zapsplat"
echo "       - Result: Professional quality production sounds"
echo ""
echo "    C) Copy from External Drive"
echo "       - If you already have downloaded sounds"
echo "       - cp /path/to/sounds/*.mp3 $SOUNDS_DIR/"
echo ""

echo "[4] After Adding Sounds:"
echo ""
echo "    1. Copy production library config:"
echo "       cp $SOUNDS_DIR/sound_library_production.yaml $SOUNDS_DIR/sound_library.yaml"
echo ""
echo "    2. Verify all sounds:"
echo "       ffprobe $SOUNDS_DIR/*.mp3 -v error -show_entries format=duration"
echo ""
echo "    3. Generate test episode:"
echo "       source .venv/bin/activate"
echo "       python3 -c \"from verbatim_podcast import generate_verbatim_podcast_from_script; ...\""
echo ""

echo "[5] Sound Library Configuration:"
echo ""

# Check which config to use
if [ -f "$SOUNDS_DIR/sound_library.yaml" ]; then
    CURRENT_LIB="$(grep 'source:' "$SOUNDS_DIR/sound_library.yaml" | head -1 | cut -d'"' -f2)"
    if [[ "$CURRENT_LIB" == "Generated test sound" ]]; then
        echo "    Current: Test sounds (generated)"
        echo "    Target: Production sounds (Incompetech/Freesound)"
    else
        echo "    Current: $CURRENT_LIB"
    fi
fi

if [ -f "$SOUNDS_DIR/sound_library_production.yaml" ]; then
    echo "    Available: sound_library_production.yaml (ready to use)"
fi

echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "For production quality:"
echo "  1. Read: SOUND_DOWNLOADS_MANUAL.md"
echo "  2. Download sounds manually from sources"
echo "  3. Place in: sounds/ directory"
echo "  4. Run: cp sounds/sound_library_production.yaml sounds/sound_library.yaml"
echo "  5. Regenerate VERA episode"
echo ""
echo "For quick testing:"
echo "  1. Run: python3 generate_test_sounds.py"
echo "  2. This creates synthetic test sounds"
echo "  3. Good for verifying audio insertion works"
echo ""
echo "=========================================="
echo ""

# Auto-detect if we should use production config
if [ -f "$SOUNDS_DIR/sound_library_production.yaml" ]; then
    PROD_COUNT=$(grep "file:" "$SOUNDS_DIR/sound_library_production.yaml" | wc -l)
    echo "ℹ Production library config found ($PROD_COUNT sounds defined)"
fi

echo ""
echo "Status: Ready for manual sound downloads"
echo "=========================================="
