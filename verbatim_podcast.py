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

import tempfile
import time
import os
import subprocess
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
        r'^\*\*(Hal|Ada|VERA|HOST)\*\*:\s*',  # **HAL**: **ADA**: (markdown bold)
        r'^(Hal|Ada|VERA|HOST):\s+',  # Hal: Ada: VERA: (or uppercase)
        r'^\[(MUSIC|SOUND|PAUSE|NOTIFICATION|DRAMATIC)',  # [Stage direction]
    ]

    lines = sample.split('\n')
    marker_count = 0
    for line in lines:
        for pattern in patterns:
            if re.search(pattern, line.strip(), re.IGNORECASE):
                marker_count += 1
                break

    # If >10% of lines are speaker markers, it's a script
    return marker_count > len(lines) * 0.1


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

    # Map speaker names to A/B/C (C = VERA, new host)
    # VERA can use her own voice or share with Ada; for now route to B
    speaker_map = {
        'Hal': 'A', 'HAL': 'A',
        'Ada': 'B', 'ADA': 'B', 'DR. ADA': 'B',
        'VERA': 'B',  # VERA introduced as third host, uses B voice for now
        'Overlord': 'A',  # Guest voices use default host voice
        'Claude': 'A',
        'Pro': 'B',
        'Codex': 'A',
        'Luis': 'A',
        'Host': 'A', 'HOST': 'A',
        'Guest': 'B', 'GUEST': 'B',
        'A': 'A', 'B': 'B',
    }

    current_speaker = None
    current_text = []

    for line in lines:
        line = line.rstrip()

        # Skip sound/music directions
        if re.match(r'^\[(SOUND|MUSIC|SOUND:|MUSIC:)', line, re.IGNORECASE):
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

        # Skip other stage directions
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

        # Try to match speaker labels
        match = re.match(r'^(?:\*\*)?([A-Z\s\.]+?)(?:\*\*)?\s*:\s+(.+)$', line)
        if match:
            speaker_name = match.group(1).strip()
            text = match.group(2).strip()

            # Map to A/B
            speaker = speaker_map.get(speaker_name, None)
            if speaker:
                # Flush previous speaker
                if current_text and current_speaker:
                    text_str = '\n'.join(current_text).strip()
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
    voice_a = el_config.get("voice_a", "oTOJ3soGzir2ldiaDSNs")
    voice_b = el_config.get("voice_b", "HBQuDIqftrmAQQAHSWnF")

    voices = el_config.get("voices", {})
    if voices:
        host = voices.get("host", {})
        guest = voices.get("guest", {})
        if host.get("voice_id"):
            voice_a = host["voice_id"]
        if guest.get("voice_id"):
            voice_b = guest["voice_id"]

    # Log loaded host personalities
    if soul_profiles:
        print(f"[Podcast] Host profiles loaded: {', '.join(f'{h} v{soul_profiles[h]['version']}' for h in soul_profiles)}", file=sys.stderr)

    print("[Podcast] Parsing verbatim script segments...", file=sys.stderr)
    segments = _extract_script_segments(script_text)
    print(f"[Podcast] Extracted {len(segments)} dialogue segments", file=sys.stderr)

    if not segments:
        raise ValueError("No dialogue segments found in script")

    # Prepare for TTS
    sweep_stale_podcast_tmp()
    tmpdir = tempfile.mkdtemp(prefix="podcast_")
    segment_files = []

    # Countdown intro (same as normal podcasts)
    print("[Podcast] Generating countdown intro...", file=sys.stderr)
    tts_segment("three", voice_a, os.path.join(tmpdir, "countdown_3.mp3"), config)
    tts_segment("two", voice_b, os.path.join(tmpdir, "countdown_2.mp3"))
    tts_segment("one", voice_a, os.path.join(tmpdir, "countdown_1.mp3"))
    time.sleep(0.3)

    tts_segment("Welcome to AI Post Transformers!", voice_a, os.path.join(tmpdir, "welcome_a.mp3"))
    tts_segment("Welcome to AI Post Transformers!", voice_b, os.path.join(tmpdir, "welcome_b.mp3"))
    time.sleep(0.3)

    subprocess.run(
        ["ffmpeg", "-y", "-i", os.path.join(tmpdir, "welcome_a.mp3"),
         "-i", os.path.join(tmpdir, "welcome_b.mp3"),
         "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
         "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "welcome_overlay.mp3")],
        capture_output=True, text=True
    )

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
        os.path.join(tmpdir, "welcome_overlay.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
    ]

    # Generate TTS for script segments
    print("[Podcast] Generating TTS for script segments...", file=sys.stderr)
    for i, seg in enumerate(segments):
        voice = voice_a if seg["speaker"] == "A" else voice_b
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        text = seg["text"]

        # Clean up markdown formatting for TTS
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # Remove **bold**
        text = re.sub(r'^#+\s+', '', text)  # Remove # headers

        print(f"[Podcast] TTS segment {i+1}/{len(segments)} ({seg['speaker']})...", file=sys.stderr)
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

    return tmpdir, list_file, segments, [], script


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

    print(f"[Podcast] Generating verbatim podcast: {title}", file=sys.stderr)
    print(f"[Podcast] VERA pronunciation: TTS will pronounce as 'Vera', not spell 'V-E-R-A'", file=sys.stderr)

    # Create podcast
    tmpdir, list_file, segments, sources, script = create_verbatim_podcast(script_text, config, soul_profiles)

    try:
        # Output path
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        year, month = date_str.split("-")[:2]
        output_dir = Path(__file__).parent / "drafts" / year / month
        output_dir.mkdir(parents=True, exist_ok=True)

        # Unique stem
        slug = unicodedata.normalize("NFKD", title)
        slug = slug.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\s-]", "", slug, flags=re.ASCII).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug)[:40].strip("-")
        hash_input = f"{title}|{date_str}|{','.join(sorted(urls))}"
        hash6 = hashlib.sha256(hash_input.encode()).hexdigest()[:6]
        stem = f"{date_str}-{slug}-{hash6}"

        audio_file = output_dir / f"{stem}.mp3"

        # Finalize audio
        finalize_podcast(tmpdir, list_file, str(audio_file))

        # Save transcript and SRT
        hosts = config.get("podcast", {}).get("hosts", {})
        host_names = {
            "A": hosts.get("a", {}).get("name", "Hal Turing"),
            "B": hosts.get("b", {}).get("name", "Dr. Ada Shannon"),
        }

        transcript_file = audio_file.with_suffix(".txt")
        srt_file = audio_file.with_suffix(".srt")
        save_transcript(script, str(transcript_file), host_names)
        generate_srt(script, segments, str(srt_file), host_names=host_names)

        # Save script JSON
        script_file = audio_file.with_suffix(".json")
        with open(script_file, "w") as f:
            json.dump({"script": script, "sources": sources, "is_verbatim": True}, f, indent=2)

        # Generate cover image
        image_file = audio_file.with_suffix(".png")
        try:
            render_title_cover(title, str(image_file))
        except Exception as e:
            print(f"[Podcast] Warning: Cover generation failed: {e}", file=sys.stderr)

        # Record in database
        conn = get_connection()
        init_db(conn)

        podcast_id = insert_podcast(
            conn,
            title=title,
            publish_date=date_str,
            elevenlabs_project_id="tts-local",
            transcript_file=str(transcript_file),
            image_file=str(image_file),
            audio_file=str(audio_file),
            source_urls=",".join(urls),
            description=f"Special episode: {title}",
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
