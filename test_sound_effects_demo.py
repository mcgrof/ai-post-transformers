#!/usr/bin/env python3
"""Minimal test: verify sound effects are audible in output."""

import sys
import yaml
from pathlib import Path
from verbatim_podcast import generate_verbatim_podcast_from_script

# Simple script with just dialogue and sound markers
test_script = """---
title: Sound Effects Test
---

Hal Turing: Test one.

**[SOUND: notification]**

Dr. Ada Shannon: Did you hear that?

**[SOUND: success_chime]**

Hal Turing: That should be the success chime.

**[SOUND: whoosh]**

Dr. Ada Shannon: And that's a whoosh.
"""

with open('config.yaml') as f:
    config = yaml.safe_load(f)

config['podcast']['tts_backend'] = 'kokoro'

try:
    print("[Test] Generating sound effects demo...", file=sys.stderr)
    result = generate_verbatim_podcast_from_script(test_script, config, title="Sound Effects Demo")
    print("✓ Demo generated. Listen for: notification → success_chime → whoosh", file=sys.stderr)
except Exception as e:
    print(f"✗ Failed: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
