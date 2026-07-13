"""Verbatim podcast generation: perform pre-written scripts as-is without analysis.

When a script is submitted (detected by dialogue structure), bypass all LLM
analysis passes and feed the dialogue directly to TTS.

This handles:
- SOUL.md or Severance: theatrical scripts with Hal, Ada, VERA
- Special instructions with full transcripts
- Any pre-written two-host dialogue

Detection: if text contains "A:", "B:", "Hal:", "Ada:", or similar speaker markers,
treat it as a verbatim script.
"""

import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from db import (
    get_connection, init_db,
    insert_podcast, update_podcast, link_podcast_paper, get_episode_count,
    add_covered_topics,
)
from draft_revisions import detect_revision, assign_revision
from elevenlabs_client import finalize_podcast, save_transcript, generate_srt, cleanup_podcast_tmpdir
from image_gen import generate_episode_image
from local_cover import render_title_cover
from rss import generate_feed
from sound_handler import load_sound_library, find_sound_markers, get_attribution_text
from sound_inserter import log_sound_timeline, insert_sounds_into_audio
from sound_mixer import build_ffmpeg_concat_script, map_sounds_to_segments
from script_parser import ScriptParser, render_tts_manifest, audit_parse

import tempfile
import time
import os
import subprocess
import yaml
from elevenlabs_client import tts_segment, sweep_stale_podcast_tmp, get_llm_backend


def _load_host_soul_profiles():
    """Load SOUL.md personality profiles for all hosts.

    Returns dict mapping host_name → personality version and traits.
    Used for TTS pronunciation hints and dialogue attribution.
    """
    profiles = {}
    hosts = ["Hal", "Ada", "VERA"]

    for host in hosts:
        soul_path = Path(__file__).parent / "hosts" / host.lower() / "SOUL.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text()
                # Extract personality_version from YAML frontmatter
                if "personality_version:" in content:
                    match = re.search(r'personality_version:\s*([0-9.]+[a-z\-]*)', content)
                    if match:
                        profiles[host] = {
                            "version": match.group(1),
                            "soul_file": str(soul_path),
                            "exists": True
                        }
                        print(f"[Verbatim] Loaded {host} SOUL v{profiles[host]['version']}", file=sys.stderr)
            except Exception as e:
                print(f"[Verbatim] Warning: Could not load {host} SOUL.md: {e}", file=sys.stderr)

    return profiles


def _is_verbatim_script(text):
    """Detect if text is a pre-written script vs paper content.

    Returns True if text has dialogue markers like:
    - A: / B:
    - Hal: / Ada: / VERA:
    - **A:** / **B:** / **HAL:** / **ADA:** / **VERA:** (markdown bold)
    - Host: / Guest:
    - [Stage directions]
    """
    # Look for speaker markers in the first 2000 chars
    sample = text[:2000]
    patterns = [
        r'^[AB]:\s+',  # A: or B:
        r'^\*\*[AB]\*\*:\s+',  # **A**: or **B**:
        r'^\*\*.*:\*\*',  # **SPEAKER (optional notes):** (markdown bold format)
        r'^(Hal|Ada|VERA|HOST|OVERLORD|CLAUDE|PRO|CODEX|LUIS)(\s+\([^)]*\))?:\s+',  # Plain speaker with optional (notes):
        r'^\[(MUSIC|SOUND|PAUSE|NOTIFICATION|DRAMATIC)',  # [Stage direction]
    ]

    lines = sample.split('\n')
    marker_count = 0
    for line in lines:
        stripped = line.strip()
        for pattern in patterns:
            if re.match(pattern, stripped, re.IGNORECASE):
                marker_count += 1
                break

    # If >10% of lines are speaker markers, it's a script
    return marker_count > len(lines) * 0.1


def _clean_dialogue_text(text):
    """Remove ALL metadata, stage directions, cues from dialogue.

    Aggressively strips anything that shouldn't be spoken:
    - [SOUND: X], [MUSIC: X], [SOUND MONTAGE ...], etc.
    - **[SOUND: X]**, **[MUSIC]**, etc.
    - Stage directions in any format
    - Metadata markers
    - Blockquotes

    This prevents metadata from being vocalized in the audio.
    """
    # First pass: remove lines that are ONLY metadata/stage directions
    lines = text.split('\n')
    filtered = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Skip entire lines that are only metadata
        if re.match(r'^-{3,}$', stripped):  # --- alone
            continue
        if re.match(r'^#{1,}\s+', stripped):  # # Header alone
            continue
        if re.match(r'^\[[A-Z]', stripped) and stripped.endswith(']'):  # [SOUND: ...], [MUSIC], etc
            continue
        if re.match(r'^\*\*\[', stripped):  # **[STAGE]**
            continue
        if re.match(r'^\*\*[A-Z_]+:\*\*$', stripped):  # **METADATA:**
            continue
        if re.match(r'^>', stripped):  # > blockquote
            continue

        filtered.append(line)

    # Second pass: remove inline metadata from remaining lines
    text = '\n'.join(filtered)

    # Remove all inline sound/music/stage markers
    text = re.sub(r'\s*\*\*\[SOUND[^\]]*\]\*\*\s*', ' ', text)
    text = re.sub(r'\s*\*\*\[MUSIC[^\]]*\]\*\*\s*', ' ', text)
    text = re.sub(r'\s*\*\*\[[A-Z_][^\]]*\]\*\*\s*', ' ', text)
    text = re.sub(r'\s*\[SOUND[^\]]*\]\s*', ' ', text)
    text = re.sub(r'\s*\[MUSIC[^\]]*\]\s*', ' ', text)
    text = re.sub(r'\s*\[PAUSE\]\s*', ' ', text)
    text = re.sub(r'\s*\[SILENCE\]\s*', ' ', text)
    text = re.sub(r'\s*\[BEAT\]\s*', ' ', text)
    text = re.sub(r'\s*\[NOTIFICATION[^\]]*\]\s*', ' ', text)

    # Remove markdown bold/italic formatting
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)

    # Clean up excess whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def _extract_script_segments_via_ast(text):
    """Extract dialogue segments using proper AST parser.

    Preserves theatrical structure (acts, scenes, cues) in the AST,
    extracts only dialogue nodes for TTS, and validates parsing completeness.

    Returns:
        tuple: (segments_list, audit_dict)
        segments_list: [{speaker, text}, ...]
        audit_dict: {raw_lines, nodes, dialogue_nodes, acts, scenes, sounds, ...}
    """
    # Speaker name to A/B mapping
    speaker_canon_map = {
        'Hal Turing': 'HAL', 'Hal': 'HAL',
        'Dr. Ada Shannon': 'ADA', 'Ada Shannon': 'ADA', 'Ada': 'ADA',
        # VERA is NOT a speaker; she's a concept Hal & Ada discuss
        # If VERA appears, she should not be voiced (skip her dialogue lines)
    }

    # Parse using AST parser
    parser = ScriptParser(speaker_map=speaker_canon_map)
    nodes = parser.parse(text)

    # Audit for completeness
    audit = audit_parse(nodes, text)

    # Hard fail if parsed content is suspiciously small
    if audit.dialogue_nodes < 2:
        raise ValueError(
            f"Parsed only {audit.dialogue_nodes} dialogue nodes. "
            f"Script likely truncated or invalid. Check parsing."
        )

    # Convert dialogue nodes to segment format for compatibility
    segments = []
    voice_map = {'HAL': 'A', 'ADA': 'B', 'VERA': 'C'}  # HAL → voice A, ADA → voice B, VERA → voice C

    for node in nodes:
        if node.type.value == 'dialogue' and node.spoken and node.text:
            speaker_voice = voice_map.get(node.canonical_speaker, 'A')
            segments.append({
                'speaker': speaker_voice,
                'text': node.text,
                'is_narration': False,
                'canonical_speaker': node.canonical_speaker,
            })

    return segments, {
        'raw_lines': audit.raw_nonblank_lines,
        'nodes_total': audit.nodes_total,
        'dialogue_nodes': audit.dialogue_nodes,
        'act_nodes': audit.act_nodes,
        'scene_nodes': audit.scene_nodes,
        'sound_nodes': audit.sound_nodes,
        'music_nodes': audit.music_nodes,
        'spoken_words': audit.spoken_words,
        'estimated_duration_seconds': audit.estimated_duration_seconds,
    }


def _extract_script_segments(text):
    """Parse verbatim script into segments for TTS.

    Handles various formats:
    - A: text
    - **A:** text
    - Hal: text
    - [Speaker]: text
    - [SOUND: ...] → skip
    - [MUSIC] → skip
    - Narrative text with speakers embedded

    Returns list of {speaker, text, is_narration, line_number} dicts.
    Line numbers track the original script line for sound mapping.
    """
    segments = []
    lines = text.split('\n')

    # Skip YAML frontmatter (--- ... ---)
    start_idx = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                start_idx = i + 1
                break

    # Keep track of absolute line numbers (including frontmatter)
    absolute_line_offset = start_idx
    lines = lines[start_idx:]

    # Map speaker names to A/B/C (only podcast personas, no personal names)
    # VERA is not a speaker; she's a concept Hal & Ada discuss
    # All keys are lowercase for case-insensitive matching
    speaker_map = {
        'hal': 'A', 'hal turing': 'A',
        'ada': 'B', 'dr. ada shannon': 'B', 'dr ada shannon': 'B',
        'vera': 'C', 'vera.': 'C',  # Third host - must map to C
        'overlord': 'A', 'overlord memo': 'A', 'overlord voice': 'A',
        'claude': 'A',
        'pro': 'B', 'chatgpt pro': 'B',
        'codex': 'A',
        'host': 'A', 'narrator': 'A', 'narrator/host intro': 'A',
        'guest': 'B',
        'a': 'A', 'b': 'B', 'c': 'C',
    }

    current_speaker = None
    current_text = []
    current_line_number = None

    for line_idx, line in enumerate(lines, start=absolute_line_offset + 1):
        line = line.rstrip()

        # Strip leading metadata markers from line for processing
        # e.g., "**[SOUND: theme]** Hal: text" → "Hal: text"
        processed_line = re.sub(r'^\s*\*\*?\[.*?\]\*\*?\s+', '', line)
        processed_line = re.sub(r'^\s*\[.*?\]\s+', '', processed_line)

        # Skip pure standalone sound/music directions (only the marker, no speaker after)
        if re.match(r'^\[(SOUND|MUSIC|SOUND:|MUSIC:)', line, re.IGNORECASE) and processed_line == line:
            if current_text and current_speaker:
                text_str = '\n'.join(current_text).strip()
                if text_str:
                    segments.append({
                        'speaker': current_speaker,
                        'text': text_str,
                        'is_narration': False,
                        'line_number': current_line_number or line_idx
                    })
            current_speaker = None
            current_text = []
            continue

        # Skip other standalone stage directions
        if re.match(r'^\[.*?\]$', line):
            if current_text and current_speaker:
                text_str = '\n'.join(current_text).strip()
                if text_str:
                    segments.append({
                        'speaker': current_speaker,
                        'text': text_str,
                        'is_narration': False,
                        'line_number': current_line_number or line_idx
                    })
            current_speaker = None
            current_text = []
            continue

        # Try to match speaker labels on the processed line (after stripping leading metadata)
        # Format: **SPEAKER_NAME (optional notes):** where ** surrounds everything including colon
        match = re.match(r'^\*\*(.+?):\*\*$', processed_line)
        if match:
            # Extract speaker name (might have parenthetical notes or continuation phrases)
            content = match.group(1).strip()
            # Remove parenthetical notes to get speaker name
            speaker_name = re.sub(r'\s*\([^)]*\)$', '', content).strip()
            # Remove continuation phrases like "continues", "presents", "outlines", "reads", etc.
            speaker_name = re.sub(r'\s+(continues|presents|outlines|reads|reads from).*$', '', speaker_name).strip()
            text = ""
        else:
            # Try plain format: SPEAKER: text (on processed line without leading metadata)
            match = re.match(r'^([A-Z][A-Za-z\s\.]*?):\s+(.+)$', processed_line)
            if match:
                speaker_name = match.group(1).strip()
                text = match.group(2).strip()
            else:
                speaker_name = None
                text = ""

        if speaker_name:
            # Map to A/B/C - try case-insensitive match
            speaker_key = speaker_name.strip().strip("*_`").casefold()
            speaker = speaker_map.get(speaker_key, None)

            # If no match, try without possessive 's
            if not speaker and "'s" in speaker_key:
                speaker = speaker_map.get(speaker_key.replace("'s", ""), None)

            # If still no match, raise error instead of defaulting to A
            if not speaker:
                print(f"[Parser] WARNING: Unknown speaker at line {line_idx}: {speaker_name!r}", file=sys.stderr)
                # Don't default to A - skip this line
                continue

            if speaker:
                # Flush previous speaker
                if current_text and current_speaker:
                    text_str = '\n'.join(current_text).strip()
                    text_str = _clean_dialogue_text(text_str)  # Remove metadata
                    if text_str:
                        segments.append({
                            'speaker': current_speaker,
                            'text': text_str,
                            'is_narration': False,
                            'line_number': current_line_number or line_idx
                        })

                current_speaker = speaker
                current_text = [text] if text else []
                current_line_number = line_idx
                continue

        # Continuation of current speaker
        if current_speaker and line.strip():
            current_text.append(line)
        elif not current_speaker and line.strip() and not line.startswith('#'):
            # Skip non-dialogue lines: metadata, bold formatting, stage directions
            stripped = line.strip()

            # Skip if line is or contains bold metadata/stage directions:
            # - **Date:** ..., **Runtime:** ..., **Format:** ..., etc.
            # - **[SOUND]**, **[MUSIC]**, etc. (bold stage directions)
            # - **HOST INTRO:** etc.
            # - Lines starting with ** and containing : (metadata) or [] (stage directions)
            if re.match(r'^\*\*', stripped):
                # Line starts with bold formatting
                if ':' in stripped or '[' in stripped or '---' in stripped:
                    # Looks like metadata, label, or stage direction - skip it
                    if current_text and current_speaker:
                        text_str = '\n'.join(current_text).strip()
                        if text_str:
                            segments.append({
                                'speaker': current_speaker,
                                'text': text_str,
                                'is_narration': False
                            })
                    current_speaker = None
                    current_text = []
                    continue

            # Standalone narration → treat as speaker A
            if current_text and current_speaker:
                text_str = '\n'.join(current_text).strip()
                if text_str:
                    segments.append({
                        'speaker': current_speaker,
                        'text': text_str,
                        'is_narration': False,
                        'line_number': current_line_number or line_idx
                    })
            current_speaker = 'A'
            current_text = [line]
            current_line_number = line_idx

    # Flush final segment
    if current_text and current_speaker:
        text_str = '\n'.join(current_text).strip()
        text_str = _clean_dialogue_text(text_str)  # Remove metadata
        if text_str:
            segments.append({
                'speaker': current_speaker,
                'text': text_str,
                'is_narration': False,
                'line_number': current_line_number or len(lines)
            })

    return segments


def _generate_title_from_script(text):
    """Extract or infer a title from script content.

    Look for title patterns:
    - YAML frontmatter title: field
    - Lines starting with # (markdown heading)
    - First sentence before dialogue
    - Default to "Special Episode"
    """
    lines = text.split('\n')

    # Check YAML frontmatter first
    if lines and lines[0].strip() == '---':
        # Parse YAML block
        for i in range(1, len(lines)):
            line = lines[i].strip()
            if line == '---':
                break  # End of frontmatter
            if line.startswith('title:'):
                title = line.replace('title:', '').strip()
                title = re.sub(r'^["""]|["""]$', '', title)  # Strip quotes
                if title:
                    return title

    # Look for markdown heading
    for line in lines:
        if line.startswith('#'):
            title = line.lstrip('#').strip()
            title = re.sub(r'^["""]|["""]$', '', title)  # Strip quotes
            if title:
                return title

        # All caps line (often titles)
        if line.isupper() and len(line) > 10 and len(line.split()) > 1:
            return line.strip()

    # Default
    return "Special Episode"


def render_soul_intro(body_audio_path, theme_path, output_path, tmpdir, theme_duration_s=10):
    """Mix theme with body on theatrical timeline (ChatGPT Pro approved).

    Uses single FFmpeg pass with filter_complex:
    - Theme: plays for full duration with fade-in, fades to quiet as dialogue starts
    - Body: starts at 2s (delayed), overlaps theme
    - Output: theatrical bed with theme + voice overlap

    Args:
        body_audio_path: path to full episode body (dialogue + SFX, no theme)
        theme_path: path to theme.mp3
        output_path: where to write final mixed audio
        tmpdir: temp directory for intermediate files
        theme_duration_s: duration of theme in seconds (5 for short, 10 for full)
    """
    import subprocess

    if not Path(theme_path).exists() or not Path(body_audio_path).exists():
        print(f"[Theater] Warning: theme or body missing, skipping mix", file=sys.stderr)
        return body_audio_path

    print(f"[Theater] Mixing theme + body on theatrical timeline (theme_duration_s={theme_duration_s})...", file=sys.stderr)
    print(f"[Theater] Theme file: {theme_path} ({Path(theme_path).stat().st_size} bytes)", file=sys.stderr)

    # Theme mixing: scales based on duration
    # 10s theme: intro 0-2s, background 2-9s, fade 9-10s
    # 20s theme: intro 0-4s, background 4-18s, fade 18-20s
    # Dialogue delay scales proportionally: 2s for 10s theme, 4s for 20s theme
    intro_end = 2 * (theme_duration_s / 10)  # 2s for 10s, 4s for 20s
    bg_fade_start = theme_duration_s - (1 * (theme_duration_s / 10))  # 1s before end, scaled
    dialogue_delay_ms = int(intro_end * 1000)

    filter_complex = (
        "[0:a]"
        "aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=0:{theme_duration_s},"
        "asetpts=PTS-STARTPTS,"
        "afade=t=in:st=0:d=0.8,"
        f"volume=if(lt(t\\,{intro_end})\\,1\\,if(lt(t\\,{bg_fade_start})\\,0.25\\,(1-((t-{bg_fade_start})/1))*0.25)):eval=frame"
        "[theme];"

        "[1:a]"
        "aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        "silenceremove=start_periods=1:start_threshold=-45dB:start_duration=0.05,"
        "asetpts=PTS-STARTPTS,"
        f"adelay={dialogue_delay_ms}|{dialogue_delay_ms}"
        "[voice];"

        "[theme][voice]"
        "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95:level=0"
        "[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(theme_path),
        "-i", str(body_audio_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-q:a", "2",
        str(output_path)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[Theater] ✗ FFmpeg failed (code {result.returncode})", file=sys.stderr)
            print(f"[Theater] {result.stderr[:300]}", file=sys.stderr)
            return body_audio_path

        if Path(output_path).exists():
            size = Path(output_path).stat().st_size
            duration = 0
            try:
                import subprocess as sp
                p = sp.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(output_path)],
                    capture_output=True, text=True, timeout=10
                )
                duration = float(p.stdout.strip()) if p.stdout.strip() else 0
            except:
                pass
            print(f"[Theater] ✓ Mixed: {size} bytes, ~{duration:.1f}s", file=sys.stderr)
            return output_path
        else:
            print(f"[Theater] ✗ Output file not created", file=sys.stderr)
            return body_audio_path
    except Exception as e:
        print(f"[Theater] ✗ Error: {e}", file=sys.stderr)
        return body_audio_path


def create_verbatim_podcast(script_text, config, soul_profiles=None, skip_countdown=False, theme_fade_duration_ms=3000, skip_theme=False):
    """Create podcast from verbatim script without LLM analysis.

    Returns (tmpdir, list_file, segments, sources, script) similar to create_podcast.

    Args:
        script_text: verbatim script with dialogue markers
        config: podcast configuration
        soul_profiles: dict of host SOUL.md profiles (for logging)
        skip_countdown: if True, omit countdown intro
        skip_theme: if True, do NOT add theme to intro (for separate theatrical mixing)
    """
    el_config = config.get("elevenlabs", {})

    # Read from nested config structure
    voices = el_config.get("voices", {})
    voice_a = voices.get("host", {}).get("voice_id") or el_config.get("voice_a", "oTOJ3soGzir2ldiaDSNs")  # Hal
    voice_b = voices.get("guest", {}).get("voice_id") or el_config.get("voice_b", "HBQuDIqftrmAQQAHSWnF")  # Ada
    voice_c = voices.get("third_host", {}).get("voice_id") or el_config.get("voice_c", None)  # Vera

    # Validate voice configuration
    if not voice_a:
        raise ValueError("voice_a (Hal) not configured")
    if not voice_b:
        raise ValueError("voice_b (Ada) not configured")
    if not voice_c:
        raise ValueError("voice_c (Vera) not configured - cannot render third host without a voice")
    if voice_c == voice_a:
        raise ValueError(f"voice_c ({voice_c}) equals voice_a - Vera would use Hal's voice")
    if voice_c == voice_b:
        raise ValueError(f"voice_c ({voice_c}) equals voice_b - Vera would use Ada's voice")

    voices = el_config.get("voices", {})
    if voices:
        host = voices.get("host", {})
        guest = voices.get("guest", {})
        third = voices.get("third_host", {})
        if host.get("voice_id"):
            voice_a = host["voice_id"]
        if guest.get("voice_id"):
            voice_b = guest["voice_id"]
        if third.get("voice_id"):
            voice_c = third["voice_id"]

    # Log loaded host personalities
    if soul_profiles:
        print(f"[Podcast] Host profiles loaded: {', '.join(f'{h} v{soul_profiles[h]['version']}' for h in soul_profiles)}", file=sys.stderr)

    print("[Podcast] Parsing verbatim script segments...", file=sys.stderr)
    # Use legacy parser (AST parser has issues with narration detection)
    segments = _extract_script_segments(script_text)
    print(f"[Podcast] Extracted {len(segments)} dialogue segments", file=sys.stderr)

    if not segments:
        raise ValueError("No dialogue segments found in script")

    # Prepare for TTS
    sweep_stale_podcast_tmp()
    tmpdir = tempfile.mkdtemp(prefix="podcast_")
    segment_files = []

    # Countdown intro (optional - can skip for special episodes)
    intro_audio_files = []

    if not skip_countdown:
        print("[Podcast] Generating countdown intro...", file=sys.stderr)
        tts_segment("three", voice_a, os.path.join(tmpdir, "countdown_3.mp3"), config)
        tts_segment("two", voice_b, os.path.join(tmpdir, "countdown_2.mp3"))
        tts_segment("one", voice_a, os.path.join(tmpdir, "countdown_1.mp3"))
        time.sleep(0.3)

        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", "0.3", "-c:a", "libmp3lame", "-q:a", "2",
             os.path.join(tmpdir, "short_pause.mp3")],
            capture_output=True, text=True
        )

        intro_audio_files = [
            os.path.join(tmpdir, "countdown_3.mp3"),
            os.path.join(tmpdir, "short_pause.mp3"),
            os.path.join(tmpdir, "countdown_2.mp3"),
            os.path.join(tmpdir, "short_pause.mp3"),
            os.path.join(tmpdir, "countdown_1.mp3"),
            os.path.join(tmpdir, "short_pause.mp3"),
        ]
    else:
        print("[Podcast] Skipping countdown intro", file=sys.stderr)

    # Add theme song to intro (skip if doing separate theatrical mixing)
    if not skip_theme:
        theme_path = Path(__file__).parent / "sounds" / "theme-full.mp3"

        if theme_path.exists():
            # Apply fade-out to theme and mark for crossfade with first dialogue
            intro_audio_files.append(str(theme_path))
            if not skip_countdown:
                intro_audio_files.append(os.path.join(tmpdir, "short_pause.mp3"))
            print("[Podcast] Added theme song to intro (will fade with dialogue)", file=sys.stderr)
    else:
        print("[Podcast] Skipping theme in intro (will mix separately for theatrical)", file=sys.stderr)

    # Generate TTS for script segments
    print("[Podcast] Generating TTS for script segments...", file=sys.stderr)
    print(f"[Podcast] Voice config: A={voice_a[:8]}... B={voice_b[:8]}... C={voice_c[:8]}...", file=sys.stderr)

    for i, seg in enumerate(segments):
        # Map speaker to voice - strict routing
        speaker_code = seg["speaker"]
        if speaker_code == "A":
            voice = voice_a
            speaker_label = "Hal"
        elif speaker_code == "B":
            voice = voice_b
            speaker_label = "Ada"
        elif speaker_code == "C":
            voice = voice_c
            speaker_label = "Vera"
        else:
            raise ValueError(f"Unknown speaker code {speaker_code!r} at segment {i} line {seg.get('line_number')}")

        if not voice:
            raise ValueError(f"No voice configured for speaker {speaker_label} (code {speaker_code})")

        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        text = seg["text"]

        # Clean up markdown formatting for TTS
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # Remove **bold**
        text = re.sub(r'^#+\s+', '', text)  # Remove # headers

        print(f"[Podcast] TTS segment {i+1}/{len(segments)} ({speaker_label})...", file=sys.stderr)
        tts_segment(text, voice, seg_path)
        segment_files.append(seg_path)
        time.sleep(0.3)

    # Build concat list
    list_file = os.path.join(tmpdir, "segments.txt")
    with open(list_file, "w") as f:
        for audio in intro_audio_files:
            f.write(f"file '{audio}'\n")
        for audio in segment_files:
            f.write(f"file '{audio}'\n")

    # Convert segments back to script format for transcript/JSON
    script = []
    for seg in segments:
        script.append({
            "speaker": seg["speaker"],
            "text": seg["text"]
        })

    return tmpdir, list_file, segment_files, [], script, intro_audio_files


def generate_verbatim_podcast_from_script(script_text, config, title=None, urls=None, goal=None):
    """Generate podcast from a verbatim script.

    Args:
        script_text: Complete script with dialogue
        config: Podcast config
        title: Override title (otherwise extracted from script)
        urls: Placeholder URLs for metadata
        goal: Optional goal/instructions (not used for verbatim, but recorded)
    """
    if not title:
        title = _generate_title_from_script(script_text)

    if not urls:
        urls = ["https://internal.do-not-panic.com/verbatim-script"]

    # Load host SOUL profiles for personality versioning
    soul_profiles = _load_host_soul_profiles()

    # Load sound effects library and detect markers
    # Check if this is a special episode (has [SOUND: theme] marker or SOUL in title)
    has_theme_marker = "[SOUND: theme]" in script_text or "[SOUND:theme]" in script_text
    is_special_episode = has_theme_marker or "SOUL" in (title or "") or "soul" in (goal or "").lower()
    theme_variant = "full" if is_special_episode else "short"

    sound_library = load_sound_library(library_name="gemini_library.yaml", theme_variant=theme_variant)
    sound_markers = find_sound_markers(script_text)

    # For special episodes, remove theme marker from sound effects (will be mixed separately)
    if is_special_episode:
        sound_markers = [(name, line, text) for name, line, text in sound_markers if name.lower() != "theme"]
        print(f"[Podcast] Special episode: theme will be mixed via theatrical renderer", file=sys.stderr)

    sounds_used = [name for name, _, _ in sound_markers]

    if sound_markers:
        print(f"[Podcast] Found {len(sound_markers)} sound effect markers (excluding theme)", file=sys.stderr)
        for name, line, text in sound_markers:
            print(f"[Podcast]   {name} at line {line}", file=sys.stderr)

    print(f"[Podcast] Generating verbatim podcast: {title}", file=sys.stderr)
    print(f"[Podcast] VERA pronunciation: TTS will pronounce as 'Vera', not spell 'V-E-R-A'", file=sys.stderr)

    # Extract segments for sound mapping (needed for theatrical mixing)
    segments = _extract_script_segments(script_text)

    # Create podcast (skip countdown and theme for SOUL/special episodes)
    tmpdir, list_file, segment_files, sources, script, intro_audio_files = create_verbatim_podcast(
        script_text, config, soul_profiles,
        skip_countdown=is_special_episode,
        skip_theme=is_special_episode,  # Skip theme in concat; will mix separately for theatrical
        theme_fade_duration_ms=800  # Quick fade: 0.8s for snappy crossfade
    )

    try:
        # Output path
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        year, month = date_str.split("-")[:2]
        output_dir = Path(__file__).parent / "drafts" / year / month
        output_dir.mkdir(parents=True, exist_ok=True)

        # Unique stem - temporary, will be renamed with checksum after generation
        slug = unicodedata.normalize("NFKD", title)
        slug = slug.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\s-]", "", slug, flags=re.ASCII).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug)[:40].strip("-")

        # Temporary filename for generation
        temp_stem = f"{date_str}-{slug}-temp"
        temp_audio_file = output_dir / f"{temp_stem}.mp3"

        # Log audio timeline with sound effects
        log_sound_timeline(segment_files, sound_markers, sound_library)

        # Build enhanced concat file with sound insertions if sounds exist
        concat_file = list_file
        if sound_markers:
            try:
                # Map sound markers to segment indices
                # For theatrical episodes, pass script segments for intelligent mapping
                if is_special_episode:
                    sound_map = map_sounds_to_segments(sound_markers, len(segment_files), script_segments=segments)
                else:
                    sound_map = map_sounds_to_segments(sound_markers, len(segment_files))

                concat_file = build_ffmpeg_concat_script(
                    segment_files,
                    sound_map,
                    sound_library,
                    tmpdir,
                    intro_files=intro_audio_files
                )
                print(f"[Podcast] Using sound-enhanced concat file: {concat_file}", file=sys.stderr)
            except Exception as e:
                import traceback
                tb_lines = traceback.format_exc().split('\n')
                print(f"[Podcast] Warning: Sound concat build failed, using original: {e}", file=sys.stderr)
                for line in tb_lines[-10:]:
                    if line.strip():
                        print(f"[Podcast]   {line}", file=sys.stderr)
                concat_file = list_file

        # Finalize audio to temporary file
        finalize_podcast(tmpdir, concat_file, str(temp_audio_file))

        # For special episodes, mix with theme on theatrical timeline
        if is_special_episode:
            # Use 20s full theme from Music directory if available, else fallback to 10s
            theme_path = Path.home() / "Music" / "ai_cuts" / "theme.mp3"
            if not theme_path.exists():
                theme_path = Path(__file__).parent / "sounds" / "theme-full.mp3"

            print(f"[Podcast] Special episode detected: theme_path={theme_path}", file=sys.stderr)
            print(f"[Podcast] Theme file exists: {theme_path.exists()}", file=sys.stderr)
            if theme_path.exists():
                theme_size = theme_path.stat().st_size
                # Determine duration: 20s if it's the ai_cuts version, 10s otherwise
                theme_duration_s = 20 if "ai_cuts" in str(theme_path) else 10
                print(f"[Podcast] Using theme: {theme_path.name} ({theme_size} bytes, {theme_duration_s}s)", file=sys.stderr)
                theatrical_output = output_dir / f"{temp_stem}-theatrical.mp3"
                final_body = render_soul_intro(
                    str(temp_audio_file),
                    str(theme_path),
                    str(theatrical_output),
                    tmpdir,
                    theme_duration_s=theme_duration_s
                )
                # Replace temp file with theatrical mix
                print(f"[Podcast] render_soul_intro returned: {final_body}", file=sys.stderr)
                print(f"[Podcast] temp_audio_file: {temp_audio_file}", file=sys.stderr)
                if final_body != str(temp_audio_file):
                    size_before = temp_audio_file.stat().st_size if temp_audio_file.exists() else 0
                    print(f"[Podcast] Replacing unmixed body ({size_before} bytes) with theatrical mix", file=sys.stderr)
                    temp_audio_file.unlink(missing_ok=True)
                    theatrical_output.rename(temp_audio_file)
                    size_after = temp_audio_file.stat().st_size if temp_audio_file.exists() else 0
                    print(f"[Podcast] Theatrical mix now at temp file ({size_after} bytes)", file=sys.stderr)
                else:
                    print(f"[Podcast] ⚠ render_soul_intro returned body path (mix failed?)", file=sys.stderr)

        # Compute MD5 checksum of generated audio and rename with checksum
        def compute_file_checksum(filepath, algo='md5'):
            """Compute checksum of file."""
            import hashlib
            hash_obj = hashlib.new(algo)
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()[:10]  # First 10 chars

        checksum = compute_file_checksum(str(temp_audio_file))
        stem = f"{date_str}-{slug}-{checksum}"
        audio_file = output_dir / f"{stem}.mp3"
        temp_audio_file.rename(audio_file)
        print(f"[Podcast] Audio checksum: {checksum}", file=sys.stderr)

        # Save transcript and SRT
        hosts = config.get("podcast", {}).get("hosts", {})
        host_names = {
            "A": hosts.get("a", {}).get("name", "Hal Turing"),
            "B": hosts.get("b", {}).get("name", "Dr. Ada Shannon"),
            "C": hosts.get("c", {}).get("name", "Vera"),  # Third host
        }

        transcript_file = audio_file.with_suffix(".txt")
        srt_file = audio_file.with_suffix(".srt")
        save_transcript(script, str(transcript_file), host_names)
        generate_srt(script, segment_files, str(srt_file), host_names=host_names)

        # Save script JSON with sound attribution
        script_file = audio_file.with_suffix(".json")
        sound_attribution = get_attribution_text(sound_library, sounds_used) if sounds_used else ""
        with open(script_file, "w") as f:
            json.dump({
                "script": script,
                "sources": sources,
                "is_verbatim": True,
                "sound_effects": sounds_used,
                "attribution": sound_attribution
            }, f, indent=2)

        # Generate cover image
        image_file = audio_file.with_suffix(".png")
        try:
            render_title_cover(title, str(image_file))
        except Exception as e:
            print(f"[Podcast] Warning: Cover generation failed: {e}", file=sys.stderr)

        # Record in database
        conn = get_connection()
        init_db(conn)

        # Build description with sound attribution note
        description = f"Special episode: {title}"
        if sounds_used:
            description += "\n\nSound effects and music licensed under Creative Commons. See show notes for attribution."

        podcast_id = insert_podcast(
            conn,
            title=title,
            publish_date=date_str,
            elevenlabs_project_id="tts-local",
            image_file=str(image_file),
            audio_file=str(audio_file),
            source_urls=",".join(urls),
            description=description,
            visibility="public",
        )

        print(f"[Podcast] ✅ Verbatim podcast generated successfully!", file=sys.stderr)
        print(f"[Podcast]   Title: {title}", file=sys.stderr)
        print(f"[Podcast]   Audio: {audio_file}", file=sys.stderr)
        print(f"[Podcast]   Transcript: {transcript_file}", file=sys.stderr)

        conn.close()

    finally:
        cleanup_podcast_tmpdir(tmpdir)


if __name__ == "__main__":
    import yaml

    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Test: read script from stdin or file
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            script_text = f.read()
    else:
        script_text = sys.stdin.read()

    title = sys.argv[2] if len(sys.argv) > 2 else None
    generate_verbatim_podcast_from_script(script_text, config, title=title)
