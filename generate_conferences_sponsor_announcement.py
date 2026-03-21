#!/usr/bin/env python3
"""Generate a draft announcement episode for conference tracking + sponsor page.

Usage:
    source .venv/bin/activate
    source ~/.enhance-bash
    python generate_conferences_sponsor_announcement.py

Generates a draft episode under drafts/YYYY/MM plus a DB row so it can be
published later with gen-podcast.py publish --draft ...
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from db import get_connection, init_db, insert_podcast
from elevenlabs_client import tts_segment, finalize_podcast, save_transcript, generate_srt
from image_gen import generate_episode_image

VOICE_A = "iP95p4xoKVk53GoZ742B"
VOICE_B = "HBQuDIqftrmAQQAHSWnF"
TODAY = date.today().isoformat()
TITLE = "New Site Features: Conferences Tracking and How to Support the Show"
SLUG = "conferences-and-sponsor-update"
DESCRIPTION = (
    "Special announcement: AI Post Transformers now has a Conferences section "
    "that tracks the AI conferences and papers we have covered, plus a new "
    "Sponsor page for listeners who want to help fund credits and infrastructure "
    "that keep the show running."
)
URLS = [
    "https://podcast.do-not-panic.com/conference/announcements/",
    "https://podcast.do-not-panic.com/sponsor.html",
    "https://github.com/sponsors/mcgrof",
]

SCRIPT = [
    {"speaker": "A", "text":
     "Hey everyone, Hal Turing here. Quick special announcement today. We just added two practical new things to the site, and both of them make the project a lot easier to understand and, honestly, a lot easier to sustain."},
    {"speaker": "B", "text":
     "First up: conferences. We now have a Conferences link on the site that tracks the AI conferences we've been following, along with the papers and podcast episodes tied to them."},
    {"speaker": "A", "text":
     "So if you've been wondering which FAST papers we've covered, or which conference a particular episode came from, you don't have to mentally reconstruct the whole release history anymore. Just click Conferences and pick the one you care about."},
    {"speaker": "B", "text":
     "And the point isn't just navigation. It's editorial transparency. You can see how the coverage clusters, where we have depth, where we're still thin, and which conferences are producing the papers that keep showing up in our queue."},
    {"speaker": "A", "text":
     "The second new thing is the Sponsor link. This podcast is not magic. It's a stack of AI credits, storage bills, synthesis costs, publishing infrastructure, and a frankly unreasonable amount of glue code."},
    {"speaker": "B", "text":
     "The sponsor page is meant to make that visible. It points to contributors and operators who are already burning existing credits across different services to make the show happen, and it gives listeners a clean way to help if they want to keep the machine fed."},
    {"speaker": "A", "text":
     "Right now one clear example is GitHub Sponsors for mcgrof. If you want to help pay for the credits behind generation, voice synthesis, storage, and site hosting, that's the button to hit."},
    {"speaker": "B", "text":
     "And yes, this is still the same project. Same no-hype policy, same paper-first editorial focus, same obsession with systems details. We just made the structure around it more visible."},
    {"speaker": "A", "text":
     "So: Conferences if you want to browse coverage by venue. Sponsor if you want to help fund the weird pile of credits and infrastructure that keeps this thing alive. Both links are now on the site."},
    {"speaker": "B", "text":
     "Thanks to everyone listening, sharing episodes, filing suggestions, and keeping us honest. We'll be back to the regular paper deep dives right after this little site update detour."},
]


def generate_cover(output_path: str):
    prompt = (
        "Dark-themed podcast cover art, square format. A neon conference badge wall on one side "
        "and a glowing support/sponsor button on the other. Clean modern typography reading "
        "'Conferences + Sponsor'. Cute robot podcast mascot with headphones in the center. "
        "Dark navy/charcoal background, cyan and orange accent colors, polished podcast infographic style."
    )
    try:
        return generate_episode_image(prompt, output_path, size="1024x1024", quality="high")
    except Exception:
        fallback = Path(__file__).parent / "images" / "github.png"
        if fallback.exists():
            import shutil
            shutil.copyfile(fallback, output_path)
            return output_path
        raise


def generate_audio(script, output_dir: Path):
    tmpdir = tempfile.mkdtemp(prefix="announcement_support_")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "0.35", "-c:a", "libmp3lame", "-q:a", "2", os.path.join(tmpdir, "short_pause.mp3")
    ], capture_output=True, text=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "0.9", "-c:a", "libmp3lame", "-q:a", "2", os.path.join(tmpdir, "silence.mp3")
    ], capture_output=True, text=True)

    tts_segment("three", VOICE_A, os.path.join(tmpdir, "countdown_3.mp3"))
    tts_segment("two", VOICE_B, os.path.join(tmpdir, "countdown_2.mp3"))
    tts_segment("one", VOICE_A, os.path.join(tmpdir, "countdown_1.mp3"))
    tts_segment("Welcome to AI Post Transformers!", VOICE_A, os.path.join(tmpdir, "welcome_a.mp3"))
    tts_segment("Welcome to AI Post Transformers!", VOICE_B, os.path.join(tmpdir, "welcome_b.mp3"))

    subprocess.run([
        "ffmpeg", "-y", "-i", os.path.join(tmpdir, "welcome_a.mp3"),
        "-i", os.path.join(tmpdir, "welcome_b.mp3"),
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
        "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", os.path.join(tmpdir, "welcome_overlay.mp3")
    ], capture_output=True, text=True)

    intro_files = [
        os.path.join(tmpdir, "countdown_3.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_2.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "countdown_1.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
        os.path.join(tmpdir, "welcome_overlay.mp3"),
        os.path.join(tmpdir, "short_pause.mp3"),
    ]

    segment_files = []
    for i, seg in enumerate(script):
        voice = VOICE_A if seg["speaker"] == "A" else VOICE_B
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        print(f"[Announcement] TTS segment {i+1}/{len(script)}", file=sys.stderr)
        tts_segment(seg["text"], voice, seg_path)
        segment_files.append(seg_path)
        time.sleep(0.25)

    hash6 = hashlib.sha256(f"{TITLE}|{TODAY}".encode()).hexdigest()[:6]
    basename = f"{TODAY}-{SLUG}-{hash6}"
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / f"{basename}.mp3"
    txt_path = output_dir / f"{basename}.txt"
    srt_path = output_dir / f"{basename}.srt"
    img_path = output_dir / f"{basename}.png"
    json_path = output_dir / f"{basename}.json"

    list_file = os.path.join(tmpdir, "segments.txt")
    with open(list_file, "w") as f:
        for sf in intro_files:
            f.write(f"file '{sf}'\n")
        for sf in segment_files:
            f.write(f"file '{sf}'\n")
            f.write(f"file '{os.path.join(tmpdir, 'silence.mp3')}'\n")

    finalize_podcast(tmpdir, list_file, str(audio_path))
    save_transcript(script, str(txt_path))
    generate_srt(script, segment_files, str(srt_path))

    metadata = {
        "title": TITLE,
        "date": TODAY,
        "slug": SLUG,
        "description": DESCRIPTION,
        "urls": URLS,
        "type": "announcement",
    }
    json_path.write_text(json.dumps(metadata, indent=2))
    return basename, audio_path, txt_path, srt_path, img_path, json_path


def main():
    project_root = Path(__file__).parent
    draft_dir = project_root / "drafts" / TODAY[:4] / TODAY[5:7]

    basename, audio, txt, srt, img, meta = generate_audio(SCRIPT, draft_dir)
    print("[Announcement] Generating cover image...", file=sys.stderr)
    generate_cover(str(img))

    conn = get_connection()
    init_db(conn)
    insert_podcast(
        conn,
        title=TITLE,
        publish_date=TODAY,
        audio_file=str(audio),
        source_urls=json.dumps(URLS),
        description=DESCRIPTION,
        image_file=str(img),
    )
    conn.close()

    print("\nDONE", file=sys.stderr)
    print(f"Audio: {audio}", file=sys.stderr)
    print(f"Transcript: {txt}", file=sys.stderr)
    print(f"Subtitles: {srt}", file=sys.stderr)
    print(f"Image: {img}", file=sys.stderr)
    print(f"Metadata: {meta}", file=sys.stderr)


if __name__ == "__main__":
    main()
