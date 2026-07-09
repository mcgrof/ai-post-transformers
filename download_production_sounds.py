#!/usr/bin/env python3
"""Download production-quality royalty-free sounds for podcast.

Sources:
  - Incompetech (Kevin MacLeod): CC-BY 3.0
  - Freesound.org: CC0/CC-BY
  - Zapsplat: CC0
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Tuple, Optional

SOUNDS_DIR = Path("sounds")
SOUNDS_DIR.mkdir(exist_ok=True)

# Production sound definitions with download sources
# Format: {filename: (url, source, license, description)}
PRODUCTION_SOUNDS = {
    # Incompetech Kevin MacLeod (CC-BY 3.0)
    "theme-futuristic.mp3": (
        "https://incompetech.com/music/royalty-free/music/Futuristic%20Positive.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Futuristic Positive theme"
    ),
    "dramatic-strings-short.mp3": (
        "https://incompetech.com/music/royalty-free/music/Dramatic%20Tension.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Dramatic Tension strings"
    ),
    "corporate-boardroom.mp3": (
        "https://incompetech.com/music/royalty-free/music/Corporate%20Background.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Corporate Background music"
    ),
    "serious-boardroom.mp3": (
        "https://incompetech.com/music/royalty-free/music/Serious%20Discussion.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Serious Discussion theme"
    ),
    "moment-of-recognition.mp3": (
        "https://incompetech.com/music/royalty-free/music/Moment%20of%20Recognition.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Moment of Recognition"
    ),
    "dramatic-comedic.mp3": (
        "https://incompetech.com/music/royalty-free/music/Comedic%20Dramatic.mp3",
        "Incompetech - Kevin MacLeod",
        "CC-BY 3.0",
        "Comedic Dramatic effect"
    ),
    # Freesound.org CC0 sounds (example IDs - these may vary)
    "notification-ping.mp3": (
        "https://freesound.org/data/previews/536/536857_11718139-lq.mp3",
        "Freesound.org",
        "CC0",
        "Notification ping sound"
    ),
    "slack-notification.mp3": (
        "https://freesound.org/data/previews/510/510971_4842129-lq.mp3",
        "Freesound.org",
        "CC0",
        "Slack-style notification"
    ),
    "transition-stinger.mp3": (
        "https://freesound.org/data/previews/549/549196_8678387-lq.mp3",
        "Freesound.org",
        "CC0",
        "Transition stinger"
    ),
    "whoosh-transition.mp3": (
        "https://freesound.org/data/previews/500/500127_9315209-lq.mp3",
        "Freesound.org",
        "CC0",
        "Whoosh transition effect"
    ),
    "success-chime.mp3": (
        "https://freesound.org/data/previews/516/516995_3194099-lq.mp3",
        "Freesound.org",
        "CC0",
        "Success chime sound"
    ),
    "git-commit-ding.mp3": (
        "https://freesound.org/data/previews/511/511884_8286761-lq.mp3",
        "Freesound.org",
        "CC0",
        "Notification ding"
    ),
    "pause-beep.mp3": (
        "https://freesound.org/data/previews/476/476140_1765233-lq.mp3",
        "Freesound.org",
        "CC0",
        "Pause beep sound"
    ),
    "laughter.mp3": (
        "https://freesound.org/data/previews/548/548055_11718139-lq.mp3",
        "Freesound.org",
        "CC0",
        "Audience laughter"
    ),
}

def download_sound(filename: str, url: str, max_retries: int = 3) -> Tuple[bool, Optional[int]]:
    """Download a sound file with retry logic.

    Args:
        filename: Target filename
        url: Source URL
        max_retries: Number of retry attempts

    Returns:
        (success, file_size) tuple
    """
    output_path = SOUNDS_DIR / filename

    # Use curl with timeout and retries
    cmd = [
        "curl",
        "-L",  # Follow redirects
        "-f",  # Fail on HTTP errors
        "--connect-timeout", "10",
        "--max-time", "30",
        "-o", str(output_path),
        "-C", "-",  # Resume if partial
        url
    ]

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=35
            )

            if result.returncode == 0 and output_path.exists():
                size = output_path.stat().st_size
                return True, size

            # Delete partial files
            if output_path.exists():
                output_path.unlink()

            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"      Retry in {wait}s...", end="", flush=True)
                time.sleep(wait)

        except subprocess.TimeoutExpired:
            if output_path.exists():
                output_path.unlink()
            continue

    return False, None

def verify_audio_file(filepath: Path) -> Tuple[bool, Optional[float]]:
    """Verify audio file with ffprobe.

    Returns:
        (is_valid, duration_seconds)
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1:noescapevalues=1",
                str(filepath)
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            return True, duration

    except Exception:
        pass

    return False, None

def main():
    print("=" * 70)
    print("Downloading Production-Quality Royalty-Free Sounds")
    print("=" * 70)

    successful = 0
    failed = 0
    failed_sounds = {}

    for i, (filename, (url, source, license_type, description)) in enumerate(
        sorted(PRODUCTION_SOUNDS.items()), 1
    ):
        print(f"\n[{i:2d}/{len(PRODUCTION_SOUNDS)}] {description}")
        print(f"       Source: {source}")
        print(f"       License: {license_type}")
        print(f"       Downloading...", end="", flush=True)

        success, size = download_sound(filename, url)

        if success:
            is_valid, duration = verify_audio_file(SOUNDS_DIR / filename)
            if is_valid and duration and duration > 0.1:
                print(f" ✓ ({size:,} bytes, {duration:.1f}s)")
                successful += 1
            else:
                print(f" ✗ (invalid audio format)")
                (SOUNDS_DIR / filename).unlink()
                failed += 1
                failed_sounds[filename] = "Invalid audio format"
        else:
            print(f" ✗ (download failed)")
            failed += 1
            failed_sounds[filename] = "Download failed"

    print("\n" + "=" * 70)
    print(f"Download Results: {successful}/{len(PRODUCTION_SOUNDS)} succeeded")
    print("=" * 70)

    if successful > 0:
        print(f"\n✓ Successfully downloaded {successful} sounds")

    if failed > 0:
        print(f"\n⚠ Failed to download {failed} sounds:")
        for filename, reason in failed_sounds.items():
            print(f"  - {filename}: {reason}")

        print("\n📝 Next Steps:")
        print("  1. Download sounds manually from sources:")
        print("     - Incompetech: https://incompetech.com")
        print("     - Freesound: https://freesound.org")
        print("     - Zapsplat: https://www.zapsplat.com")
        print("  2. Place files in sounds/ directory")
        print("  3. Update sound_library.yaml with correct metadata")

    # List what's available
    print("\n" + "=" * 70)
    print("Available Sound Files")
    print("=" * 70)

    existing = sorted(SOUNDS_DIR.glob("*.mp3"))
    if existing:
        for f in existing:
            size = f.stat().st_size
            is_valid, duration = verify_audio_file(f)
            status = "✓" if is_valid else "✗"
            duration_str = f"{duration:.1f}s" if duration else "unknown"
            print(f"  {status} {f.name:40} {size:>10,} bytes ({duration_str})")

    print("\n" + "=" * 70)
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
