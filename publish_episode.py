#!/usr/bin/env python3
"""Publish a draft podcast episode: copy to public/, update DB paths and publish_date."""

import argparse
import os
import shutil
import sqlite3
from datetime import datetime, timezone


def publish(episode_id, db_path="papers.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM podcasts WHERE id = ?", (episode_id,)).fetchone()
    if not row:
        raise ValueError(f"Episode ID {episode_id} not found")

    row = dict(row)
    audio = row.get("audio_file", "")
    image = row.get("image_file", "")

    if "/drafts/" not in (audio or ""):
        print(f"Episode {episode_id} is already published (not in drafts/)")
        conn.close()
        return row

    # Determine public path: drafts/YYYY/MM/... -> public/YYYY/MM/...
    public_audio = audio.replace("/drafts/", "/public/")
    public_dir = os.path.dirname(public_audio)
    os.makedirs(public_dir, exist_ok=True)

    # Copy files
    base = os.path.splitext(audio)[0]
    public_base = os.path.splitext(public_audio)[0]
    copied = []
    for ext in [".mp3", ".png", ".srt", ".json", ".txt"]:
        src = base + ext
        dst = public_base + ext
        if os.path.exists(src):
            shutil.copy2(src, dst)
            copied.append(ext)

    print(f"Copied {', '.join(copied)} to {public_dir}/")

    # Update DB: paths and publish_date to TODAY
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updates = {"audio_file": public_audio, "publish_date": today}
    if image and "/drafts/" in image:
        updates["image_file"] = image.replace("/drafts/", "/public/")

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [episode_id]
    conn.execute(f"UPDATE podcasts SET {set_clause} WHERE id = ?", values)
    conn.commit()

    print(f"DB updated: publish_date={today}, audio → public/")

    # Re-fetch
    row = dict(conn.execute("SELECT * FROM podcasts WHERE id = ?", (episode_id,)).fetchone())
    conn.close()
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish a draft episode")
    parser.add_argument("episode_id", type=int, help="Episode ID to publish")
    parser.add_argument("--db", default="papers.db", help="Database path")
    args = parser.parse_args()

    row = publish(args.episode_id, args.db)
    print(f"\nReady for R2 upload:")
    print(f"  Title: {row['title']}")
    print(f"  Audio: {row['audio_file']}")
    print(f"  Date:  {row['publish_date']}")
