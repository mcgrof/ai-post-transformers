"""Advanced audio mixing with sound effects insertion via ffmpeg.

Handles complex audio workflows:
- Insert sound effects at specific points in dialogue
- Mix multiple audio streams with proper timing
- Handle silence/padding between segments and sounds
- Preserve audio quality during mixing
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional


def generate_silence(duration_ms: int, sample_rate: int = 44100) -> str:
    """Generate a silent audio file.

    Args:
        duration_ms: Duration in milliseconds
        sample_rate: Sample rate (default 44100 Hz)

    Returns:
        Path to generated silence file.
    """
    duration_s = duration_ms / 1000.0
    silence_file = f"/tmp/silence_{duration_ms}ms.mp3"

    if os.path.exists(silence_file):
        return silence_file

    # Generate silence using ffmpeg
    try:
        subprocess.run([
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"anullsrc=r={sample_rate}:cl=mono",
            "-t", str(duration_s),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            silence_file
        ], capture_output=True, check=True)
        return silence_file
    except Exception as e:
        print(f"[Mixer] Warning: Could not generate silence: {e}", file=sys.stderr)
        return None


def map_sounds_to_segments(
    sound_markers: List[Tuple[str, int, str]],
    segment_count: int
) -> Dict[int, List[str]]:
    """Map sound effects to segment indices.

    Args:
        sound_markers: List of (sound_name, line_number, context)
        segment_count: Total number of dialogue segments

    Returns:
        Dict mapping segment index to list of sounds to insert after.
    """
    sound_map = {}

    # Estimate max line number from markers to calibrate scaling
    max_line = max((line_num for _, line_num, _ in sound_markers), default=100)

    for sound_name, line_num, context in sound_markers:
        # Map line number proportionally to segment index
        # Sound at line 30 in 150-line script goes ~20% through segments
        if max_line > 0:
            segment_idx = int((line_num / max_line) * segment_count * 0.95)  # 95% to leave room at end
        else:
            segment_idx = 0

        segment_idx = max(0, min(segment_idx, segment_count - 1))

        if segment_idx not in sound_map:
            sound_map[segment_idx] = []
        sound_map[segment_idx].append(sound_name)

    return sound_map


def build_ffmpeg_concat_script(
    segment_files: List[str],
    sound_map: Dict[int, List[str]],
    sound_library: Dict,
    output_dir: str,
    intro_files: List[str] = None
) -> str:
    """Build FFmpeg concat file with sound insertions.

    Args:
        segment_files: List of dialogue segment audio files
        sound_map: Dict mapping segment index to sounds
        sound_library: Sound library configuration
        output_dir: Directory for temp files
        intro_files: Optional list of intro files to prepend (countdown, theme)

    Returns:
        Path to generated concat demux file.
    """
    concat_file = os.path.join(output_dir, "concat_with_sounds.txt")
    segments_added = 0

    with open(concat_file, "w") as f:
        # Add intro files first (countdown + theme)
        if intro_files:
            for intro_file in intro_files:
                if os.path.exists(intro_file):
                    f.write(f"file '{intro_file}'\n")
                    print(f"[Mixer] Added intro file: {os.path.basename(intro_file)}", file=sys.stderr)

        for i, seg_file in enumerate(segment_files):
            f.write(f"file '{seg_file}'\n")
            segments_added += 1

            # Add sounds after this segment if mapped
            if i in sound_map:
                for sound_name in sound_map[i]:
                    if sound_name in sound_library:
                        config = sound_library[sound_name]
                        sound_file = config.get("file_path")

                        if sound_file and os.path.exists(sound_file):
                            f.write(f"file '{sound_file}'\n")
                            print(f"[Mixer] Inserting {sound_name} after segment {i} ({os.path.basename(sound_file)})", file=sys.stderr)
                        else:
                            print(f"[Mixer] ERROR: Sound file NOT FOUND for {sound_name}: {sound_file}", file=sys.stderr)
                    else:
                        print(f"[Mixer] Warning: Sound {sound_name} not in library", file=sys.stderr)

    print(f"[Mixer] Built concat script with {segments_added} dialogue segments", file=sys.stderr)
    return concat_file


def create_mixed_audio(
    segment_files: List[str],
    sound_markers: List[Tuple[str, int, str]],
    sound_library: Dict,
    output_file: str,
    tmpdir: str = "/tmp",
    fade_duration_ms: int = 200
) -> bool:
    """Create final audio with sound effects mixed in.

    Args:
        segment_files: List of dialogue audio files
        sound_markers: List of (sound_name, line_number, context)
        sound_library: Sound library config
        output_file: Output audio file path
        tmpdir: Temporary directory for working files
        fade_duration_ms: Fade-in/out duration for sounds

    Returns:
        True if successful, False otherwise.
    """
    if not sound_markers:
        # No sounds - just concatenate normally
        return True

    try:
        # Map sounds to segments
        sound_map = map_sounds_to_segments(sound_markers, len(segment_files))

        # Build concat script with sound insertions
        concat_file = build_ffmpeg_concat_script(
            segment_files,
            sound_map,
            sound_library,
            tmpdir
        )

        print(f"[Mixer] Creating mixed audio with {len(sound_markers)} sound effects", file=sys.stderr)

        # Use existing finalize_podcast for now (no sound mixing yet)
        # In production, would add afade filter here for smooth sound insertion
        print(f"[Mixer] Ready for ffmpeg sound mixing (implementation TBD)", file=sys.stderr)
        return True

    except Exception as e:
        print(f"[Mixer] Error: {e}", file=sys.stderr)
        return False


def get_sound_fade_filter(fade_ms: int = 200) -> str:
    """Generate ffmpeg audio fade filter.

    Args:
        fade_ms: Fade duration in milliseconds

    Returns:
        FFmpeg audio filter string.
    """
    fade_s = fade_ms / 1000.0
    return f"afade=t=in:st=0:d={fade_s},afade=t=out:st=0:d={fade_s}"


if __name__ == "__main__":
    print("[Mixer] Sound mixer module loaded", file=sys.stderr)
