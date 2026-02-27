"""ElevenLabs TTS client for podcast generation using regular text-to-speech API."""
import os
import re
import sys
import time
import requests
import json

BASE_URL = "https://api.elevenlabs.io/v1"

def get_api_key():
    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Set ELEVEN_API_KEY or ELEVENLABS_API_KEY")
    return key

def tts_segment(text, voice_id, output_path):
    """Generate speech for a single segment."""
    key = get_api_key()
    resp = requests.post(
        f"{BASE_URL}/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        },
        stream=True
    )
    if resp.status_code != 200:
        raise RuntimeError(f"TTS failed ({resp.status_code}): {resp.text[:200]}")
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=4096):
            f.write(chunk)
    return output_path

def generate_podcast_script(text, config):
    """Use OpenAI to generate a two-host podcast conversation from paper text."""
    import openai
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        # Try reading from codex auth
        try:
            with open(os.path.expanduser("~/.codex/auth.json")) as f:
                api_key = json.load(f).get("OPENAI_API_KEY")
        except:
            pass
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY for script generation")
    
    client = openai.OpenAI(api_key=api_key)
    
    podcast_config = config.get("podcast", {})
    max_words = podcast_config.get("max_words", 1500)
    
    prompt = f"""Generate a podcast conversation between two hosts discussing this research paper.
Host A is curious and asks good questions. Host B is the expert who explains clearly.
Keep it under {max_words} words. Be conversational, not formal.
Output as JSON array of objects: [{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}]
Only output the JSON array, nothing else.

Paper content:
{text[:8000]}"""

    resp = client.chat.completions.create(
        model=podcast_config.get("llm_model", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    
    script_text = resp.choices[0].message.content.strip()
    # Clean markdown code fences if present
    script_text = re.sub(r"^```json\n?|```$", "", script_text, flags=re.MULTILINE).strip()
    return json.loads(script_text)

def create_podcast(text, config):
    """Create a podcast by generating script + stitching TTS segments."""
    import subprocess
    import tempfile
    
    podcast_config = config.get("podcast", {})
    el_config = config.get("elevenlabs", {})
    
    voice_a = el_config.get("voice_a", "21m00Tcm4TlvDq8ikWAM")  # Rachel
    voice_b = el_config.get("voice_b", "AZnzlk1XvdvUeBnXmlld")  # Domi
    
    print("[Podcast] Generating conversation script...", file=sys.stderr)
    script = generate_podcast_script(text, config)
    print(f"[Podcast] Script has {len(script)} segments", file=sys.stderr)
    
    # Generate TTS for each segment
    tmpdir = tempfile.mkdtemp(prefix="podcast_")
    segment_files = []
    
    for i, seg in enumerate(script):
        voice = voice_a if seg["speaker"] == "A" else voice_b
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
        print(f"[Podcast] TTS segment {i+1}/{len(script)} ({seg['speaker']})...", file=sys.stderr)
        tts_segment(seg["text"], voice, seg_path)
        segment_files.append(seg_path)
        time.sleep(0.3)  # Rate limit courtesy
    
    # Concatenate with ffmpeg
    list_file = os.path.join(tmpdir, "segments.txt")
    with open(list_file, "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")
    
    return tmpdir, list_file, segment_files

def finalize_podcast(tmpdir, list_file, output_path):
    """Concatenate segments into final MP3."""
    import subprocess
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "libmp3lame", "-q:a", "2", output_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[:200]}")
    print(f"[Podcast] Saved to {output_path}", file=sys.stderr)
    return output_path

def download_podcast_audio(project_id, output_path):
    """Compatibility stub - not used in TTS mode."""
    pass
