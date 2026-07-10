#!/usr/bin/env python3
"""Lightweight test runner for SOUL.md atomic episodes.

No heavy ML dependencies — just script parsing, TTS, and audio.
"""

import sys
import argparse
from pathlib import Path
import yaml

# Only import what we need for verbatim generation
from verbatim_podcast import generate_verbatim_podcast_from_script


def load_config():
    """Load podcast config."""
    config_path = Path("config.yaml")
    if not config_path.exists():
        raise FileNotFoundError("config.yaml not found")
    with open(config_path) as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Generate SOUL.md test episodes without ML dependencies"
    )
    parser.add_argument("script", type=Path, help="Script file to generate")
    parser.add_argument(
        "--goal",
        default="Read verbatim test script",
        help="Generation goal/intent"
    )
    args = parser.parse_args()

    if not args.script.exists():
        print(f"Error: Script not found: {args.script}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.script.name}...", file=sys.stderr)
    with open(args.script) as f:
        script_text = f.read()

    config = load_config()

    print(f"Generating SOUL.md test episode...", file=sys.stderr)
    print(f"  Script: {args.script.name}", file=sys.stderr)
    print(f"  Goal: {args.goal}", file=sys.stderr)
    print(file=sys.stderr)

    try:
        result = generate_verbatim_podcast_from_script(
            script_text,
            config,
            title=None,  # Extract from script
            urls=[f"test:{args.script.name}"],
            goal=args.goal
        )

        if result:
            print(f"\n✓ Episode generated successfully", file=sys.stderr)
            print(f"  {result}", file=sys.stderr)
        else:
            print(f"✓ Episode generated", file=sys.stderr)

    except Exception as e:
        print(f"\n✗ Generation failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
