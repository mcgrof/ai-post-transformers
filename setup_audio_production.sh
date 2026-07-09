#!/bin/bash
# One-command production audio setup - Fully Automated
# No manual downloads required. Everything happens automatically.

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  AI Post Transformers - Production Audio Setup (Fully Auto)   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Generate/Download sounds
echo "[1/4] Preparing production sounds..."
python3 download_production_sounds_auto.py > /tmp/audio_setup.log 2>&1
SOUND_COUNT=$(ls sounds/*.mp3 2>/dev/null | wc -l)
echo "      ✓ $SOUND_COUNT sounds ready"

# Step 2: Switch to production config
echo "[2/4] Switching to production configuration..."
if [ -f "sounds/sound_library_production.yaml" ]; then
    cp sounds/sound_library.yaml sounds/sound_library_test.yaml 2>/dev/null || true
    cp sounds/sound_library_production.yaml sounds/sound_library.yaml
    echo "      ✓ Production config activated"
else
    echo "      ⚠ Production config not found, keeping test config"
fi

# Step 3: Verify all sounds
echo "[3/4] Verifying audio files..."
for f in sounds/*.mp3; do
    if [ -f "$f" ]; then
        size=$(ls -lh "$f" | awk '{print $5}')
        echo "      ✓ $(basename $f) ($size)"
    fi
done

# Step 4: Show status
echo "[4/4] Final status..."
TOTAL=$(ls sounds/*.mp3 2>/dev/null | wc -l)
if [ "$TOTAL" -eq 14 ]; then
    echo "      ✓ All 14 sounds ready"
    echo ""
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                  ✨ SETUP COMPLETE ✨                          ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Your podcast now has production audio enabled!"
    echo ""
    echo "Next steps:"
    echo "  1. Generate a VERA episode:"
    echo "     source .venv/bin/activate"
    echo "     python3 -c 'from verbatim_podcast import generate_verbatim_podcast_from_script; ...'"
    echo ""
    echo "  2. Use sound markers in scripts:"
    echo "     **[SOUND: theme]**"
    echo "     Hal: Welcome!"
    echo "     **[SOUND: notification]**"
    echo ""
    echo "All 14 sounds are automatically inserted during generation."
    echo ""
else
    echo "      ⚠ Only $TOTAL/14 sounds found"
    echo "      Check /tmp/audio_setup.log for details"
    exit 1
fi
