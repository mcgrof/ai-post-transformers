"""Regression tests for the episode-conclusion gate.

Episodes were shipping that "die mid-thought": the final part got cut at
a segment boundary (a capacity/rate-limit error mid-generation), leaving
the episode ending on an unanswered question or a mid-conversation line
with no host sign-off. The old gate only checked the final part had
segments, so these passed. These tests pin the stronger check.
"""

from elevenlabs_client import _ends_properly, _has_farewell


def _seg(speaker, text):
    return {"speaker": speaker, "text": text}


# --- real failure modes from production drafts --------------------------
def test_ending_on_unanswered_question_is_rejected():
    # Thought Anchors draft ended exactly like this.
    script = [_seg("B", "Here is the setup."),
              _seg("A", "Is that enough to license the framing in the intro?")]
    assert not _ends_properly(script)


def test_ending_on_midconversation_statement_without_farewell_is_rejected():
    # Zhang draft ended on Ada's critical point; Hal never signed off.
    script = [_seg("B", "The labeling pipeline decides everything."),
              _seg("A", "That's not a footnote — it's the foundation "
                        "everything sits on.")]
    assert not _ends_properly(script)


# --- healthy sign-offs from real published episodes ---------------------
def test_real_signoffs_pass():
    for closing in [
        "Thanks for listening, everyone — we'll catch you next time.",
        "That's HyperOffload — thanks for listening, I'm Hal Turing, "
        "that's Dr. Ada Shannon, and we'll catch you next time.",
        "Solid work. Thanks for listening, everyone — take care.",
        "That's it for this one, thanks for listening, and we'll catch "
        "you next time.",
    ]:
        script = [_seg("A", "Some final analysis."), _seg("B", closing)]
        assert _ends_properly(script), closing


def test_has_farewell_detects_thanks_for_listening():
    assert _has_farewell([_seg("B", "Thanks for listening, take care.")])
    assert not _has_farewell([_seg("A", "So the mechanism is subtle.")])


def test_farewell_looked_for_in_last_segments_only():
    # A "thanks for listening" buried early must NOT count as a sign-off.
    script = [_seg("B", "Thanks for listening to the setup — now the meat."),
              _seg("A", "First point."), _seg("B", "Second point."),
              _seg("A", "And a nagging open question about scope.")]
    assert not _ends_properly(script)


def test_truncated_midword_ending_is_rejected():
    script = [_seg("B", "The core idea."),
              _seg("A", "traces back to how they deci")]
    assert not _ends_properly(script)


def test_empty_script_is_not_proper():
    assert not _ends_properly([])


# --- dropped-turn / speaker-continuity detection ------------------------
from elevenlabs_client import _speaker_run_count


def test_alternating_dialogue_has_no_runs():
    script = [_seg("A", "one"), _seg("B", "two"),
              _seg("A", "three"), _seg("B", "four")]
    assert _speaker_run_count(script) == 0


def test_dropped_turn_is_detected():
    # The real episode-584 bug: Hal hands off, then Hal speaks again.
    script = [_seg("B", "Here are the three methods."),
              _seg("A", "Alright — Ada, walk us through it."),
              _seg("A", "So here's what's been nagging me, Ada.")]
    assert _speaker_run_count(script) == 1


def test_multiple_runs_counted():
    script = [_seg("A", "1"), _seg("A", "2"), _seg("A", "3"),
              _seg("B", "4"), _seg("B", "5")]
    assert _speaker_run_count(script) == 3
