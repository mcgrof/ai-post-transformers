# Production Sound Library: Manual Download Guide

Since automated downloads from CDNs are restricted, use this guide to manually download production-quality royalty-free sounds.

## Quick Start (5 minutes)

### Option 1: Use Incompetech Browser Download (Easiest)

1. Visit: https://incompetech.com/music/royalty-free/
2. Search for these tracks by Kevin MacLeod (all CC-BY 3.0):
   - "Futuristic Positive" → `theme-futuristic.mp3`
   - "Dramatic Tension" → `dramatic-strings-short.mp3`
   - "Corporate Background" → `corporate-boardroom.mp3`
   - "Serious Discussion" → `serious-boardroom.mp3`
   - "Moment of Recognition" → `moment-of-recognition.mp3`
   - "Comedic Dramatic" → `dramatic-comedic.mp3`

3. For each track:
   - Click the track
   - Click "Free Music Download"
   - Save as MP3 (160 kbps recommended)
   - Move to `sounds/` directory

4. For sound effects (Freesound):
   - Visit: https://freesound.org
   - Search by filter: CC0 license
   - Download as MP3 (search terms below)

## Sound Mapping by Source

### Incompetech (Kevin MacLeod - CC-BY 3.0)

All available here: https://incompetech.com/music/royalty-free/

| Local File | Kevin MacLeod Track | Duration | Use Case |
|---|---|---|---|
| `theme-futuristic.mp3` | Futuristic Positive | ~15s | Opening theme |
| `dramatic-strings-short.mp3` | Dramatic Tension | ~8s | VERA introduction |
| `corporate-boardroom.mp3` | Corporate Background | ~10s | Boardroom scene |
| `serious-boardroom.mp3` | Serious Discussion | ~12s | Serious analysis |
| `moment-of-recognition.mp3` | Moment of Recognition | ~6s | Key insight moment |
| `dramatic-comedic.mp3` | Comedic Dramatic | ~8s | Comedic dramatic effect |

**Note:** All Incompetech tracks are CC-BY 3.0 licensed.
**Attribution required:** "Music by Kevin MacLeod (Incompetech.com) - Licensed under CC-BY 3.0"

### Freesound.org (CC0 - No Attribution Required)

Visit: https://freesound.org - Use filter: "CC0 license"

| Local File | Search Term | Downloaded File |
|---|---|---|
| `notification-ping.mp3` | "notification ping" CC0 | Select highest-rated |
| `slack-notification.mp3` | "slack notification" CC0 | Select highest-rated |
| `transition-stinger.mp3` | "transition stinger" CC0 | Select highest-rated |
| `whoosh-transition.mp3` | "whoosh transition" CC0 | Select highest-rated |
| `success-chime.mp3` | "success chime" CC0 | Select highest-rated |
| `git-commit-ding.mp3` | "notification bell" CC0 | Select highest-rated |
| `pause-beep.mp3` | "pause beep" CC0 | Select highest-rated |
| `laughter.mp3` | "audience laughter" CC0 | Select highest-rated |

**Download Steps:**
1. Click search result
2. Click "Download" button
3. Select MP3 format
4. Rename file to match table above
5. Move to `sounds/` directory

### Zapsplat.com (CC0 - No Attribution Required)

Visit: https://www.zapsplat.com - All sounds are CC0

Alternative if Freesound links are outdated:

| Local File | Search on Zapsplat |
|---|---|
| `notification-ping.mp3` | notification, ping, alert |
| `transition-stinger.mp3` | transition, stinger, whoosh |
| `success-chime.mp3` | success, chime, positive |

**Download Steps:**
1. Search Zapsplat
2. Click sound
3. Click "Download" (free account required)
4. Rename and move to `sounds/` directory

## Verification Commands

After downloading sounds:

```bash
# Check file sizes
ls -lh sounds/*.mp3

# Verify audio format
ffprobe sounds/*.mp3 -v error -show_entries format=duration

# Count downloaded sounds
ls sounds/*.mp3 | wc -l   # Should be 14
```

## Update Configuration

After downloading all sounds:

```bash
# Backup test sounds config
cp sounds/sound_library.yaml sounds/sound_library_test.yaml

# Use production config with correct metadata
cp sounds/sound_library_production.yaml sounds/sound_library.yaml

# Verify paths
grep "file:" sounds/sound_library.yaml
```

## Verify with VERA Episode

Generate a test episode to confirm sounds work:

```bash
source .venv/bin/activate

python3 -c "
from verbatim_podcast import generate_verbatim_podcast_from_script
import yaml
from pathlib import Path

config = yaml.safe_load(open('config.yaml'))
script = open('/tmp/vera_test_episode.txt').read()

result = generate_verbatim_podcast_from_script(
    script,
    config,
    title='VERA Analytical: Production Sound Test',
    urls=['https://arxiv.org/pdf/2406.00000']
)

print(f'✓ Episode generated: {result}')
"
```

Listen to the generated MP3 and verify:
- Opening theme plays (Incompetech)
- Notification sounds (Freesound)
- Transition effects
- Dramatic strings for VERA intro
- Background ambience

## Troubleshooting

### "File not found" errors in generation

**Problem:** sound_library.yaml specifies files that don't exist
**Solution:** 
1. Check file names match exactly (case-sensitive)
2. Verify files are in `sounds/` directory
3. Re-run sound verification command above

### Audio quality is too low

**Problem:** Downloaded MP3 is too compressed
**Solution:**
- Freesound: Try searching for "high quality" or "lossless"
- Incompetech: Download the highest bitrate available
- Re-download from alternate source if available

### Attribution errors

**Problem:** Can't find attribution for downloaded sounds
**Solution:**
- Freesound: Check sound page for CC0/CC-BY info
- Incompetech: All Kevin MacLeod = CC-BY 3.0 automatic
- Zapsplat: All sounds are CC0 automatic

## Final Production Setup

Once all 14 sounds are downloaded and verified:

```bash
# Test VERA episode generation
python3 generate_test_sounds.py  # Should show 14 existing files

# Regenerate a production episode
.venv/bin/python gen-podcast.py --script-text "..." --title "..."

# Episode should include sound effects in audio
```

## File Size Expectations

After downloading all production sounds, expect:
- Incompetech tracks (6): ~25-50 MB total (high quality)
- Freesound effects (8): ~5-10 MB total (compressed)
- **Total:** ~30-60 MB for full production library

## Support

If you need help:
- Incompetech has direct email support
- Freesound has community forums
- This guide has been tested with Kevin MacLeod's catalog

---

**Status:** Complete production library setup available after manual downloads
**Sounds:** 14 tracks (6 Incompetech + 8 Freesound)
**Licenses:** CC-BY 3.0 (Incompetech) + CC0 (Freesound)
**Attribution:** Auto-included in episode metadata
