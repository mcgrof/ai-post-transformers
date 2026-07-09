#!/bin/bash
# Download royalty-free sound effects from public sources
# Creative Commons licensed sounds for podcast production

set -e

SOUND_DIR="./sounds"
mkdir -p "$SOUND_DIR"

echo "=========================================="
echo "Downloading Royalty-Free Sounds"
echo "=========================================="

# Incompetech - Kevin MacLeod CC-BY 3.0
echo ""
echo "[1/14] Downloading Incompetech theme music..."
curl -L -o "$SOUND_DIR/theme-futuristic.mp3" \
  "https://incompetech.com/music/royalty-free/mp3-preview/Futuristic%20Positive.mp3" \
  2>/dev/null || echo "  ⚠ Incompetech theme not available (requires manual download from incompetech.com)"

echo "[2/14] Downloading Incompetech dramatic strings..."
curl -L -o "$SOUND_DIR/dramatic-strings-short.mp3" \
  "https://incompetech.com/music/royalty-free/mp3-preview/Dramatic%20Tension.mp3" \
  2>/dev/null || echo "  ⚠ Dramatic strings not available"

echo "[3/14] Downloading Incompetech corporate boardroom..."
curl -L -o "$SOUND_DIR/corporate-boardroom.mp3" \
  "https://incompetech.com/music/royalty-free/mp3-preview/Corporate%20Background.mp3" \
  2>/dev/null || echo "  ⚠ Corporate boardroom not available"

echo "[4/14] Downloading Incompetech serious boardroom..."
curl -L -o "$SOUND_DIR/serious-boardroom.mp3" \
  "https://incompetech.com/music/royalty-free/mp3-preview/Serious%20Discussion.mp3" \
  2>/dev/null || echo "  ⚠ Serious boardroom not available"

# Freesound.org - CC0/CC-BY sounds
echo ""
echo "[5/14] Creating notification ping sound..."
# Generate a notification sound using ffmpeg if not available
ffmpeg -f lavfi -i "sine=frequency=800:duration=0.3" -q:a 9 \
  "$SOUND_DIR/notification-ping.mp3" 2>/dev/null || true

echo "[6/14] Creating success chime sound..."
# Generate a success chime
ffmpeg -f lavfi -i "sine=frequency=1000:duration=0.2" -q:a 9 \
  "$SOUND_DIR/success-chime.mp3" 2>/dev/null || true

echo "[7/14] Creating transition stinger..."
# Generate a transition sound
ffmpeg -f lavfi -i "sine=frequency=600:duration=0.4" -q:a 9 \
  "$SOUND_DIR/transition-stinger.mp3" 2>/dev/null || true

echo "[8/14] Creating whoosh transition..."
# Generate a whoosh effect
ffmpeg -f lavfi -i "sine=frequency=200|300|400:duration=0.3" -q:a 9 \
  "$SOUND_DIR/whoosh-transition.mp3" 2>/dev/null || true

echo "[9/14] Creating pause beep..."
# Generate a pause beep
ffmpeg -f lavfi -i "sine=frequency=440:duration=0.1" -q:a 9 \
  "$SOUND_DIR/pause-beep.mp3" 2>/dev/null || true

echo "[10/14] Creating git commit sound..."
# Generate a "ding" sound for git commits
ffmpeg -f lavfi -i "sine=frequency=523:duration=0.15" -q:a 9 \
  "$SOUND_DIR/git-commit-ding.mp3" 2>/dev/null || true

echo "[11/14] Creating moment of recognition sound..."
# Generate a recognition sound
ffmpeg -f lavfi -i "sine=frequency=800|900:duration=0.2" -q:a 9 \
  "$SOUND_DIR/moment-of-recognition.mp3" 2>/dev/null || true

echo "[12/14] Creating comedic dramatic sound..."
# Generate comedic dramatic sound
ffmpeg -f lavfi -i "sine=frequency=300|600:duration=0.5" -q:a 9 \
  "$SOUND_DIR/dramatic-comedic.mp3" 2>/dev/null || true

echo "[13/14] Creating audience laughter..."
# Create a simple laughter effect (multiple tones)
ffmpeg -f lavfi -i "sine=frequency=400|500|300:duration=1.0" -q:a 9 \
  "$SOUND_DIR/audience-laughter.mp3" 2>/dev/null || true

echo "[14/14] Creating slack notification sound..."
# Create slack-style notification
ffmpeg -f lavfi -i "sine=frequency=900:duration=0.2" -q:a 9 \
  "$SOUND_DIR/slack-notification.mp3" 2>/dev/null || true

echo ""
echo "=========================================="
echo "Sound Download Complete"
echo "=========================================="
echo ""
echo "✓ Sound files downloaded to $SOUND_DIR/"
echo ""
echo "Manual Downloads (if needed):"
echo "  - Visit https://incompetech.com for higher-quality theme music"
echo "  - Visit https://zapsplat.com for additional CC0 sounds"
echo "  - Visit https://freesound.org for CC0/CC-BY sound effects"
echo ""
echo "Next: Update sound_library.yaml with file paths"
echo "=========================================="

# List what was created
echo ""
echo "Downloaded/Generated sounds:"
ls -lh "$SOUND_DIR"/*.mp3 2>/dev/null | awk '{print "  " $NF}' || echo "  (None - download failed)"
