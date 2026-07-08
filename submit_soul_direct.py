#!/usr/bin/env python3
"""
Submit SOUL.md or Severance episode directly via admin submission API.

Uses the proper metadata structure so gen-podcast.py uses the script
as fallback_source_text instead of analyzing a paper.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def submit_soul_episode():
    # Read the script
    script_path = ROOT / "EPISODE_OUTLINE_SOUL_OR_SEVERANCE.md"
    with open(script_path) as f:
        script = f.read()

    placeholder_url = "https://internal.do-not-panic.com/soul-md-or-severance"

    # Build proper submission structure with fallback_source_text
    submission = {
        "urls": [placeholder_url],
        "instructions": script,  # Also pass as instructions for LLM steering
        "metadata": {
            placeholder_url: {
                "fallback_source_text": script,  # THIS is the key - tells worker to use script instead of fetching URL
                "title": "SOUL.md or Severance",
            }
        },
        "visibility": "public"
    }

    print("=" * 80)
    print("SOUL.md or Severance Episode Submission")
    print("=" * 80)
    print(f"\nSubmission structure:")
    print(f"  URLs: {submission['urls']}")
    print(f"  Instructions length: {len(script)} chars")
    print(f"  Metadata fallback_source_text: {len(script)} chars")
    print(f"  Visibility: {submission['visibility']}")
    print("\n" + "=" * 80)

    # Save for reference
    with open(ROOT / "soul_submission.json", "w") as f:
        json.dump(submission, f, indent=2)

    print("\nSubmission payload saved to: soul_submission.json")
    print("\nTo submit via admin API (if you have CF Access credentials):")
    print("  python -c \"import json; print(json.load(open('soul_submission.json')))\" | \\")
    print("    curl -X POST https://admin.podcast.do-not-panic.com/api/submit \\")
    print("    -H 'Content-Type: application/json' \\")
    print("    -d @- ")
    print("\nOR use the web form at:")
    print("  https://admin.podcast.do-not-panic.com/drafts")
    print("\nThe key is: metadata[url].fallback_source_text tells gen-podcast.py")
    print("to use your script instead of downloading/analyzing the paper.")

    return submission

if __name__ == "__main__":
    submit_soul_episode()
