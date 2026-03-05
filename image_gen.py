"""Episode cover art generation via OpenAI gpt-image-1 API."""

import base64
import json
import os
import sys


def _get_openai_api_key():
    """Get OpenAI API key from environment or auth file."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.codex/auth.json")) as f:
            key = json.load(f).get("OPENAI_API_KEY")
    except Exception:
        pass
    return key


def generate_episode_image(prompt, output_path, model=None,
                           size="1024x1024", quality=None, config=None):
    """Generate an episode cover image via OpenAI image API.

    Args:
        prompt: Text prompt describing the desired infographic.
        output_path: Path to write the generated PNG file.
        model: OpenAI image model (default: gpt-image-1).
        size: Image dimensions (default: 1024x1024).
        quality: Image quality tier (default: medium).
        config: Application config dict.

    Returns:
        output_path on success, None on failure.
    """
    key = _get_openai_api_key()
    if not key:
        print("[Image] Skipping: OPENAI_API_KEY not set", file=sys.stderr)
        return None

    import openai
    client = openai.OpenAI(api_key=key)

    if model is None:
        model = "gpt-image-1"
    if quality is None:
        quality = "medium"

    try:
        print(f"[Image] Generating via {model} ({quality}, {size})...",
              file=sys.stderr)
        result = client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            size=size,
            quality=quality,
        )

        # gpt-image-1 returns base64
        image_data = result.data[0]
        if hasattr(image_data, "b64_json") and image_data.b64_json:
            img_bytes = base64.b64decode(image_data.b64_json)
            with open(output_path, "wb") as f:
                f.write(img_bytes)
        elif hasattr(image_data, "url") and image_data.url:
            import urllib.request
            urllib.request.urlretrieve(image_data.url, output_path)
        else:
            print("[Image] No image data in response", file=sys.stderr)
            return None

        fsize = os.path.getsize(output_path)
        print(f"[Image] Generated: {output_path} ({fsize // 1024}KB)",
              file=sys.stderr)
        return output_path

    except Exception as e:
        print(f"[Image] Generation failed: {e}", file=sys.stderr)
        return None
