"""Upload all draft MP3s to R2 so dashboard play buttons work.
Maps episode IDs to their draft MP3 files by matching DB entries
against files on disk, then uploads missing ones to R2."""
import json, sqlite3, os, glob, subprocess

PODCAST_DIR = os.path.expanduser("~/devel/ai-post-transformers")
DRAFT_DIR = os.path.join(PODCAST_DIR, "drafts/2026/03")
R2_TOKEN = "WP4STS_5CtR36JFgn8cIGR6dJP_ITtv0WopQVosm"
ACCOUNT_ID = "c6cd84b0ad169e7e8e46f41ee960024a"
BUCKET = "dash"

# Published IDs that don't need draft uploads
published_ids = {15, 16, 23, 29, 30, 31, 32, 34, 35, 37, 38, 39, 40, 41, 45, 46}

db_path = os.path.join(PODCAST_DIR, "papers.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get column names properly
cursor = conn.execute("SELECT * FROM podcasts LIMIT 1")
col_names = [desc[0] for desc in cursor.description]
print("DB columns:", col_names)

# Get all draft episodes
rows = conn.execute("SELECT * FROM podcasts ORDER BY id DESC").fetchall()

# Get all MP3 files in drafts
mp3_files = sorted(glob.glob(os.path.join(DRAFT_DIR, "*.mp3")))
print("Draft MP3s on disk:", len(mp3_files))

# Build a map: for each draft episode, find its MP3 by checking
# if any column contains the file stem, or by matching title patterns
for r in rows:
    ep_id = r["id"]
    if ep_id in published_ids:
        continue

    # Check if there's an audio_file or similar column
    mp3_path = None
    for col in col_names:
        val = r[col]
        if isinstance(val, str) and val.endswith(".mp3"):
            # Found a direct MP3 reference
            candidate = os.path.join(PODCAST_DIR, val)
            if os.path.exists(candidate):
                mp3_path = candidate
                break
            # Try in drafts dir
            candidate = os.path.join(DRAFT_DIR, os.path.basename(val))
            if os.path.exists(candidate):
                mp3_path = candidate
                break

    if not mp3_path:
        # Try matching by scanning MP3 filenames for title keywords
        title_words = r["title"].lower().split()[:3]
        for mp3 in mp3_files:
            bn = os.path.basename(mp3).lower()
            matches = sum(1 for w in title_words if len(w) > 3 and w in bn)
            if matches >= 2:
                mp3_path = mp3
                break

    if mp3_path:
        r2_key = "drafts/ep%d.mp3" % ep_id
        size_mb = os.path.getsize(mp3_path) / (1024*1024)
        print("ep%d: %s (%.1f MB) -> %s" % (ep_id, os.path.basename(mp3_path), size_mb, r2_key))

        # Upload via curl
        result = subprocess.run([
            "curl", "-s", "-X", "PUT",
            "https://api.cloudflare.com/client/v4/accounts/%s/r2/buckets/%s/objects/%s" % (ACCOUNT_ID, BUCKET, r2_key),
            "-H", "Authorization: Bearer %s" % R2_TOKEN,
            "-H", "Content-Type: audio/mpeg",
            "--data-binary", "@%s" % mp3_path
        ], capture_output=True, text=True, timeout=120)
        try:
            resp = json.loads(result.stdout)
            print("  -> success:", resp.get("success"))
        except:
            print("  -> upload sent (no JSON response)")
    else:
        print("ep%d: NO MP3 FOUND for '%s'" % (ep_id, r["title"][:50]))
