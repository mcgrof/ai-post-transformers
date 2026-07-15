"""Load and manage SOUL personality layers."""

import yaml
from pathlib import Path
from typing import Dict, Any


SOUL_CORE_PATH = Path(__file__).parent / "SOUL_CORE.yaml"
SOUL_LENS_POLICY_PATH = Path(__file__).parent / "SOUL_LENS_POLICY.yaml"
SOUL_CONVERSATION_POLICY_PATH = Path(__file__).parent / "SOUL_CONVERSATION_POLICY.yaml"
SOUL_VOICE_REALIZATION_PATH = Path(__file__).parent / "SOUL_VOICE_REALIZATION.yaml"


def load_soul_layer(layer_path: Path) -> Dict[str, Any]:
    """Load a SOUL layer YAML file."""
    if not layer_path.exists():
        return {}
    with open(layer_path) as f:
        return yaml.safe_load(f) or {}


def get_soul_core(character: str) -> Dict[str, Any]:
    """Get SOUL_CORE for a character."""
    core = load_soul_layer(SOUL_CORE_PATH)
    return core.get(character, {})


def get_soul_lens_policy(character: str) -> Dict[str, Any]:
    """Get SOUL_LENS_POLICY for a character."""
    lens = load_soul_layer(SOUL_LENS_POLICY_PATH)
    return lens.get(character, {})


def get_soul_conversation_policy(character: str) -> Dict[str, Any]:
    """Get SOUL_CONVERSATION_POLICY for a character."""
    conv = load_soul_layer(SOUL_CONVERSATION_POLICY_PATH)
    return conv.get(character, {})


def get_soul_voice_realization(character: str) -> Dict[str, Any]:
    """Get SOUL_VOICE_REALIZATION for a character."""
    voice = load_soul_layer(SOUL_VOICE_REALIZATION_PATH)
    return voice.get(character, {})


def get_soul_profile(character: str) -> Dict[str, Any]:
    """Get complete SOUL profile for a character (all 4 layers)."""
    return {
        "core": get_soul_core(character),
        "lens_policy": get_soul_lens_policy(character),
        "conversation_policy": get_soul_conversation_policy(character),
        "voice_realization": get_soul_voice_realization(character),
    }


def describe_character_appraisal(character: str) -> str:
    """Generate a one-sentence description of how a character appraises evidence."""
    profile = get_soul_profile(character)
    core = profile.get("core", {})
    lens = profile.get("lens_policy", {})

    appraisal = lens.get("appraisal_patterns", [])
    if not appraisal:
        return f"{character} evaluates evidence carefully."

    first_pattern = appraisal[0]
    return f"{character}: {first_pattern}"


def build_system_prompt_segment(character: str) -> str:
    """Build the SOUL-informed system prompt segment for a character.

    This is used during LLM generation to steer the model toward
    character-authentic behavior.
    """
    profile = get_soul_profile(character)
    core = profile.get("core", {})
    lens = profile.get("lens_policy", {})

    identity = core.get("identity", "")
    values = core.get("enduring_values", [])
    blind_spots = core.get("blind_spots", [])
    standards = core.get("standards_of_evidence", [])
    what_notices = lens.get("what_they_notice_first", [])
    appraisals = lens.get("appraisal_patterns", [])

    prompt = f"""
You are {character}. Your identity: {identity}

Your enduring values:
{chr(10).join('- ' + v for v in values)}

Your blind spots (areas you tend to miss):
{chr(10).join('- ' + b for b in blind_spots)}

Your standards of evidence:
{chr(10).join('- ' + s for s in standards)}

What you notice first about research:
{chr(10).join('- ' + n for n in what_notices)}

How you appraise evidence:
{chr(10).join('- ' + a for a in appraisals)}

Stay true to this character when responding. Don't explain who you are,
just be it. Think the way {character} thinks.
"""
    return prompt


def get_voice_guidance(character: str) -> str:
    """Get voice and tone guidance for a character."""
    profile = get_soul_profile(character)
    voice = profile.get("voice_realization", {})

    cadence = voice.get("cadence_and_pacing", [])
    vocab = voice.get("vocabulary_preferences", {})

    guidance = f"""
Voice guidance for {character}:

Cadence and pacing:
{chr(10).join('- ' + c for c in cadence)}

Vocabulary preferences:
- Favors: {', '.join(vocab.get('Favors', []) if isinstance(vocab, dict) and 'Favors' in str(vocab) else [])}
- Avoids: {', '.join(vocab.get('Avoids', []) if isinstance(vocab, dict) and 'Avoids' in str(vocab) else [])}
"""
    return guidance
