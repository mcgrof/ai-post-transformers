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
    voice_map = {'HAL': 'A', 'ADA': 'B'}  # HAL → voice A, ADA → voice B

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

    Returns list of {speaker, text, is_narration} dicts.
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
    lines = lines[start_idx:]

    # Map speaker names to A/B (only podcast personas, no personal names)
    # VERA is not a speaker; she's a concept Hal & Ada discuss
    speaker_map = {
        'Hal': 'A', 'HAL': 'A', 'Hal Turing': 'A',
        'Ada': 'B', 'ADA': 'B', 'Dr. Ada Shannon': 'B', 'DR. ADA': 'B', 'Dr Ada Shannon': 'B',
        'Vera': 'C', 'VERA': 'C',  # Third host - use 'Vera' not 'VERA' (AI capitalization issues)
        'Overlord': 'A', 'OVERLORD': 'A', 'OVERLORD MEMO': 'A', 'OVERLORD VOICE': 'A',
        'Claude': 'A', 'CLAUDE': 'A',
        'Pro': 'B', 'PRO': 'B', 'ChatGPT Pro': 'B',
        'Codex': 'A', 'CODEX': 'A',
        'Host': 'A', 'HOST': 'A', 'NARRATOR': 'A',
        'NARRATOR/HOST INTRO': 'A',
        'Guest': 'B', 'GUEST': 'B',
        'A': 'A', 'B': 'B', 'C': 'C',
    }

    current_speaker = None
    current_text = []

    for line in lines:
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
                        'is_narration': False
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
                        'is_narration': False
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
            # Map to A/B - try exact match first, then try variations
            speaker = speaker_map.get(speaker_name, None)
            # If no exact match, try removing 's and other possessive forms
            if not speaker and "'s" in speaker_name:
                speaker = speaker_map.get(speaker_name.replace("'s", ""), None)
            if speaker:
                # Flush previous speaker
                if current_text and current_speaker:
                    text_str = '\n'.join(current_text).strip()
                    text_str = _clean_dialogue_text(text_str)  # Remove metadata
                    if text_str:
                        segments.append({
                            'speaker': current_speaker,
                            'text': text_str,
                            'is_narration': False
                        })

                current_speaker = speaker
                current_text = [text] if text else []
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
                        'is_narration': False
                    })
            current_speaker = 'A'
            current_text = [line]

    # Flush final segment
    if current_text and current_speaker:
        text_str = '\n'.join(current_text).strip()
        text_str = _clean_dialogue_text(text_str)  # Remove metadata
        if text_str:
            segments.append({
                'speaker': current_speaker,
                'text': text_str,
                'is_narration': False
            })

    return segments


def _generate_title_from_script(text):
    """Extract or infer a title from script content.

    Look for title patterns:
    - Lines starting with # (markdown heading)
    - First sentence before dialogue
    - Default to "Special Episode"
    """
    lines = text.split('\n')

    for line in lines:
        # Markdown heading
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


def create_verbatim_podcast(script_text, config, soul_profiles=None):
    """Create podcast from verbatim script without LLM analysis.

    Returns (tmpdir, list_file, segments, sources, script) similar to create_podcast.

    Args:
        script_text: verbatim script with dialogue markers
        config: podcast configuration
        soul_profiles: dict of host SOUL.md profiles (for logging)
    """
    el_config = config.get("elevenlabs", {})
    voice_a = el_config.get("voice_a", "oTOJ3soGzir2ldiaDSNs")  # Hal
    voice_b = el_config.get("voice_b", "HBQuDIqftrmAQQAHSWnF")  # Ada
    voice_c = el_config.get("voice_c", "TBD")  # Vera (third host)

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

    # Countdown intro (skip welcome for verbatim episodes - goes straight to script)
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

    # Add theme song to intro (plays after 3-2-1)
    theme_path = Path(__file__).parent / "sounds" / "theme-full.mp3"

    intro_audio_files = [
        os.path.join(tmpdir, "countdown_3.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_2.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_1.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
    ]

    # Add theme if it exists
    if theme_path.exists():
        intro_audio_files.append(str(theme_path))
        intro_audio_files.append(os.path.join(tmpdir, "short_pause.mp3"))
        print("[Podcast] Added theme song to intro", file=sys.stderr)

    # Generate TTS for script segments
    print("[Podcast] Generating TTS for script segments...", file=sys.stderr)
    for i, seg in enumerate(segments):
        # Map speaker to voice
        if seg["speaker"] == "A":
            voice = voice_a
            speaker_label = "Hal"
        elif seg["speaker"] == "B":
            voice = voice_b
            speaker_label = "Ada"
        elif seg["speaker"] == "C":
            voice = voice_c
            speaker_label = "Vera"
        else:
            voice = voice_a
            speaker_label = seg["speaker"]

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

    return tmpdir, list_file, segment_files, [], script


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
    # Check if this is a special episode (use full theme) or regular (use short theme)
    is_special_episode = "SOUL" in (title or "") or "soul" in (goal or "").lower()
    theme_variant = "full" if is_special_episode else "short"

    sound_library = load_sound_library(library_name="gemini_library.yaml", theme_variant=theme_variant)
    sound_markers = find_sound_markers(script_text)
    sounds_used = [name for name, _, _ in sound_markers]

    if sound_markers:
        print(f"[Podcast] Found {len(sound_markers)} sound effect markers", file=sys.stderr)
        for name, line, text in sound_markers:
            print(f"[Podcast]   {name} at line {line}", file=sys.stderr)

    print(f"[Podcast] Generating verbatim podcast: {title}", file=sys.stderr)
    print(f"[Podcast] VERA pronunciation: TTS will pronounce as 'Vera', not spell 'V-E-R-A'", file=sys.stderr)

    # Create podcast
    tmpdir, list_file, segment_files, sources, script = create_verbatim_podcast(script_text, config, soul_profiles)

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
                print(f"[Podcast] Warning: Sound concat build failed, using original: {e}", file=sys.stderr)
                concat_file = list_file

        # Finalize audio to temporary file
        finalize_podcast(tmpdir, concat_file, str(temp_audio_file))

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
