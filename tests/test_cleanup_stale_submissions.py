"""Tests for the publish-job-aware staleness classifier.

The old cleanup deleted any approved_for_publish/draft_generated record
whose MP3 was not in drafts/ — which is exactly true for an episode whose
MP3 is mid-move drafts/ -> episodes/ during publish. The classifier must
never delete an in-flight or published episode's submission.
"""

from scripts.cleanup_stale_submissions import _classify_submission

CUTOFF = "2026-06-01T00:00:00+00:00"


def _c(**kw):
    base = dict(
        status="approved_for_publish",
        basename="ep-abc123",
        stem="drafts/2026/06/ep-abc123",
        updated_at="2026-06-10T00:00:00+00:00",  # recent (after cutoff)
        job_states=set(),
        draft_mp3_basenames=set(),
        published_basenames=set(),
        cutoff_date=CUTOFF,
    )
    base.update(kw)
    return _classify_submission(**base)


def test_skip_when_publish_running():
    assert _c(job_states={"publish_running"}) == "skip"


def test_skip_when_publish_claimed():
    assert _c(job_states={"publish_claimed"}) == "skip"


def test_skip_when_publish_job_approved():
    assert _c(job_states={"approved_for_publish"}) == "skip"


def test_advance_when_publish_completed():
    assert _c(job_states={"publish_completed"}) == "advance"


def test_advance_when_episode_already_published():
    assert _c(published_basenames={"ep-abc123"}) == "advance"


def test_skip_when_still_a_live_draft():
    assert _c(draft_mp3_basenames={"ep-abc123"}) == "skip"


def test_delete_true_orphan_when_old():
    # no publish job, no episode, no draft MP3, older than grace window
    assert _c(updated_at="2026-05-01T00:00:00+00:00") == "delete"


def test_skip_true_orphan_when_recent():
    # same as above but recent -> grace, don't delete yet
    assert _c(updated_at="2026-06-10T00:00:00+00:00") == "skip"


def test_delete_no_draft_stem_when_old():
    assert _c(basename="", stem="", updated_at="2026-05-01T00:00:00+00:00") == "delete"


def test_active_beats_completed_protects_inflight():
    # a re-publish in flight (running) must win over a prior completed job
    assert _c(job_states={"publish_running", "publish_completed"}) == "skip"


def test_never_advance_rejected_even_if_episode_published():
    # a rejected submission whose stem collides with a published episode
    # must NEVER be advanced to published
    assert _c(status="rejected", published_basenames={"ep-abc123"}) == "skip"
    assert _c(status="rejected", job_states={"publish_completed"}) == "skip"


def test_skip_published_status_is_left_alone():
    assert _c(status="published", published_basenames={"ep-abc123"}) == "skip"


def test_skip_generation_failed_even_when_old():
    assert _c(
        status="generation_failed", updated_at="2026-05-01T00:00:00+00:00"
    ) == "skip"
