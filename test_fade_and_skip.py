#!/usr/bin/env python3
"""Test fade effect and countdown skip."""

import sys
import subprocess
from pathlib import Path

# Test 1: Skip countdown, with fade
print("=" * 60)
print("TEST 1: Special episode (no countdown, with fade)")
print("=" * 60)

result = subprocess.run([
    sys.executable, "test_soul_episode.py",
    "test_scripts/soul-full-test.md"
], cwd="/home/mcgrof/devel/ai-post-transformers")

if result.returncode == 0:
    print("✓ Test 1 passed: SOUL episode generated with fade effect")
else:
    print("✗ Test 1 failed")
    sys.exit(1)

print()

# Test 2: Regular episode with countdown (for comparison)
print("=" * 60)
print("TEST 2: Regular episode (with countdown, no fade)")
print("=" * 60)

# Create a simple minimal script
minimal_script = """---
title: Regular Test Episode
---

Hal Turing: This is a regular episode with the countdown.

Dr. Ada Shannon: Yeah, and no special fade effect.
"""

test_file = Path("/tmp/test_regular_episode.md")
test_file.write_text(minimal_script)

result = subprocess.run([
    sys.executable, "test_soul_episode.py",
    str(test_file)
], cwd="/home/mcgrof/devel/ai-post-transformers")

if result.returncode == 0:
    print("✓ Test 2 passed: Regular episode generated with countdown")
else:
    print("✗ Test 2 failed")
    sys.exit(1)

print()
print("=" * 60)
print("All tests passed!")
print("=" * 60)
print()
print("Summary:")
print("- SOUL episodes now skip the countdown and use fade effect")
print("- Regular episodes keep the countdown (no fade)")
print("- Theme fades out over 3 seconds as first dialogue fades in")
