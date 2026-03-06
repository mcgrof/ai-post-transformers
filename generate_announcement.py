#!/usr/bin/env python3
"""Generate the open-source launch announcement episode + GitHub icon + infographic.

Usage:
    source ~/.enhance-bash
    .venv/bin/python generate_announcement.py

Generates:
  - images/github.png           (GitHub icon matching platform button style)
  - drafts/2026/03/announcement  (audio, transcript, SRT, image, metadata)
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

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from elevenlabs_client import tts_segment, finalize_podcast, save_transcript, generate_srt
from image_gen import generate_episode_image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VOICE_A = "iP95p4xoKVk53GoZ742B"  # Hal Turing (host)
VOICE_B = "HBQuDIqftrmAQQAHSWnF"  # Dr. Ada Shannon (guest)

TITLE = "We're Open Source! New Home, Visualizations, and How to Shape Our Queue"
SLUG = "open-source-launch"
TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Announcement script — short, fun, energetic
# ---------------------------------------------------------------------------

SCRIPT = [
    {"speaker": "A", "text":
     "Hey everyone! Hal Turing here, and today is a very special episode. "
     "No paper deep-dive this time. Instead, we have some genuinely "
     "exciting news to share."},

    {"speaker": "B", "text":
     "This is Dr. Ada Shannon, and I have to say, Hal, I've been "
     "waiting to say this for a while. We have a home. A real, proper "
     "website. podcast dot do dash not dash panic dot com. Go check it out."},

    {"speaker": "A", "text":
     "That's right! Full episode archive, topic browsing, "
     "and here's the part I'm really excited about: interactive "
     "visualizations. We're building visual companions for episodes "
     "where it helps you actually see the ideas."},

    {"speaker": "B", "text":
     "Because let's be honest, when I'm describing a three-dimensional "
     "attention pattern or a memory hierarchy diagram, audio only goes "
     "so far. Now you can pull up the visualization and follow along. "
     "Diagrams, interactive plots, the works."},

    {"speaker": "A", "text":
     "But wait, there's more. And I don't say that in an infomercial "
     "way, I say it because this next part is actually kind of wild. "
     "The entire software that runs this podcast? Open source. "
     "MIT license. You can go read every line of code right now."},

    {"speaker": "B", "text":
     "You can find the repository by clicking the GitHub link right "
     "from the podcast's new home page. Everything: the multi-pass "
     "LLM pipeline, the editorial scoring engine, the TTS synthesis, "
     "the queue ranking algorithm. All of it. Open. Free."},

    {"speaker": "A", "text":
     "And this brings us to maybe my favorite part. You, the listener, "
     "can now directly influence what papers we cover. On our website, "
     "on the queue page, and even on the main page, you'll see a link "
     "that says Suggest a Paper. Click it. It opens a GitHub issue form. "
     "Paste an arXiv URL, tell us why the paper is interesting, and submit."},

    {"speaker": "B", "text":
     "Your submission goes straight into our editorial queue. It gets "
     "the same algorithmic treatment as every other paper we evaluate. "
     "And it gets a small boost, actually, because a human took the "
     "time to flag it. That matters to us."},

    {"speaker": "A", "text":
     "OK Ada, since we just open-sourced the whole thing, let's give "
     "people a tour of how the queue actually works. Because the "
     "algorithm is genuinely interesting."},

    {"speaker": "B", "text":
     "So the queue runs a two-pass architecture. Pass one is fast "
     "embedding-based scoring. We take every paper from arXiv, "
     "HuggingFace Daily Papers, Semantic Scholar, and community "
     "submissions, embed them using sentence-transformers, and compute "
     "similarity against three seed profiles: public AI interest, "
     "memory and storage relevance, and a negative out-of-scope profile."},

    {"speaker": "A", "text":
     "Each paper gets scored on two independent axes. A Public AI "
     "Interest score and a Memory/Storage score. These are weighted "
     "composites built from features like broad relevance, momentum, "
     "teachability, novelty, evidence quality, systems leverage, and "
     "deployment proximity. The weights are all public in weights.yaml."},

    {"speaker": "B", "text":
     "And here's the inductive bias: we believe that memory, storage, "
     "bandwidth, and interconnect are increasingly first-class "
     "constraints in modern AI. Not incidental details. Primary "
     "research targets. The memory wall paper by Gholami, the LLM "
     "inference hardware challenges paper, DualPath for agentic "
     "workloads. These aren't niche. They're the future."},

    {"speaker": "A", "text":
     "So our queue isn't just a popularity contest. A paper can rank "
     "high because it changes how the field thinks, that's the public "
     "lens. Or because it addresses real systems bottlenecks, that's "
     "the memory lens. Or both, that's what we call the bridge category. "
     "Papers that score well on both axes are the ones we get most "
     "excited about."},

    {"speaker": "B", "text":
     "Pass two sends the shortlist to an LLM for editorial judgment. "
     "A seven-question rubric: audience relevance, systems impact, "
     "memory connection, evidence quality, generality, novelty, and "
     "episode potential. The LLM can adjust scores up or down by up "
     "to 0.3, assign badges, and set editorial status. Cover now, "
     "Monitor, Deferred, or Out of Scope."},

    {"speaker": "A", "text":
     "And the final queue partitions into Bridge, Public AI, "
     "Memory/Storage, and Monitor sections. Ten slots each for the "
     "top three. A diversity cap prevents too many similar papers from "
     "clustering. Everything is transparent. You can hover over any "
     "paper on the queue page and see exactly why it scored the way "
     "it did."},

    {"speaker": "B", "text":
     "Now, looking ahead. One of the most requested features from "
     "folks who've been following the project is internationalization. "
     "Multiple languages. And we hear you."},

    {"speaker": "A", "text":
     "This is a real priority for us. We're already using ElevenLabs' "
     "multilingual v2 model for synthesis, so the voice infrastructure "
     "supports it. The challenge is making it scale: script generation "
     "in multiple languages, subtitle localization, RSS feeds per "
     "language, all without multiplying the compute cost linearly."},

    {"speaker": "B", "text":
     "It's a systems problem, appropriately enough for this podcast. "
     "But it's coming. If you care about this, come tell us on the "
     "GitHub repo. File an issue. Contribute. That's the whole point "
     "of going open source."},

    {"speaker": "A", "text":
     "So to wrap up: podcast dot do dash not dash panic dot com is live, "
     "visualizations are coming with episodes, the code is fully open "
     "source under the MIT license, just click the GitHub link on our "
     "home page, you can suggest papers right from the website, and "
     "internationalization is on the roadmap."},

    {"speaker": "B", "text":
     "This podcast exists because we think AI research deserves more "
     "than summaries. It deserves real analysis, honest critique, and "
     "transparent editorial judgment. Now you can see how we make "
     "those judgments. And you can help shape them."},

    {"speaker": "A", "text":
     "Thanks for listening, everyone. We'll be back with our regular "
     "paper deep-dives very soon. Until then, go break some memory "
     "walls. See you next time on AI Post Transformers!"},
]


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def generate_github_icon(output_path):
    """Generate a GitHub icon matching the platform button style."""
    prompt = (
        "Digital illustration of a cute small robot mascot with glowing "
        "blue eyes and headphones, sitting next to the GitHub Octocat logo. "
        "The robot is the same character from a podcast series. "
        "Below them, bold text reads 'GitHub'. "
        "Style: clean digital illustration, dark transparent background, "
        "cyberpunk glow accents in blue and purple. "
        "The image should work as a button icon at 180px height. "
        "No busy background, mostly transparent/dark with the characters "
        "and text as the focus. Similar style to a podcast platform button."
    )
    return generate_episode_image(prompt, output_path, size="1024x1024",
                                  quality="medium")


def generate_infographic(output_path):
    """Generate the announcement episode infographic."""
    prompt = (
        "Dark-themed podcast infographic, 1024x1024 square format. "
        "IMPORTANT: All text and visuals must fit well within the frame "
        "with generous padding on all sides. No text or graphics near edges.\n\n"
        "Center layout, top to bottom:\n"
        "1. Large heading 'WE ARE OPEN SOURCE' in bold white at top center\n"
        "2. A cute robot mascot with headphones and glowing cyan eyes, "
        "standing next to an open padlock icon glowing in cyan\n"
        "3. A simple vertical flowchart with 3 rounded boxes connected "
        "by arrows: 'Papers In' (top, cyan) -> 'Score + Review' (middle, "
        "orange) -> 'Bridge | Public | Memory' (bottom, split into 3 "
        "small boxes in cyan, blue, orange)\n"
        "4. Four small icon badges in a row near bottom: "
        "'MIT' with a shield, '450+ eps' with headphones, "
        "'Visualizations' with a chart, 'i18n' with a globe\n\n"
        "Style: very dark background (#141414), neon glow accents in "
        "cyan (#5eeacd), electric blue (#8ab4f8), and orange (#ffb347). "
        "Clean flat illustration style. Bold sans-serif typography. "
        "Keep text SHORT — use icons and visuals, not paragraphs. "
        "No podcast host names. No text smaller than 14pt equivalent."
    )
    return generate_episode_image(prompt, output_path, size="1024x1024",
                                  quality="high")


def generate_audio(script, output_dir):
    """Generate TTS audio from script segments, concatenate to MP3."""
    tmpdir = tempfile.mkdtemp(prefix="announcement_")

    # Countdown intro
    print("[Announcement] Generating countdown intro...", file=sys.stderr)
    tts_segment("three", VOICE_A, os.path.join(tmpdir, "countdown_3.mp3"))
    tts_segment("two", VOICE_B, os.path.join(tmpdir, "countdown_2.mp3"))
    tts_segment("one", VOICE_A, os.path.join(tmpdir, "countdown_1.mp3"))
    time.sleep(0.3)

    tts_segment("Welcome to AI Post Transformers!", VOICE_A,
                os.path.join(tmpdir, "welcome_a.mp3"))
    tts_segment("Welcome to AI Post Transformers!", VOICE_B,
                os.path.join(tmpdir, "welcome_b.mp3"))
    time.sleep(0.3)

    # Overlay both welcome voices
    subprocess.run(
        ["ffmpeg", "-y", "-i", os.path.join(tmpdir, "welcome_a.mp3"),
         "-i", os.path.join(tmpdir, "welcome_b.mp3"),
         "-filter_complex",
         "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
         "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "welcome_overlay.mp3")],
        capture_output=True, text=True
    )

    # Short pause
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "0.3", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "short_pause.mp3")],
        capture_output=True, text=True
    )

    # 1-second silence between segments
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "1.0", "-c:a", "libmp3lame", "-q:a", "2",
         os.path.join(tmpdir, "silence.mp3")],
        capture_output=True, text=True
    )

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

    # Generate TTS for each segment
    segment_files = []
    for i, seg in enumerate(script):
        voice = VOICE_A if seg["speaker"] == "A" else VOICE_B
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        print(f"[Announcement] TTS segment {i+1}/{len(script)} "
              f"({seg['speaker']})...", file=sys.stderr)
        tts_segment(seg["text"], voice, seg_path)
        segment_files.append(seg_path)
        time.sleep(0.3)

    # Build concat list
    list_file = os.path.join(tmpdir, "segments.txt")
    with open(list_file, "w") as f:
        for sf in intro_files:
            f.write(f"file '{sf}'\n")
        for sf in segment_files:
            f.write(f"file '{sf}'\n")
            f.write(f"file '{os.path.join(tmpdir, 'silence.mp3')}'\n")

    # Build filename hash
    hash_input = f"{TITLE}{TODAY}"
    hash6 = hashlib.sha256(hash_input.encode()).hexdigest()[:6]
    basename = f"{TODAY}-{SLUG}-{hash6}"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / f"{basename}.mp3"
    txt_path = output_dir / f"{basename}.txt"
    srt_path = output_dir / f"{basename}.srt"
    img_path = output_dir / f"{basename}.png"
    json_path = output_dir / f"{basename}.json"

    # Concatenate
    print("[Announcement] Concatenating audio...", file=sys.stderr)
    finalize_podcast(tmpdir, list_file, str(audio_path))

    # Save transcript
    save_transcript(script, str(txt_path))

    # Save SRT
    generate_srt(script, segment_files, str(srt_path))

    # Save metadata
    metadata = {
        "title": TITLE,
        "date": TODAY,
        "slug": SLUG,
        "description": (
            "Special announcement: AI Post Transformers is now open source "
            "under the MIT license. We introduce our new home at "
            "podcast.do-not-panic.com, interactive paper visualizations, "
            "community paper submissions via GitHub Issues, a deep dive "
            "into our two-pass editorial queue algorithm and its inductive "
            "bias toward memory and storage constraints, and our roadmap "
            "for internationalization."
        ),
        "urls": ["https://github.com/mcgrof/ai-post-transformers"],
        "type": "announcement",
    }
    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"[Announcement] Metadata saved to {json_path}", file=sys.stderr)

    return basename, audio_path, txt_path, srt_path, img_path, json_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    project_root = Path(__file__).parent
    draft_dir = project_root / "drafts" / "2026" / "03"

    # 1) Generate GitHub icon
    github_icon = project_root / "images" / "github.png"
    if not github_icon.exists():
        print("\n=== Generating GitHub icon ===", file=sys.stderr)
        generate_github_icon(str(github_icon))
    else:
        print(f"[Skip] GitHub icon already exists: {github_icon}",
              file=sys.stderr)

    # 2) Generate announcement audio
    print("\n=== Generating announcement audio ===", file=sys.stderr)
    basename, audio, txt, srt, img_path, meta = generate_audio(
        SCRIPT, draft_dir)

    # 3) Generate infographic
    print("\n=== Generating infographic ===", file=sys.stderr)
    generate_infographic(str(img_path))

    # Summary
    print("\n" + "=" * 60, file=sys.stderr)
    print("DONE!", file=sys.stderr)
    print(f"  Audio:      {audio}", file=sys.stderr)
    print(f"  Transcript: {txt}", file=sys.stderr)
    print(f"  Subtitles:  {srt}", file=sys.stderr)
    print(f"  Image:      {img_path}", file=sys.stderr)
    print(f"  Metadata:   {meta}", file=sys.stderr)
    print(f"  GitHub icon: {github_icon}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
