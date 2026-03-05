"""Cloudflare R2 upload utilities for podcast publishing."""

import os
import sys

import boto3


def _isatty():
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _c(code, text):
    if not _isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def get_r2_client():
    """Create an S3-compatible client for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload_file(client, local_path, r2_key, content_type=None, bucket=None):
    """Upload a file to R2.

    Args:
        client: boto3 S3 client.
        local_path: Path to the local file.
        r2_key: Object key in R2 (e.g., 'episodes/my-episode.mp3').
        content_type: MIME type override.
        bucket: Bucket name (defaults to R2_BUCKET env var).

    Returns:
        The public URL of the uploaded file.
    """
    bucket = bucket or os.environ.get("R2_BUCKET", "ai-post-transformers")
    base_url = os.environ.get("PODCAST_BASE_URL", "https://podcast.do-not-panic.com")

    extra_args = {}
    if content_type:
        extra_args["ContentType"] = content_type

    # Auto-detect content type
    if not content_type:
        ext = os.path.splitext(local_path)[1].lower()
        ct_map = {
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".srt": "text/plain",
            ".txt": "text/plain",
            ".xml": "application/xml",
            ".json": "application/json",
        }
        if ext in ct_map:
            extra_args["ContentType"] = ct_map[ext]

    file_size = os.path.getsize(local_path)
    size_str = f"{file_size / 1024 / 1024:.1f} MB"
    print(f"{_c('36', '[R2]')} Uploading {_c('1', r2_key)} "
          f"{_c('2', f'({size_str})')}...", file=sys.stderr)

    client.upload_file(local_path, bucket, r2_key, ExtraArgs=extra_args)

    url = f"{base_url.rstrip('/')}/{r2_key}"
    print(f"{_c('36', '[R2]')} {_c('32', '→')} {_c('2', url)}",
          file=sys.stderr)
    return url


def _episode_r2_prefix(filename):
    """R2 key prefix for episode files. All episodes live flat under episodes/."""
    return "episodes"


def publish_episode(audio_file, image_file=None, srt_file=None):
    """Upload all episode artifacts to R2.

    Files are organized under episodes/YYYY/MM/ based on the
    episode date embedded in the filename.

    Args:
        audio_file: Path to the MP3 file.
        image_file: Path to the PNG cover art (optional).
        srt_file: Path to the SRT transcript (optional).

    Returns:
        Dict of uploaded URLs keyed by type.
    """
    client = get_r2_client()
    urls = {}

    audio_basename = os.path.basename(audio_file)
    prefix = _episode_r2_prefix(audio_basename)

    # Upload audio
    urls["audio"] = upload_file(
        client, audio_file, f"{prefix}/{audio_basename}"
    )

    # Upload cover art
    if image_file and os.path.exists(image_file):
        urls["image"] = upload_file(
            client, image_file,
            f"{prefix}/{os.path.basename(image_file)}"
        )

    # Upload transcript
    if srt_file and os.path.exists(srt_file):
        urls["transcript"] = upload_file(
            client, srt_file,
            f"{prefix}/{os.path.basename(srt_file)}"
        )

    return urls


def upload_feed(feed_file):
    """Upload the RSS feed XML to R2 root.

    Args:
        feed_file: Path to the local feed.xml.

    Returns:
        Public URL of the feed.
    """
    client = get_r2_client()
    return upload_file(client, feed_file, "feed.xml", content_type="application/xml")
