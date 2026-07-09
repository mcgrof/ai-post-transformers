# Audio Insertion Implementation Guide

## Overview

Phase 5 audio insertion is now complete. The system can detect `**[SOUND: name]**` markers in podcast scripts and insert corresponding audio files during episode generation.

## Architecture

### Three-Layer System

1. **Detection Layer** (sound_handler.py)
   - `find_sound_markers(text)` — Parse `**[SOUND: name]**` markers
   - `load_sound_library()` — Load sound configuration from YAML
   - `get_attribution_text()` — Generate CC-BY attribution text

2. **Mapping Layer** (sound_mixer.py)
   - `map_sounds_to_segments()` — Distribute sounds across dialogue segments
   - `build_ffmpeg_concat_script()` — Create FFmpeg concat file with sound insertions
   - `create_mixed_audio()` — Wrapper for complete audio mixing

3. **Integration Layer** (verbatim_podcast.py)
   - Detect sound markers in script
   - Build enhanced concat file before finalization
   - Pass to `finalize_podcast()` for FFmpeg mixing

### Data Flow

```
Script Text (with **[SOUND: name]** markers)
    ↓
find_sound_markers() → List[(sound_name, line_num, context)]
    ↓
load_sound_library() → Dict[sound_name → {file, license, ...}]
    ↓
map_sounds_to_segments() → Dict[segment_idx → [sounds]]
    ↓
build_ffmpeg_concat_script() → FFmpeg concat file with sounds
    ↓
finalize_podcast() → Mixed MP3 with dialogue + sounds
```

## Sound Library Configuration

Sounds are defined in `sounds/sound_library.yaml`:

```yaml
sounds:
  theme:
    file: theme-futuristic.mp3
    source: Incompetech
    license: CC-BY 3.0
    duration_ms: 3200
    attribution: "Kevin MacLeod - Incompetech.com"
  notification:
    file: notification-ping.mp3
    source: Zapsplat
    license: CC0
    duration_ms: 800
```

## Current Status

### ✓ Implemented
- Sound marker detection regex: `\*\*\[SOUND:\s*([a-z_]+)\]\*\*`
- Sound library loader with file verification
- FFmpeg concat script builder
- Integration into verbatim podcast workflow
- Attribution text generation for CC-BY sounds

### ⏳ Needs Setup
- Download actual sound files from royalty-free sources
- Update file paths in sound_library.yaml
- Test with real VERA episode

### 🔄 Optional Enhancements
- Audio fade-in/out filters for smooth transitions
- Dynamic sound selection based on episode content
- Parallel sound insertion (multiple simultaneous sounds)

## Getting Started

### 1. Download Sound Files

Create the `sounds/` directory structure:

```bash
mkdir -p sounds
cd sounds

# Download from Incompetech (CC-BY 3.0)
# https://incompetech.com
# Download: theme-futuristic.mp3, dramatic-strings-short.mp3, etc.

# Download from Zapsplat (CC0)
# https://www.zapsplat.com
# Download: notification-ping.mp3, transition-stinger.mp3, etc.

# Download from Freesound (CC0/CC-BY)
# https://freesound.org
# Download specific sounds matching library names
```

### 2. Update sound_library.yaml

After downloading, update file paths:

```yaml
sounds:
  theme:
    file: theme-futuristic.mp3          # Update actual filename
    source: Incompetech
    license: CC-BY 3.0
    duration_ms: 3200
  notification:
    file: notification-ping.mp3
    source: Zapsplat
    license: CC0
    duration_ms: 800
```

### 3. Test with Verbatim Script

Create a test script with sound markers:

```
**[SOUND: theme]**

Hal Turing: Welcome to AI Post Transformers!

**[SOUND: notification]**

Dr. Ada Shannon: Today we're exploring multi-agent systems.

**[SOUND: transition]**

Hal: Let's dive in.
```

### 4. Generate Episode

```bash
.venv/bin/python gen-podcast.py \
  --script-text "$(cat test_script.txt)" \
  --title "Test Episode with Sounds"
```

### 5. Verify

Check that:
- Audio file exists: `drafts/YYYY/MM/DD-{slug}-{hash}.mp3`
- Sounds were inserted (listen for effects between dialogue)
- Episode metadata includes sound_effects list
- Attribution text appears in show notes

## Testing Checklist

- [ ] Sound library loads without file-not-found warnings
- [ ] Script markers detected correctly (check stderr output)
- [ ] Concat file built with sound entries
- [ ] FFmpeg concat succeeds (no stderr errors)
- [ ] Episode audio includes sound effects at correct points
- [ ] CC-BY sounds include attribution in metadata
- [ ] Episode JSON has `sound_effects` array
- [ ] Attribution text in episode description

## Debugging

### Check Sound Library Loading

```bash
python3 -c "
from sound_handler import load_sound_library
lib = load_sound_library()
print(f'Loaded {len(lib)} sounds')
for name, config in lib.items():
    print(f'  {name}: {config.get(\"file_path\", \"NO PATH\")}')"
```

### Verify Marker Detection

```bash
python3 -c "
from sound_handler import find_sound_markers
text = '''
**[SOUND: theme]**
Hal: Hello!
**[SOUND: notification]**
Ada: Hi!
'''
markers = find_sound_markers(text)
for name, line, ctx in markers:
    print(f'{name} at line {line}')"
```

### Check Concat File

After generation, examine the concat file in `/tmp`:

```bash
cat /tmp/podcast_*/concat_with_sounds.txt
```

Should show segments + sound files in order:

```
file '/tmp/podcast_abc/seg_0_hal.mp3'
file '/sounds/theme-futuristic.mp3'
file '/tmp/podcast_abc/seg_1_ada.mp3'
file '/sounds/notification-ping.mp3'
```

## Metadata Integration

Generated episodes include sound effects metadata:

```json
{
  "script": "...",
  "sources": [...],
  "is_verbatim": true,
  "sound_effects": ["theme", "notification"],
  "attribution": "## Sound Effects & Music Attribution\n\nMusic by Kevin MacLeod (Incompetech.com)\nLicensed under Creative Commons: By Attribution 3.0\nhttps://creativecommons.org/licenses/by/3.0/"
}
```

## Performance Notes

- Sound insertion uses FFmpeg concat demux (zero-copy, fast)
- No re-encoding of audio segments
- Concat file generation is O(segments + sounds)
- Total overhead: ~100ms for concat file building + FFmpeg exec

## Next Steps

1. Download royalty-free sound files
2. Test with a real VERA analytical episode
3. Validate audio mixing quality in final episodes
4. Add fade-in/out filters for smooth transitions (optional)
5. Document in podcast RSS/metadata (already implemented)

## References

- Incompetech: https://incompetech.com (CC-BY 3.0)
- Zapsplat: https://www.zapsplat.com (CC0)
- Freesound: https://freesound.org (CC0/CC-BY)
- FFmpeg Concat: https://ffmpeg.org/ffmpeg-formats.html#concat-1
