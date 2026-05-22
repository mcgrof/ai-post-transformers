"""Regression tests for the TTS pronunciation rewrite layer in
elevenlabs_client._pronounce_for_tts.

Rewrites happen at the TTS boundary only — the transcript .txt,
SRT, and script JSON on disk keep the original spelling. We test
the produced text directly so the test does not depend on having
an actual TTS engine available.
"""

from elevenlabs_client import _pronounce_for_tts


def test_gpus_plural_renders_as_yous_not_us():
    """Original bug: 'G P Us' was read as 'gee pee us' (uhs sound)
    instead of 'gee pee yooz'. The fix uses the phonetic word
    'yous' so the audio is consistent across ElevenLabs and Kokoro.
    """
    assert _pronounce_for_tts("modern GPUs are expensive") == (
        "modern G P yous are expensive"
    )


def test_gpu_singular_renders_as_you_not_uh():
    assert _pronounce_for_tts("a single GPU runs the model") == (
        "a single G P you runs the model"
    )


def test_gpu_possessive_renders_like_plural():
    assert _pronounce_for_tts("the GPU's memory bandwidth") == (
        "the G P yous memory bandwidth"
    )


def test_tpu_variants_get_the_same_phonetic_treatment():
    assert _pronounce_for_tts("TPUs and GPUs differ") == (
        "T P yous and G P yous differ"
    )
    assert _pronounce_for_tts("on a TPU pod") == "on a T P you pod"
    assert _pronounce_for_tts("the TPU's HBM") == "the T P yous H B M"


def test_gpu_with_trailing_hyphen_compound_still_rewrites():
    """\\bGPU\\b matches before a hyphen so compounds like
    "GPU-rich" still get the corrected pronunciation."""
    assert _pronounce_for_tts("GPU-rich operators") == "G P you-rich operators"


def test_pronounce_preserves_unrelated_text():
    """The rewriter must not touch text without GPU/TPU acronyms."""
    text = "We trained for 100 hours on a cluster."
    assert _pronounce_for_tts(text) == text


def test_pronounce_does_not_touch_substring_matches():
    """\\bGPU\\b must NOT rewrite 'GPUSEC' or other longer
    identifiers that happen to contain GPU as a substring."""
    assert _pronounce_for_tts("GPUSEC vendors") == "GPUSEC vendors"
    assert _pronounce_for_tts("AGPU is not the same") == "AGPU is not the same"


def test_pronounce_handles_empty_and_none():
    assert _pronounce_for_tts("") == ""
    assert _pronounce_for_tts(None) is None


def test_other_existing_acronym_rules_still_apply():
    """Sanity-check that we didn't break the rest of the table."""
    assert "L L M" in _pronounce_for_tts("LLM workloads")
    assert "L L Ms" in _pronounce_for_tts("frontier LLMs")
    assert "archive" in _pronounce_for_tts("see arXiv 2402.01234")
