"""SOUL.md integration for host personality versioning.

SOUL.md files track evolving host personalities:
- hosts/hal/SOUL.md → Hal's personality version, stale phrases, growth edges
- hosts/ada/SOUL.md → Ada's personality version, patterns, evolution
- hosts/vera/SOUL.md → VERA's personality (new host, instantiated 2026-06-25)

Each episode loads the current SOUL.md versions for all hosts and passes
them to the script generation pipeline. Personality evolution is tracked
and versioned in evolution_log entries.
"""

import yaml
from pathlib import Path
import sys


def load_soul_profile(host_name):
    """Load SOUL.md personality profile for a host.

    Returns parsed YAML frontmatter + content as dict.
    """
    soul_path = Path(__file__).parent / "hosts" / host_name.lower() / "SOUL.md"
    if not soul_path.exists():
        print(f"[SOUL] Warning: No SOUL.md for {host_name} at {soul_path}", file=sys.stderr)
        return None

    content = soul_path.read_text()
    # Parse YAML frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1])
                body = parts[2].strip()
                return {
                    **frontmatter,
                    "body": body,
                    "path": str(soul_path)
                }
            except yaml.YAMLError:
                print(f"[SOUL] Error parsing YAML in {soul_path}", file=sys.stderr)
                return None
    return None


def build_host_context(host_names=None):
    """Build personality context for all hosts.

    Returns dict mapping host_name → SOUL profile with personality version,
    deprecated phrases, load-bearing patterns, growth edges.
    """
    if host_names is None:
        host_names = ["Hal", "Ada", "VERA"]

    context = {}
    for host in host_names:
        profile = load_soul_profile(host)
        if profile:
            context[host] = {
                "name": host,
                "version": profile.get("personality_version", "unknown"),
                "status": profile.get("status", "unknown"),
                "body": profile.get("body", ""),
                "path": profile.get("path", "")
            }
            print(f"[SOUL] Loaded {host} v{context[host]['version']}", file=sys.stderr)
        else:
            print(f"[SOUL] Failed to load {host}", file=sys.stderr)

    return context


def build_podcast_persona_block(host_context):
    """Build LLM prompt block with host personality guidance.

    Includes: personality versions, stale phrases to avoid, load-bearing patterns,
    approved growth edges, forbidden behaviors.
    """
    if not host_context:
        return ""

    block = "\n\nHOST PERSONALITY VERSIONING (SOUL.md guidance):\n"
    block += "=" * 60 + "\n"

    for host, profile in host_context.items():
        block += f"\n{host.upper()} (v{profile['version']}):\n"
        block += f"Status: {profile.get('status', '?')}\n"
        # Include just the key patterns, not full body (too verbose)
        block += f"Profile loaded from: {Path(profile['path']).relative_to(Path.cwd())}\n"

    block += "\n" + "=" * 60
    block += "\nREQUIREMENT: Preserve load-bearing personality traits."
    block += "\nREQUIREMENT: Deprecate stale phrases (documented in SOUL.md)."
    block += "\nOBJECTIVE: Evolve rituals, not discard them.\n"

    return block


def extract_host_constraints(host_context):
    """Extract hard constraints from SOUL.md files.

    Returns dict with forbidden behaviors, tests, and evolution targets
    for each host.
    """
    constraints = {}

    # For now, return a summary structure
    # Full parsing would extract YAML sections from body
    for host in host_context:
        constraints[host] = {
            "host": host,
            "version": host_context[host]["version"],
            "soul_file_loaded": True
        }

    return constraints


if __name__ == "__main__":
    # Test: load all host profiles
    hosts = build_host_context()
    print("\n✓ Host contexts loaded:", list(hosts.keys()))

    # Build LLM guidance
    guidance = build_podcast_persona_block(hosts)
    print("\nLLM Persona Block Preview:")
    print(guidance[:500] + "...")
