#!/usr/bin/env python3
"""Minimal test: Vera whoosh sound (Kokoro TTS to save credits)."""

import sys
import yaml
from pathlib import Path
from verbatim_podcast import generate_verbatim_podcast_from_script

with open('config.yaml') as f:
    config = yaml.safe_load(f)

# Use Kokoro to save ElevenLabs credits
config['podcast']['tts_backend'] = 'kokoro'

script = Path('test_scripts/vera-whoosh-test.md').read_text()

try:
    print("[Test] Generating minimal Vera whoosh test (Kokoro TTS)...", file=sys.stderr)
    result = generate_verbatim_podcast_from_script(script, config)
    print("✓ Generated. You should hear:", file=sys.stderr)
    print("  1. Ada: 'So what are we supposed to do?'", file=sys.stderr)
    print("  2. WHOOSH sound effect", file=sys.stderr)
    print("  3. Vera: 'I can help with that.'", file=sys.stderr)
except Exception as e:
    print(f"✗ Failed: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
