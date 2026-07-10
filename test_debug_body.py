import sys
import yaml
from pathlib import Path
from verbatim_podcast import create_verbatim_podcast, _load_host_soul_profiles, _extract_script_segments, load_sound_library
import os

with open('config.yaml') as f:
    config = yaml.safe_load(f)

config['podcast']['tts_backend'] = 'kokoro'

script = """
Hal Turing: Are you hearing this Ada!?

Dr. Ada Shannon: Yes I am hearing this, it is pretty good.
""".strip()

soul_profiles = _load_host_soul_profiles()
tmpdir, list_file, segment_files, sources, script_out, intro_audio_files = create_verbatim_podcast(
    script, config, soul_profiles,
    skip_countdown=True,
    skip_theme=True,
)

print(f"[DEBUG] Temp dir: {tmpdir}", file=sys.stderr)
print(f"[DEBUG] Segment files: {segment_files}", file=sys.stderr)
print(f"[DEBUG] List file: {list_file}", file=sys.stderr)

# Check if segments exist
for seg in segment_files:
    if os.path.exists(seg):
        size = os.path.getsize(seg)
        print(f"[DEBUG] {seg}: {size} bytes", file=sys.stderr)
