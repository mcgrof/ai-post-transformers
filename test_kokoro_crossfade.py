#!/usr/bin/env python3
"""Test crossfade with Kokoro TTS backend (saves ElevenLabs credits)."""

import sys
import yaml
from pathlib import Path
from verbatim_podcast import generate_verbatim_podcast_from_script

# Load config
with open('config.yaml') as f:
    config = yaml.safe_load(f)

# Force Kokoro backend
config['podcast']['tts_backend'] = 'kokoro'
print("[Test] Using Kokoro TTS backend to save ElevenLabs credits", file=sys.stderr)

# Read test script
script = Path('test_scripts/mini-fade-test.md').read_text()

# Generate podcast with Kokoro
try:
    result = generate_verbatim_podcast_from_script(script, config)
    print("\n✓ Kokoro crossfade test complete!")
    print("  You should hear:")
    print("  - Theme starts")
    print("  - ~1.5 seconds in: Hal's voice STARTS WHILE theme is playing")
    print("  - Theme fades out as Hal talks")
    print("  - Ada and Hal continue dialogue")
except Exception as e:
    print(f"✗ Test failed: {e}", file=sys.stderr)
    sys.exit(1)
