#!/usr/bin/env python3
"""
Submit the SOUL.md or Severance special episode via the admin submission API.

This reads the full episode outline and submits it as a special-instructions podcast.
"""

import json
import os
from pathlib import Path

def submit_soul_episode():
    # Read the episode outline
    outline_path = Path(__file__).parent / "EPISODE_OUTLINE_SOUL_OR_SEVERANCE.md"

    with open(outline_path, "r") as f:
        script_content = f.read()

    # Prepare submission payload
    submission = {
        "urls": ["https://internal.do-not-panic.com/soul-md-or-severance"],  # Placeholder URL for special episode
        "instructions": script_content,  # Full script as special instructions
        "visibility": "public"  # Public episode
    }

    print("=" * 80)
    print("SOUL.md or Severance Episode Submission")
    print("=" * 80)
    print(f"\nSubmission payload:")
    print(f"  URLs: {submission['urls']}")
    print(f"  Instructions length: {len(script_content)} characters")
    print(f"  Visibility: {submission['visibility']}")
    print(f"\nFirst 300 chars of script:")
    print(script_content[:300] + "...")
    print("\n" + "=" * 80)
    print("To submit this episode via the admin UI:")
    print("1. Go to the Submit Papers page")
    print("2. Paste this in 'Paper URLs':")
    print(f"   {submission['urls'][0]}")
    print("3. Paste the full EPISODE_OUTLINE_SOUL_OR_SEVERANCE.md script in 'Special Instructions'")
    print("4. Leave visibility as 'Public'")
    print("5. Click 'Submit Papers'")
    print("\nThe system will pass your script as --goal to the generation pipeline.")
    print("=" * 80)

    # Save payload for reference
    payload_path = Path(__file__).parent / "soul_episode_submission.json"
    with open(payload_path, "w") as f:
        json.dump(submission, f, indent=2)

    print(f"\nPayload saved to: {payload_path}")

    return submission

if __name__ == "__main__":
    submit_soul_episode()
    print("\n✅ Submission data prepared. Use the admin UI to submit.")
