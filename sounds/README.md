# Sound Effects Library

This directory contains configuration and audio files for podcast sound effects and music.

## Setup

### 1. Configure in Script

In your verbatim podcast script, add sound markers:

```
**[SOUND: theme]**

Hal Turing: Welcome to the show!

**[SOUND: notification]**

Dr. Ada Shannon: Hello!
```

### 2. Download Sound Files

Download from the sources listed in `sound_library.yaml`:

**Free/CC0 Sounds (no attribution needed):**
- Freesound.org (search for sound name, filter by CC0)
- Zapsplat (all free, CC0 license)

**CC-BY Licensed (attribution required in show notes):**
- Incompetech (Kevin MacLeod)
  - Site: https://incompetech.com
  - Download directly or search their library

### 3. Place Files

Copy downloaded files to this directory with names matching `sound_library.yaml`:

```
sounds/
  theme-futuristic.mp3
  notification-ping.mp3
  transition-stinger.mp3
  success-chime.mp3
  git-commit-ding.mp3
  dramatic-strings-short.mp3
  corporate-boardroom.mp3
  ...
```

### 4. Update sound_library.yaml

If you use different files or sources, update the mappings in `sound_library.yaml`.

## Available Sounds

See `sound_library.yaml` for the complete list. Common ones:

| Name | Use | License |
|------|-----|---------|
| theme | Opening/closing music | CC-BY 3.0 |
| notification | Slack-style ping | CC0 |
| transition | Between sections | CC0 |
| success_chime | Positive moment | CC0 |
| git_commit | Git action | CC0 |
| dramatic_strings | VERA introduction | CC-BY 3.0 |
| boardroom | Tribunal/meeting scene | CC-BY 3.0 |
| laughter | Audience response | CC0 |

## Usage in Podcast

The `sound_handler.py` module:
1. Loads the sound library
2. Detects all `**[SOUND: name]**` markers in the script
3. Tracks which sounds are used
4. Generates attribution text for show notes
5. Includes attribution in episode metadata

## Attribution

When publishing episodes with sounds:

1. **CC-BY sounds** (Incompetech/Kevin MacLeod):
   - Include in show notes or episode description
   - Link to: https://creativecommons.org/licenses/by/3.0/

2. **CC0 sounds**:
   - No attribution required, but optional to credit

Example show note:

> **Sound Credits**
> 
> Music by Kevin MacLeod (Incompetech.com)
> Licensed under Creative Commons: By Attribution 3.0
> https://creativecommons.org/licenses/by/3.0/

## Future: Audio Insertion

When audio insertion is implemented, the system will:
1. Parse `**[SOUND: name]**` markers
2. Insert corresponding audio files during concatenation
3. Handle silence/padding between segments
4. Auto-include attribution in show notes

For now, markers are detected and tracked in episode metadata.
