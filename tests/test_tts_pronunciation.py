"""Pronunciation fix unit tests.

Confirms that TTS-bound text gets technical terms rewritten without
breaking the on-disk transcript content.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elevenlabs_client import _pronounce_for_tts


def test_arxiv_becomes_archive():
    assert _pronounce_for_tts("see arXiv 2401.12345") == "see archive 2401.12345"


def test_arxiv_is_case_insensitive():
    assert _pronounce_for_tts("the ArXiv paper") == "the archive paper"
    assert _pronounce_for_tts("ARXIV preprint") == "archive preprint"


def test_arxiv_word_boundary_only():
    # We don't want to accidentally rewrite a word that just happens
    # to contain the letters
    assert _pronounce_for_tts("hyperarXivism") == "hyperarXivism"
    assert _pronounce_for_tts("arXivers club") == "arXivers club"


def test_llm_acronyms_get_spaced_out():
    assert "L L M" in _pronounce_for_tts("modern LLM serving")
    assert "L L Ms" in _pronounce_for_tts("multiple LLMs running")
    assert "v L L M" in _pronounce_for_tts("vLLM is fast")


def test_gpu_tpu_hbm_get_spaced_out():
    # GPU/TPU end in phonetic "you"/"yous": a literal "G P Us" gets
    # voiced as "gee pee us" by some TTS engines.
    assert "G P you" in _pronounce_for_tts("the GPU was busy")
    assert "G P yous" in _pronounce_for_tts("many GPUs at once")
    assert "T P you" in _pronounce_for_tts("Google's TPU pod")
    assert "H B M" in _pronounce_for_tts("HBM bandwidth")


def test_kv_cache_phrase():
    assert "K V cache" in _pronounce_for_tts("the KV cache eviction")
    assert "K V cache" in _pronounce_for_tts("kv cache")


def test_acronyms_dont_break_normal_words():
    # "all" should stay "all", not get split because of "L L"
    assert _pronounce_for_tts("all the things") == "all the things"
    # "gp" should stay "gp"
    assert _pronounce_for_tts("gpu") == "gpu"  # lowercase is left alone


def test_empty_and_none():
    assert _pronounce_for_tts("") == ""
    assert _pronounce_for_tts(None) is None
