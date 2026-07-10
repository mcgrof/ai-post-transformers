"""Sound effect handler for verbatim podcasts.

Loads sound library and handles insertion of audio effects at marked points.
Maps **[SOUND: name]** markers in scripts to audio files.
"""

import os
import re
import sys
import yaml
from pathlib import Path


def load_sound_library(library_name="gemini_library.yaml", theme_variant="short"):
    """Load sound library configuration.

    Args:
        library_name: Which library to load (gemini_library.yaml or sound_library.yaml)
        theme_variant: Which theme version to use ("short" or "full")

    Returns dict mapping sound names to metadata + file paths.
    """
    lib_path = Path(__file__).parent / "sounds" / library_name

    # Fallback to default library if specified one doesn't exist
    if not lib_path.exists():
        lib_path = Path(__file__).parent / "sounds" / "sound_library.yaml"
        print(f"[Sound] Using fallback library: {lib_path}", file=sys.stderr)

    if not lib_path.exists():
        print(f"[Sound] Warning: sound library not found", file=sys.stderr)
        return {}

    try:
        with open(lib_path) as f:
            data = yaml.safe_load(f)
        sounds = data.get("sounds", {})

        # Verify files exist and resolve paths (handle ~ expansion)
        for name, config in sounds.items():
            file_name = config.get("file")
            if file_name:
                # Check if this sound has theme variants
                if name == "theme" and config.get("variants"):
                    # Select theme variant
                    variant_file = config["variants"].get(theme_variant)
                    if variant_file:
                        file_name = variant_file
                        config["file"] = file_name
                        # Update duration based on variant
                        if theme_variant == "full":
                            config["duration_ms"] = 10000  # Full theme is 10s
                        else:
                            config["duration_ms"] = 5000   # Short theme is 5s
                        print(f"[Sound] Using theme variant: {theme_variant} ({config['duration_ms']}ms)", file=sys.stderr)

                # Resolve file path (relative to sounds/ or absolute)
                file_path = Path(__file__).parent / "sounds" / file_name

                config["file_path"] = str(file_path)
                if not file_path.exists():
                    print(f"[Sound] Warning: {name} file not found: {file_path}", file=sys.stderr)

        print(f"[Sound] Loaded {len(sounds)} sound effects from {library_name}", file=sys.stderr)
        return sounds

    except Exception as e:
        print(f"[Sound] Error loading library: {e}", file=sys.stderr)
        return {}


def find_sound_markers(text):
    """Find all **[SOUND: name]** markers in text.

    Returns list of (name, line_number).
    """
    markers = []
    lines = text.split('\n')

    for i, line in enumerate(lines):
        # Match **[SOUND: soundname]**
        match = re.search(r'\*\*\[SOUND:\s*([a-z_]+)\]\*\*', line, re.IGNORECASE)
        if match:
            sound_name = match.group(1).lower()
            markers.append((sound_name, i, line))

    return markers


def get_sound_file(sound_name, sound_library):
    """Get file path for a sound effect.

    Args:
        sound_name: Name of sound (e.g., 'notification', 'theme')
        sound_library: Loaded sound library dict

    Returns:
        File path (str) or None if not found.
    """
    if sound_name not in sound_library:
        print(f"[Sound] Warning: sound '{sound_name}' not in library", file=sys.stderr)
        return None

    config = sound_library[sound_name]
    file_path = config.get("file_path")

    if not file_path or not Path(file_path).exists():
        print(f"[Sound] Warning: sound file not found: {file_path}", file=sys.stderr)
        return None

    return file_path


def get_attribution_text(sound_library, sounds_used):
    """Build attribution text for used sounds.

    Args:
        sound_library: Loaded sound library
        sounds_used: List of sound names that were used

    Returns:
        Attribution text for show notes.
    """
    cc_by_sounds = []
    cc0_sources = set()

    for sound_name in sounds_used:
        if sound_name not in sound_library:
            continue

        config = sound_library[sound_name]
        license_type = config.get("license", "")
        attribution = config.get("attribution", "")
        source = config.get("source", "")

        if "CC-BY" in license_type and attribution:
            cc_by_sounds.append(attribution)
        elif "CC0" in license_type and source:
            cc0_sources.add(source)

    # Remove duplicates
    cc_by_sounds = list(set(cc_by_sounds))
    cc0_sources = list(sorted(cc0_sources))

    text = "## Sound Effects & Music Attribution\n\n"

    if cc_by_sounds:
        text += "**Licensed under Creative Commons: By Attribution 3.0**\n"
        for attr in cc_by_sounds:
            text += f"- {attr}\n"
        text += "\nhttps://creativecommons.org/licenses/by/3.0/\n\n"

    if cc0_sources:
        text += "**Public Domain / CC0 Sounds**\n"
        for source in cc0_sources:
            text += f"- {source}\n"

    return text


if __name__ == "__main__":
    # Test
    lib = load_sound_library()
    print(f"Loaded {len(lib)} sounds")

    test_text = """
    **[SOUND: theme]**

    Hal Turing: Hello!

    **[SOUND: notification]**

    Dr. Ada Shannon: Hi there!
    """

    markers = find_sound_markers(test_text)
    print(f"Found {len(markers)} sound markers:")
    for name, line, text in markers:
        print(f"  {name} at line {line}: {text}")
