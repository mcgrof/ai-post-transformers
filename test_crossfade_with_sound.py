#!/usr/bin/env python3
"""Test: Crossfade (Hal + theme) WITH sound effect (Kokoro TTS)."""

import sys
import yaml
from pathlib import Path
from verbatim_podcast import generate_verbatim_podcast_from_script

with open('config.yaml') as f:
    config = yaml.safe_load(f)

# Use Kokoro to save ElevenLabs credits
config['podcast']['tts_backend'] = 'kokoro'

script = Path('test_scripts/crossfade-with-sound-test.md').read_text()

try:
    print("[Test] Generating crossfade + sound test (Kokoro TTS)...", file=sys.stderr)
    result = generate_verbatim_podcast_from_script(script, config)
    print("✓ Generated. You should hear:", file=sys.stderr)
    print("  1. Theme song fades in", file=sys.stderr)
    print("  2. Hal: 'Are you hearing this Ada!?' (overlaid at ~35% volume with theme)", file=sys.stderr)
    print("  3. Ada: 'Yes I am hearing this, it is pretty good.'", file=sys.stderr)
    print("  4. Hal: 'We have a theme song now!'", file=sys.stderr)
    print("  5. WHOOSH sound effect", file=sys.stderr)
    print("  6. Vera: 'I can help with that.'", file=sys.stderr)
except Exception as e:
    print(f"✗ Failed: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
