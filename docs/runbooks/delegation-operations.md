# Delegation Operations Runbook

This runbook is for the operator handling volunteer onboarding, polling,
claims, overrides, retries, and recovery.

Architecture context lives in
[`docs/delegation-architecture.md`](/home/mcgrof/devel/ai-post-transformers/docs/delegation-architecture.md).

## Start Here

Before you touch a live queue, answer these:

- Where is the authoritative manifest stored right now?
- Are you looking at the latest manifest `version`?
- Do you know which volunteers are approved?
- Do you know whether the job should be released or failed?
- Do you know whether an override is justified?

If any answer is "no", stop and refresh state first.

## Queue Triage Loop

Run this loop whenever the queue stalls:

1. Export a fresh snapshot.
2. Check `version`, queued jobs, claimed jobs, and failed jobs.
3. Check volunteers with high `active_claims`.
4. Look for locale or capability mismatches.
5. Decide one action only: approve, reassign, release, fail, or override.

## Operator Decision Table

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Claim rejected with stale version | another actor updated queue | reload snapshot and retry |
| Claim rejected for approval | volunteer not approved | approve only after identity check |
| Claim rejected for capability | volunteer manifest is wrong or job is mis-tagged | fix metadata before retry |
| Claim rejected for locale | wrong worker for target locale | reassign to matching locale |
| Job stuck in `claimed` too long | crashed or abandoned worker | verify worker state, then release or fail |
| Job repeatedly requeues | deterministic processing failure | inspect error and stop auto-retrying |
| Too many manual overrides | workflow drift | repair volunteer registry or scheduler |

## Volunteer Onboarding Checklist

- Verify the volunteer identity outside the queue itself.
- Register `volunteer_id`.
- Set explicit `capabilities`.
- Set explicit `locales`.
- Set a conservative `max_claims`.
- Approve the volunteer.
- Record who approved them and why.

Do not approve broad `*` locale access unless there is a real operational
need.

## Polling and Claim Loop

Recommended volunteer loop:

```text
poll snapshot
  -> select eligible queued job
  -> attempt claim with expected_version
  -> if conflict, refresh and retry
  -> run task
  -> on handoff, release
  -> on success/failure, record result
```

Operator checks during active polling:

- watch for repeated stale-version conflicts
- watch for `active_claims` at or above `max_claims`
- watch for locale skew, for example only `en-*` jobs draining

## Release vs Fail

Use `release_job()` when:

- a human is handing the task off cleanly
- the worker never started real work
- the input was valid but the assigned worker is the wrong fit

Use `record_job_result(... success=False)` when:

- the worker attempted the job and hit a real processing failure
- you want retry accounting to advance
- the error message is useful for the next assignee

Rule:

Releases are not failures. Do not burn retry budget for operator routing
mistakes.

## Override Procedure

Overrides bypass normal constraints. Treat them as break-glass actions.

Before override:

- confirm the volunteer identity
- confirm why normal approval/capability/locale checks are insufficient
- confirm the current manifest version

When override is used, include:

- `admin_id`
- a short reason

After override:

- review the job `history`
- fix the underlying registry or job metadata problem
- avoid repeating the same override pattern

## i18n Routing Guide

Use locale metadata intentionally:

- `en` can service `en-US` or `en-GB`
- `ja-JP` should not claim `fr-FR`
- `*` is for trusted generalists only

Suggested operator policy:

- keep translation generation and review as separate capabilities
- prefer native or strong-language reviewers for final `review` jobs
- do not solve systematic locale mismatches with overrides

## Failure Drills

### Drill: stale claim race

Symptoms:

- `ClaimConflictError`

Actions:

1. Fetch a new snapshot.
2. Check who already claimed the job.
3. Retry only if the job returned to `queued`.

### Drill: volunteer disappeared after claim

Symptoms:

- job remains `claimed`
- no result arrives

Actions:

1. Verify whether work actually started.
2. If no meaningful work started, `release_job()`.
3. If partial work exists but cannot complete, mark failure with context.
4. Reassign only after the manifest reflects the previous action.

### Drill: retry budget exhausted

Symptoms:

- job status becomes `failed`

Actions:

1. Read prior `history` and errors.
2. Decide whether the job input is bad, the worker path is bad, or the
   capability model is wrong.
3. Do not blindly reset and retry.
4. Write down the operator decision before reopening the work.

### Drill: accidental override

Symptoms:

- claim succeeded for a volunteer who should not have received the job

Actions:

1. Review `override` metadata.
2. Release the job if no work has started.
3. Correct volunteer or job metadata.
4. Capture the incident in operator notes.

## Backup Procedure

Minimum backup plan for a live deployment:

1. Snapshot manifest state on a schedule.
2. Store snapshots in R2 with immutable timestamped keys.
3. Back up SQL/D1 queue tables separately from R2 artifacts.
4. Periodically test restore into a non-production environment.

Recommended snapshot contents:

- manifest version
- jobs
- volunteers
- metrics
- export timestamp

## Restore Procedure

When restoring after corruption or operator error:

1. Freeze new claims.
2. Identify the last good SQL/D1 state.
3. Compare it with the latest R2 manifest snapshot.
4. Restore the authoritative state in SQL/D1.
5. Resume polling only after operators confirm queued vs claimed jobs.

Do not restore from R2 blindly if SQL/D1 contains newer valid claims.

## Security Checks

Run these checks before opening queue access wider:

- Are operator actions authenticated?
- Are volunteer identities authenticated?
- Are overrides auditable?
- Are unpublished payloads kept out of public R2 paths?
- Are claim/result endpoints rejecting stale versions?
- Are queue snapshots redacting data not needed by volunteers?

## Bus-Factor Plan

At least two operators should be able to do all of the following without
oral handoff:

- approve a volunteer
- diagnose a stale claim conflict
- distinguish release from failure
- recover a stuck claimed job
- restore from backup
- explain why SQL/D1, not R2, is the live queue source of truth

If only one person can do those steps, the queue is not production-ready.

## Validation

Before merging queue-behavior changes:

```bash
.venv/bin/python -m pytest tests/test_delegation_queue.py -q
.venv/bin/python -m pytest -q
```

CI also runs `pytest -q` through
[`pytest.yml`](/home/mcgrof/devel/ai-post-transformers/.github/workflows/pytest.yml).
