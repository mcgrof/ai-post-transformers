"""OpenAI image generation client for podcast episode cover art."""

import base64
import os
import sys

import requests

BASE_URL = "https://api.openai.com/v1/images/generations"


def _get_openai_api_key():
    """Read OpenAI API key from environment."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY for image generation")
    return key


def generate_episode_image(prompt, output_path, model="gpt-image-1",
                           size="1024x1024", quality="high"):
    """Generate an episode cover image using OpenAI's image generation API.

    Args:
        prompt: Text prompt describing the desired image.
        output_path: Path to write the generated PNG file.
        model: OpenAI image model to use.
        size: Image dimensions (e.g., "1024x1024").
        quality: Image quality ("low", "medium", "high").

    Returns:
        output_path on success, None on failure.
    """
    key = _get_openai_api_key()

    resp = requests.post(
        BASE_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "b64_json",
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Image generation failed ({resp.status_code}): {resp.text[:200]}"
        )

    data = resp.json().get("data", [])
    if not data:
        raise RuntimeError("Image generation returned no data")

    # Handle both b64_json and url response formats
    entry = data[0]
    if "b64_json" in entry:
        image_bytes = base64.b64decode(entry["b64_json"])
    elif "url" in entry:
        img_resp = requests.get(entry["url"], timeout=60)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
    else:
        raise RuntimeError("Unexpected response format: no b64_json or url")

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    print(f"[Image] Generated episode image: {output_path}", file=sys.stderr)
    return output_path
