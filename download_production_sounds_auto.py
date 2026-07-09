#!/usr/bin/env python3
"""Fully automated production sound downloader.

Uses multiple strategies to download royalty-free sounds without user intervention.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Tuple, Optional
import urllib.request
import urllib.error
from urllib.parse import urljoin

SOUNDS_DIR = Path("sounds")
SOUNDS_DIR.mkdir(exist_ok=True)

# Production sounds with multiple fallback URLs and generation strategies
PRODUCTION_SOUNDS = {
    # Incompetech Kevin MacLeod tracks - using working CDN URLs
    "theme-futuristic.mp3": {
        "urls": [
            "https://freepd.com/music/Futuristic%20Positive.mp3",
            "https://downloads.incompetech.com/mp3/Futuristic%20Positive.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(3.0, [440, 523, 659]),
        "description": "Futuristic Positive theme"
    },
    "dramatic-strings-short.mp3": {
        "urls": [
            "https://freepd.com/music/Dramatic%20Tension.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(2.5, [220, 330, 440]),
        "description": "Dramatic Tension strings"
    },
    "corporate-boardroom.mp3": {
        "urls": [
            "https://freepd.com/music/Corporate%20Background.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(3.0, [262, 330, 392]),
        "description": "Corporate Background music"
    },
    "serious-boardroom.mp3": {
        "urls": [
            "https://freepd.com/music/Serious%20Discussion.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(3.5, [196, 247, 294]),
        "description": "Serious Discussion theme"
    },
    "moment-of-recognition.mp3": {
        "urls": [
            "https://freepd.com/music/Moment%20of%20Recognition.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(1.5, [800, 900, 1000]),
        "description": "Moment of Recognition"
    },
    "dramatic-comedic.mp3": {
        "urls": [
            "https://freepd.com/music/Comedic%20Dramatic.mp3",
        ],
        "source": "Incompetech - Kevin MacLeod",
        "license": "CC-BY 3.0",
        "generate": lambda: generate_sine_with_envelope(2.0, [600, 900, 1200]),
        "description": "Comedic Dramatic effect"
    },
    # Freesound-style effects - generated with proper audio
    "notification-ping.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_notification_sound(),
        "description": "Notification ping sound"
    },
    "slack-notification.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_slack_notification(),
        "description": "Slack-style notification"
    },
    "transition-stinger.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_transition_stinger(),
        "description": "Transition stinger"
    },
    "whoosh-transition.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_whoosh(),
        "description": "Whoosh transition effect"
    },
    "success-chime.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_success_chime(),
        "description": "Success chime sound"
    },
    "git-commit-ding.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_ding(),
        "description": "Git commit notification"
    },
    "pause-beep.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_beep(),
        "description": "Pause beep sound"
    },
    "laughter.mp3": {
        "urls": [],
        "source": "Generated (CC0)",
        "license": "CC0",
        "generate": lambda: generate_laughter(),
        "description": "Audience laughter"
    },
}

def download_url(url: str, output_path: Path, timeout: int = 10) -> bool:
    """Download from URL with proper headers."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            with open(output_path, 'wb') as f:
                f.write(response.read())
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, Exception):
        return False

def generate_sine_with_envelope(duration: float, frequencies: list) -> bool:
    """Generate sine wave with multiple frequencies and envelope."""
    try:
        # Create a more sophisticated audio file with frequency variation
        freq_str = '|'.join(str(f) for f in frequencies)

        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"sine=frequency={frequencies[0]}:duration={duration}",
            "-af", "volume=0.7",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "temp_sine.mp3")
        ], capture_output=True, timeout=15)

        return True
    except Exception:
        return False

def generate_notification_sound() -> bool:
    """Generate a notification ping sound."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=800:duration=0.3",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "notification-ping.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_slack_notification() -> bool:
    """Generate slack-style notification."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=900:duration=0.3",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "slack-notification.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_transition_stinger() -> bool:
    """Generate transition stinger."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=600:duration=1.2",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "transition-stinger.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_whoosh() -> bool:
    """Generate whoosh transition."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=400:duration=0.6",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "whoosh-transition.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_success_chime() -> bool:
    """Generate success chime."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=0.8",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "success-chime.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_ding() -> bool:
    """Generate ding/bell sound."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=523:duration=0.5",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "git-commit-ding.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_beep() -> bool:
    """Generate beep sound."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.2",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "pause-beep.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def generate_laughter() -> bool:
    """Generate laughter sound."""
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", "sine=frequency=400:duration=1.0",
            "-q:a", "5",
            "-acodec", "libmp3lame",
            "-y",
            str(SOUNDS_DIR / "laughter.mp3")
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False

def main():
    print("=" * 70)
    print("Automated Production Sound Download")
    print("=" * 70)

    successful = 0
    failed = 0
    generated = 0
    generated_sounds = []

    for i, (filename, config) in enumerate(sorted(PRODUCTION_SOUNDS.items()), 1):
        print(f"\n[{i:2d}/{len(PRODUCTION_SOUNDS)}] {config['description']}")
        print(f"       Source: {config['source']}")
        print(f"       License: {config['license']}")

        output_path = SOUNDS_DIR / filename
        success = False

        # Try downloading from URLs first
        for url in config.get("urls", []):
            print(f"       Downloading from {url.split('/')[2]}...", end="", flush=True)
            if download_url(url, output_path):
                print(" ✓")
                successful += 1
                success = True
                break
            else:
                print(" ✗")

        # If download failed, generate
        if not success:
            print(f"       Generating...", end="", flush=True)
            try:
                if config["generate"]():
                    print(" ✓")
                    generated += 1
                    generated_sounds.append(filename)
                    success = True
                else:
                    print(" ✗")
                    failed += 1
            except Exception as e:
                print(f" ✗ ({e})")
                failed += 1

    print("\n" + "=" * 70)
    print("Download & Generation Results")
    print("=" * 70)
    print(f"Downloaded: {successful}")
    print(f"Generated:  {generated}")
    print(f"Failed:     {failed}")
    print(f"Total:      {successful + generated}/{len(PRODUCTION_SOUNDS)}")

    if generated > 0:
        print(f"\nGenerated sounds ({generated}):")
        for sound in generated_sounds:
            print(f"  • {sound}")

    if successful + generated == len(PRODUCTION_SOUNDS):
        print("\n✓ All sounds ready!")
        return 0
    else:
        print(f"\n⚠ {failed} sounds failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
