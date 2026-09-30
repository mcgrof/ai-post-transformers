"""Keep optional overlapping speech bounded across the assembled episode."""

from copy import deepcopy
import re
from unittest.mock import Mock

import pytest

import db
import elevenlabs_client as client
import fun_facts


def _segments(speakers="ABABAB"):
    return [
        {"speaker": speaker, "text": f"Complete thought {index}.",
         "interrupt": True, "source": {"part": index}}
        for index, speaker in enumerate(speakers)
    ]


def _interrupt_indices(script):
    return [index for index, segment in enumerate(script)
            if segment.get("interrupt")]


def test_empty_script_has_no_interrupts():
    assert client._limit_interrupts([], 1) == []


@pytest.mark.parametrize("limit", [0, -1, -10])
def test_disabled_interrupts_preserve_dialogue(limit):
    original = _segments()
    result = client._limit_interrupts(original, limit)

    assert _interrupt_indices(result) == []
    assert [(s["speaker"], s["text"]) for s in result] == [
        (s["speaker"], s["text"]) for s in original
    ]


def test_invalid_handoffs_do_not_consume_interrupt_budget():
    # The first two marks have no other speaker to interrupt. A retained
    # interrupt at index 2 suppresses index 3, but not the next handoff.
    result = client._limit_interrupts(_segments("AABABA"), 2)

    assert _interrupt_indices(result) == [2, 4]


@pytest.mark.parametrize("limit, expected", [(1, [1]), (2, [1, 3])])
def test_episode_budget_caps_separated_interruptions(limit, expected):
    assert _interrupt_indices(client._limit_interrupts(_segments(), limit)) == expected


def test_normalization_copies_segments_without_changing_source_or_metadata():
    original = _segments()
    original[2]["interrupt"] = False
    original[4].pop("interrupt")
    before = deepcopy(original)

    result = client._limit_interrupts(original, 1)

    assert original == before
    assert result is not original
    for actual, source in zip(result, original):
        assert actual is not source
        assert {k: v for k, v in actual.items() if k != "interrupt"} == {
            k: v for k, v in source.items() if k != "interrupt"
        }
    assert result[2]["interrupt"] is False
    assert "interrupt" not in result[4]
    assert _interrupt_indices(result) == [1]


@pytest.mark.parametrize("parts, topic_count", [(2, 1), (3, 9), (4, 15)])
@pytest.mark.parametrize("limit", [0, 1])
def test_generation_caps_interrupts_after_editor_and_closing(
    monkeypatch, tmp_path, parts, topic_count, limit
):
    """An over-eager LLM cannot multiply the budget at each generation stage."""
    monkeypatch.chdir(tmp_path)
    stages = []
    generated = []
    edited = []
    closing = [
        {"speaker": "A", "text": "Only the measured workload supports that claim.",
         "interrupt": True},
        {"speaker": "B", "text": "Thanks for listening.", "interrupt": True},
    ]

    def fake_llm(backend, model, prompt, **kwargs):
        # Check policy propagation, without freezing its wording in this test.
        assert client._DIALOGUE_QUALITY_RULES in prompt
        if prompt.startswith("You are planning a podcast episode"):
            stages.append("plan")
            return {"core_research_question": "Does the method reduce latency?"}
        if prompt.startswith("Generate PART "):
            part, requested_parts = map(
                int, re.match(r"Generate PART (\d+) of (\d+)", prompt).groups()
            )
            assert requested_parts == parts
            stages.append(f"part_{part}")
            segments = _segments("ABABABAB")
            for index, segment in enumerate(segments):
                segment["text"] = f"Part {part}, thought {index}, with its evidence."
            generated.extend(deepcopy(segments))
            return segments
        if prompt.startswith("You are a podcast script editor"):
            stages.append("editor")
            # Every editor output gets a new overlap mark, even if an earlier
            # stage filtered them. The wording proves this edit was accepted.
            edited.extend({**s, "text": f"Edited: {s['text']}", "interrupt": True}
                          for s in generated)
            return deepcopy(edited)
        if prompt.startswith("The following podcast conversation was cut off"):
            stages.append("closing")
            return deepcopy(closing)
        raise AssertionError(f"Unexpected LLM call: {prompt[:80]}")

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline dialogue test attempted an external operation")

    monkeypatch.setattr(client, "get_llm_backend", lambda config: {"type": "mock"})
    monkeypatch.setattr(client, "llm_call", fake_llm)
    monkeypatch.setattr(client, "_topic_classification_pass", Mock(return_value={
        "topics": [{"name": f"topic {i}", "is_new": False}
                   for i in range(topic_count)],
        "authors": [], "title": "Synthetic fixture", "institutions": [],
    }))
    monkeypatch.setattr(client, "_concept_analysis_pass", Mock(return_value={
        "critical_questions": [], "additional_references": [],
    }))
    monkeypatch.setattr(client, "_load_host_soul_profiles", lambda: {})
    monkeypatch.setattr(fun_facts, "get_podcast_context", lambda: {})
    for name in ("get_connection", "init_db", "get_episode_count", "mark_facts_used"):
        monkeypatch.setattr(db, name, forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)

    script, sources, topics, _ = client.generate_podcast_script(
        "Synthetic fixture: latency was lower on one measured workload.",
        {"podcast": {"max_words": 1000, "min_script_words": 0,
                     "dynamics": {"interrupt_per_episode": limit,
                                  "disagreement_every_n": 0}}},
        covered_topics=set(),
    )

    assert stages == ["plan"] + [f"part_{i}" for i in range(1, parts + 1)] + [
        "editor", "closing"
    ]
    assert [(s["speaker"], s["text"]) for s in script] == [
        (s["speaker"], s["text"]) for s in edited + closing
    ]
    assert _interrupt_indices(script) == ([1] if limit else [])
    assert client._ends_properly(script)
    assert sources == []
    assert len(topics) == topic_count
