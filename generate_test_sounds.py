#!/usr/bin/env python3
"""Generate test sound effects using ffmpeg for podcast production."""

import subprocess
import os
from pathlib import Path

SOUNDS_DIR = Path("sounds")
SOUNDS_DIR.mkdir(exist_ok=True)

# Define all sounds with their ffmpeg generation commands and durations
SOUNDS = {
    "theme-futuristic.mp3": {
        "duration": 3.0,
        "cmd": "sine=frequency=440:duration=3",
        "description": "Theme - Futuristic notes"
    },
    "notification-ping.mp3": {
        "duration": 0.4,
        "cmd": "sine=frequency=800:duration=0.4",
        "description": "Notification ping"
    },
    "slack-notification.mp3": {
        "duration": 0.3,
        "cmd": "sine=frequency=900:duration=0.3",
        "description": "Slack-style notification"
    },
    "transition-stinger.mp3": {
        "duration": 1.2,
        "cmd": "sine=frequency=600:duration=1.2",
        "description": "Transition stinger"
    },
    "whoosh-transition.mp3": {
        "duration": 0.6,
        "cmd": "sine=frequency=400:duration=0.6",
        "description": "Whoosh transition"
    },
    "success-chime.mp3": {
        "duration": 0.8,
        "cmd": "sine=frequency=1000:duration=0.8",
        "description": "Success chime"
    },
    "git-commit-ding.mp3": {
        "duration": 0.5,
        "cmd": "sine=frequency=523:duration=0.5",
        "description": "Git commit bell"
    },
    "pause-beep.mp3": {
        "duration": 0.2,
        "cmd": "sine=frequency=440:duration=0.2",
        "description": "Pause beep"
    },
    "dramatic-strings-short.mp3": {
        "duration": 2.5,
        "cmd": "sine=frequency=220:duration=2.5",
        "description": "Dramatic strings"
    },
    "corporate-boardroom.mp3": {
        "duration": 3.0,
        "cmd": "sine=frequency=262:duration=3",
        "description": "Corporate boardroom music"
    },
    "moment-of-recognition.mp3": {
        "duration": 1.5,
        "cmd": "sine=frequency=800:duration=1.5",
        "description": "Moment of recognition"
    },
    "dramatic-comedic.mp3": {
        "duration": 2.0,
        "cmd": "sine=frequency=600:duration=2",
        "description": "Dramatic comedic effect"
    },
    "serious-boardroom.mp3": {
        "duration": 3.5,
        "cmd": "sine=frequency=196:duration=3.5",
        "description": "Serious boardroom music"
    },
    "audience-laughter.mp3": {
        "duration": 1.0,
        "cmd": "sine=frequency=400:duration=1",
        "description": "Audience laughter effect"
    },
}

def generate_sound(filename, ffmpeg_filter, duration):
    """Generate a sound using ffmpeg."""
    output_path = SOUNDS_DIR / filename

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", ffmpeg_filter,
        "-q:a", "5",
        "-acodec", "libmp3lame",
        "-y",
        str(output_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return True, duration * 1000
        else:
            return False, 0
    except Exception as e:
        print(f"  Error: {e}")
        return False, 0

def main():
    print("=" * 70)
    print("Generating Test Sound Effects")
    print("=" * 70)

    generated = 0
    failed = 0

    for filename, config in sorted(SOUNDS.items()):
        print(f"\n[{generated + failed + 1:2d}/{len(SOUNDS)}] {config['description']}")
        print(f"       → {filename}")

        success, duration_ms = generate_sound(
            filename,
            config["cmd"],
            config["duration"]
        )

        if success:
            generated += 1
            print(f"       ✓ ({int(duration_ms)}ms)")
        else:
            failed += 1
            print(f"       ✗ Failed")

    print("\n" + "=" * 70)
    print(f"Generated: {generated}/{len(SOUNDS)} sounds")
    if failed > 0:
        print(f"Failed: {failed}")
    print("=" * 70)

    # List generated files
    if generated > 0:
        print("\nGenerated files:")
        for f in sorted(SOUNDS_DIR.glob("*.mp3")):
            size = f.stat().st_size
            print(f"  ✓ {f.name:35} ({size:,} bytes)")

    return failed == 0

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
