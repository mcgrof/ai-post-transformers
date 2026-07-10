#!/usr/bin/env python3
"""Test full SOUL.md intro with crossfade (Kokoro TTS)."""

import sys
import yaml
from pathlib import Path
from verbatim_podcast import generate_verbatim_podcast_from_script

# Load config
with open('config.yaml') as f:
    config = yaml.safe_load(f)

# Force Kokoro backend
config['podcast']['tts_backend'] = 'kokoro'
print("[Test] Generating SOUL.md intro with Kokoro TTS", file=sys.stderr)

# Read full SOUL script
script = Path('test_scripts/soul-full-test.md').read_text()

# Generate podcast with Kokoro
try:
    result = generate_verbatim_podcast_from_script(script, config)
    print("\n✓ SOUL.md intro test complete!", file=sys.stderr)
    print("  Listen to the full episode intro with:", file=sys.stderr)
    print("  - Theme plays softly (35% volume)", file=sys.stderr)
    print("  - Hal's opening dialogue clear and prominent", file=sys.stderr)
    print("  - All dialogue levels consistent", file=sys.stderr)
except Exception as e:
    print(f"✗ Test failed: {e}", file=sys.stderr)
    sys.exit(1)
