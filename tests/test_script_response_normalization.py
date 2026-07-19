"""Regression tests for LLM script-response shape normalization.

Models occasionally return a single segment object instead of an
array, or wrap the segment array under a key other than "script".
The old code discarded those responses entirely, which surfaced as
"Part N generation failed: None" and missing-conclusion failures in
the submission queue.
"""

from elevenlabs_client import _normalize_script_response


SEG_A = {"speaker": "A", "text": "Welcome to the show."}
SEG_B = {"speaker": "B", "text": "Glad to be here."}


def test_plain_list_passes_through():
    assert _normalize_script_response([SEG_A, SEG_B]) == [SEG_A, SEG_B]


def test_list_filters_non_segments():
    raw = [SEG_A, "junk", {"speaker": "B"}, {"text": "no speaker"}, SEG_B]
    assert _normalize_script_response(raw) == [SEG_A, SEG_B]


def test_single_segment_dict_is_wrapped():
    # The exact failure seen in production: the model returned one
    # segment object instead of an array and the whole part vanished.
    assert _normalize_script_response(SEG_A) == [SEG_A]


def test_script_key_wrapper():
    assert _normalize_script_response({"script": [SEG_A, SEG_B]}) == [SEG_A, SEG_B]


def test_alternate_key_wrapper():
    assert _normalize_script_response({"dialogue": [SEG_A]}) == [SEG_A]
    assert _normalize_script_response({"segments": [SEG_B]}) == [SEG_B]


def test_wrapper_with_junk_items_filters():
    raw = {"script": [SEG_A, {"speaker": 1, "text": 2}, SEG_B]}
    assert _normalize_script_response(raw) == [SEG_A, SEG_B]


def test_empty_and_unusable_shapes_return_empty():
    assert _normalize_script_response([]) == []
    assert _normalize_script_response({}) == []
    assert _normalize_script_response({"script": []}) == []
    assert _normalize_script_response("just prose") == []
    assert _normalize_script_response(None) == []


def test_whitespace_only_text_is_not_a_segment():
    assert _normalize_script_response([{"speaker": "A", "text": "   "}]) == []
