# Production Sound Library Setup Guide

Complete guide to upgrading from test sounds to production-quality royalty-free audio for AI Post Transformers podcast episodes.

## Current Status

### What We Have
✓ Audio insertion infrastructure fully implemented (Phase 5)
✓ Test sounds working (14 synthetic sounds via FFmpeg)
✓ Sound marker detection working (**[SOUND: name]**)
✓ FFmpeg concat integration working
✓ VERA episodes generating with sounds

### What We Need
Replace 14 test sounds with production-quality royalty-free audio from:
- **Incompetech** (Kevin MacLeod): 6 tracks - CC-BY 3.0 (requires attribution)
- **Freesound.org**: 8 sound effects - CC0 (no attribution needed)
- **Zapsplat** (optional backup): CC0 alternatives

## Production Sound Library

All 14 sounds are pre-configured in `sounds/sound_library_production.yaml`:

| Sound ID | File Name | Duration | Source | License | Use Case |
|---|---|---|---|---|---|
| `theme` | theme-futuristic.mp3 | ~15s | Incompetech | CC-BY 3.0 | Opening/closing theme |
| `notification` | notification-ping.mp3 | ~0.8s | Freesound | CC0 | Alert notifications |
| `slack_ping` | slack-notification.mp3 | ~0.6s | Freesound | CC0 | Slack-style ping |
| `transition` | transition-stinger.mp3 | ~1.5s | Freesound | CC0 | Between sections |
| `whoosh` | whoosh-transition.mp3 | ~1.0s | Freesound | CC0 | Transition effect |
| `success_chime` | success-chime.mp3 | ~1.2s | Freesound | CC0 | Positive moment |
| `git_commit` | git-commit-ding.mp3 | ~0.8s | Freesound | CC0 | Action/event marker |
| `pause` | pause-beep.mp3 | ~0.4s | Freesound | CC0 | Pause indicator |
| `dramatic_strings` | dramatic-strings-short.mp3 | ~8s | Incompetech | CC-BY 3.0 | VERA introduction |
| `boardroom` | corporate-boardroom.mp3 | ~10s | Incompetech | CC-BY 3.0 | Boardroom scene |
| `moment_of_recognition` | moment-of-recognition.mp3 | ~6s | Incompetech | CC-BY 3.0 | Key insight |
| `absurd_dramatic` | dramatic-comedic.mp3 | ~8s | Incompetech | CC-BY 3.0 | Comedic dramatic |
| `serious_boardroom` | serious-boardroom.mp3 | ~12s | Incompetech | CC-BY 3.0 | Serious analysis |
| `laughter` | laughter.mp3 | ~2s | Freesound | CC0 | Audience reaction |

## Download Instructions

### Quick Reference

**Total sounds needed:** 14
**Time to download:** ~20-30 minutes
**Total file size:** ~30-60 MB (high quality)

### Step 1: Download Incompetech Tracks (6 sounds)

Visit: https://incompetech.com/music/royalty-free/

Kevin MacLeod's catalog is completely free under CC-BY 3.0.

1. Search: **"Futuristic Positive"** → Save as `theme-futuristic.mp3`
2. Search: **"Dramatic Tension"** → Save as `dramatic-strings-short.mp3`
3. Search: **"Corporate Background"** → Save as `corporate-boardroom.mp3`
4. Search: **"Serious Discussion"** → Save as `serious-boardroom.mp3`
5. Search: **"Moment of Recognition"** → Save as `moment-of-recognition.mp3`
6. Search: **"Comedic Dramatic"** → Save as `dramatic-comedic.mp3`

**For each track:**
1. Click the track name to open details
2. Click "Free Music Download" button
3. Select MP3 format (160 kbps recommended)
4. Save with the exact filename from table above
5. Move to `sounds/` directory

**Attribution:** Automatically handled in `sound_library_production.yaml`

### Step 2: Download Freesound Effects (8 sounds)

Visit: https://freesound.org/

1. In search box, add filter: **License: CC0**
2. Search for each term below
3. Download highest-rated result as MP3
4. Rename to match table above
5. Move to `sounds/` directory

**Searches:**
- "notification ping" → `notification-ping.mp3`
- "slack notification" → `slack-notification.mp3`
- "transition stinger" → `transition-stinger.mp3`
- "whoosh transition" → `whoosh-transition.mp3`
- "success chime" → `success-chime.mp3`
- "notification bell" → `git-commit-ding.mp3`
- "pause beep" → `pause-beep.mp3`
- "audience laughter" → `laughter.mp3`

**For each sound:**
1. Click search result
2. Click "Download" button
3. Select MP3 format
4. Rename file to match table
5. Move to `sounds/` directory

**Attribution:** CC0 - no attribution needed

### Step 3: Organize Files

After downloading all 14 sounds:

```bash
# Verify all files are present
ls -1 sounds/*.mp3 | wc -l    # Should show 14

# Check file sizes (should be reasonable, not tiny)
ls -lh sounds/*.mp3 | awk '{print $5, $9}'

# Verify audio format
ffprobe sounds/*.mp3 -v error -show_entries format=duration
```

## Switch to Production Library

Once all 14 sounds are downloaded:

```bash
# Backup test sound config
cp sounds/sound_library.yaml sounds/sound_library_test.yaml

# Use production config with real sounds
cp sounds/sound_library_production.yaml sounds/sound_library.yaml

# Verify configuration
grep "source:" sounds/sound_library.yaml | sort | uniq -c
```

Expected output after switching:
```
6 Incompetech - Kevin MacLeod
8 Freesound.org
```

## Test Production Setup

Generate a VERA episode with production sounds:

```bash
source .venv/bin/activate

python3 -c "
from verbatim_podcast import generate_verbatim_podcast_from_script
import yaml

config = yaml.safe_load(open('config.yaml'))
script = open('/tmp/vera_test_episode.txt').read()

result = generate_verbatim_podcast_from_script(
    script,
    config,
    title='VERA Analytical: Production Sound Quality Test',
    urls=['https://arxiv.org/pdf/2406.00000']
)
print(f'✓ Episode generated with production sounds')
"
```

Listen to the generated MP3 and verify:
- Opening theme is Kevin MacLeod's "Futuristic Positive"
- Notification sounds are crisp and clear
- Dramatic strings sound professional
- Transitions are smooth
- All 13 sound markers play at the right times

## Verification Checklist

- [ ] All 14 sound files downloaded
- [ ] Files are in `sounds/` directory
- [ ] File sizes are reasonable (not <1KB)
- [ ] `ffprobe` shows proper duration for each file
- [ ] `sound_library_production.yaml` matches file names
- [ ] Copied production config: `cp sounds/sound_library_production.yaml sounds/sound_library.yaml`
- [ ] VERA episode generates successfully
- [ ] Audio includes real sounds (not test sine waves)
- [ ] Incompetech tracks sound professional
- [ ] Freesound effects are clear

## Troubleshooting

### "file 'theme-futuristic.mp3' not found" error

**Problem:** File doesn't exist or wrong filename
**Solution:**
1. Check file exists: `ls -l sounds/theme-futuristic.mp3`
2. Check spelling (case-sensitive on Linux)
3. Verify in `sound_library.yaml`: `grep "file: " sounds/sound_library.yaml`

### Generated episode has no sound effects

**Problem:** Old config still being used
**Solution:**
1. Verify current config: `head -5 sounds/sound_library.yaml`
2. Should show Incompetech/Freesound sources
3. If not, re-run: `cp sounds/sound_library_production.yaml sounds/sound_library.yaml`

### Audio quality is poor

**Problem:** Downloaded sounds were compressed
**Solution:**
- Freesound: Re-download, select "original" or "high quality" version
- Incompetech: Select 192 kbps or higher MP3
- Re-run VERA generation after replacing files

### Attribution error for Kevin MacLeod

**Problem:** Missing "Music by Kevin MacLeod" credit
**Solution:**
1. Verify `sounds/sound_library.yaml` has attribution text
2. Episode JSON should include in `attribution` field
3. Attribution automatically added to show notes

### Can't find specific Incompetech track

**Problem:** Track name doesn't match exactly
**Solution:**
1. Go to https://incompetech.com/music/royalty-free/
2. Search by keyword (not exact track name)
3. Browse results and pick best match
4. Update filename and `sound_library_production.yaml` if needed

## File Size Reference

After downloading production sounds, expect:

```
Incompetech (6 tracks):
  theme-futuristic.mp3           ~5-10 MB
  dramatic-strings-short.mp3     ~5-10 MB
  corporate-boardroom.mp3        ~5-10 MB
  serious-boardroom.mp3          ~5-10 MB
  moment-of-recognition.mp3      ~3-8 MB
  dramatic-comedic.mp3           ~3-8 MB
  Subtotal: ~26-56 MB

Freesound (8 effects):
  notification-*.mp3 (4 files)   ~0.2-1 MB each
  Other effects (4 files)        ~0.2-1 MB each
  Subtotal: ~2-8 MB

Total: ~28-64 MB
```

If files are much smaller (<100KB), they may be preview/low-quality versions. Re-download.

## Final Steps

After production sounds are set up:

1. Test multiple VERA episodes to ensure consistent quality
2. Listen to at least 2-3 generated episodes with production sounds
3. Verify attribution shows correctly in show notes
4. Confirm episode files upload to R2 correctly
5. Update RSS feed with sound metadata
6. Publish to podcast platform (Spotify, Apple Podcasts, etc.)

## Production Checklist

- [ ] All 14 production sounds downloaded and verified
- [ ] `sounds/sound_library.yaml` points to real audio files
- [ ] Test VERA episode generates with production sounds
- [ ] Audio quality verified (no artifacts, clear sound)
- [ ] Attribution metadata correct
- [ ] Episodes upload successfully
- [ ] RSS feed includes sound metadata
- [ ] Podcast apps display episodes correctly

## Next Production Episodes

Once production sounds are verified, generate special episodes:

```
**[SOUND: theme]**
Hal Turing: Welcome to a special VERA analytical episode!
**[SOUND: dramatic_strings]**
...
```

All sound effects will now be professional quality, providing:
- Professional production value
- Clear transitions between segments
- Engaging audio experience
- Proper licensing compliance

---

**Status:** Ready for manual sound downloads and production setup
**Infrastructure:** 100% complete and tested
**Next:** Download 14 production sounds, switch config, regenerate episodes
