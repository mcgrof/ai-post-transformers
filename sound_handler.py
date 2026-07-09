"""Sound effect handler for verbatim podcasts.

Loads sound library and handles insertion of audio effects at marked points.
Maps **[SOUND: name]** markers in scripts to audio files.
"""

import os
import re
import sys
import yaml
from pathlib import Path


def load_sound_library():
    """Load sound library configuration.

    Returns dict mapping sound names to metadata + file paths.
    """
    lib_path = Path(__file__).parent / "sounds" / "sound_library.yaml"

    if not lib_path.exists():
        print(f"[Sound] Warning: sound library not found at {lib_path}", file=sys.stderr)
        return {}

    try:
        with open(lib_path) as f:
            data = yaml.safe_load(f)
        sounds = data.get("sounds", {})

        # Verify files exist and add full paths
        for name, config in sounds.items():
            file_name = config.get("file")
            if file_name:
                file_path = Path(__file__).parent / "sounds" / file_name
                config["file_path"] = str(file_path)
                if not file_path.exists():
                    print(f"[Sound] Warning: {name} file not found: {file_path}", file=sys.stderr)

        print(f"[Sound] Loaded {len(sounds)} sound effects from library", file=sys.stderr)
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
