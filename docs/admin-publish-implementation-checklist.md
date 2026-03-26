# Admin Publish Workflow Implementation Checklist

Implement a real multi-admin publish workflow for AI Post Transformers. Do not treat admin approval as equivalent to publication. Treat publication as a claimable, stepwise job that must reach `live_verified` before it is considered done.

## Goal

Make the admin interface support this end-to-end flow:

1. Review a draft
2. Approve it for publication
3. Let one admin claim responsibility for publishing it
4. Execute the expensive publish steps using that admin's local environment and credits
5. Track progress and ownership in shared state
6. Verify all public artifacts
7. Remove stale draft state only after successful publish completion

Support multiple admins, partial failures, retries, and future extension to translation / i18n jobs.

## Non-goals

Do not implement i18n in this phase.
Do not depend on a single chat session to complete publication.
Do not hide partial failures behind a single boolean success flag.

## Lessons that must drive the design

Treat these as hard operational rules:

1. Publishing audio is not enough.
2. `make publish-site` must succeed.
3. Missing `image_file` means the episode is not fully published.
4. Missing visualization link means the episode is not fully published when viz is required.
5. Admin UI must distinguish editorial approval from publication execution.
6. The only truly done state is `live_verified`.
7. Publish artifacts must be verified live after site deployment.
8. Draft cleanup must happen after publish success, not before.

## Current behavior and gaps

### Current admin behavior

`admin/src/worker.js` handles `/api/review` via `reviewDraft()`.

On `approve`, it currently:

- writes a review record under `reviews/...`
- writes a queue record under `publish-queue/...`
- marks it `pending_publish`

It does **not**:

- run `gen-podcast.py publish --draft ...`
- generate visualization
- generate / backfill cover art
- run `make publish-site`
- verify public URLs
- expose publish ownership or lease status

### Missing repo component

No in-repo publish worker currently consumes `publish-queue/` and completes the full publish pipeline.

That is the primary missing piece.

## Target architecture

Split the system into two planes.

### 1. Shared control plane

Use the shared admin bucket / shared metadata as the coordination layer.

Persist these durable entities:

- draft metadata
- review records
- publish jobs
- publish results
- claim / lease state
- future: i18n jobs

### 2. Local execution plane

Run a local publish worker on an admin-controlled machine.

That worker must:

- read approved publish jobs
- claim one job
- execute the publish pipeline locally with that admin's environment and API credits
- update job state after each step
- release or renew claim leases during long-running steps
- report failures with enough detail for retry

This design supports multiple admins with different hosts, API keys, and model credit budgets.

## Canonical state machine

### Episode / publish lifecycle states

Support these states at minimum:

- `draft_created`
- `review_requested`
- `review_claimed`
- `review_ready`
- `approved_for_publish`
- `publish_claimed`
- `publish_running`
- `published_audio_only`
- `viz_generated`
- `cover_generated`
- `site_published`
- `live_verified`
- `publish_failed`
- `rejected`
- `archived`

### State meanings

- `approved_for_publish`
  - editorial decision is complete
  - no one has committed credits yet

- `publish_claimed`
  - a specific admin has volunteered to execute publication
  - lease / ownership is active

- `publish_running`
  - worker is executing steps

- `published_audio_only`
  - audio/transcript published successfully
  - visualization / cover / site / verification still pending

- `live_verified`
  - all required artifacts exist and public endpoints respond correctly
  - this is the only real done state

- `publish_failed`
  - at least one required step failed
  - preserve partial artifacts and step status

## Claim and lease model

Add claim metadata to every publish job.

### Required fields

- `claimed_by_admin_id`
- `claimed_by_name`
- `claimed_at`
- `lease_expires_at`
- `last_heartbeat_at`
- `released_at`
- `release_reason`

### Rules

- Only one active claim at a time
- Claims expire if the worker stops heartbeating
- Another admin may reclaim after lease expiry
- Workers renew lease while long-running steps are active
- UI must show who owns the job and whether the lease is healthy

## Publish job schema

Store job records in a stable JSON format under `publish-jobs/`.

### Suggested schema

```json
{
  "job_id": "pub_2026_03_26_000102",
  "episode_id": 102,
  "draft_id": 102,
  "draft_key": "drafts/ep102.mp3",
  "draft_stem": "drafts/2026/03/2026-03-25-leworldmodel-stable-joint-embedding-worl-650f9f",
  "title": "LeWorldModel: Stable Joint-Embedding World Models from Pixels",
  "state": "approved_for_publish",
  "created_at": "2026-03-26T12:00:00Z",
  "approved_by_admin_id": "admin-123",
  "approved_by_name": "mcgrof",
  "claimed_by_admin_id": null,
  "claimed_by_name": null,
  "claimed_at": null,
  "lease_expires_at": null,
  "last_heartbeat_at": null,
  "requirements": {
    "viz": true,
    "cover": true,
    "publish_site": true,
    "verify": true
  },
  "progress": {
    "publish": "pending",
    "viz": "pending",
    "cover": "pending",
    "site": "pending",
    "verify": "pending"
  },
  "artifacts": {
    "audio_url": null,
    "srt_url": null,
    "page_url": null,
    "viz_url": null,
    "cover_url": null,
    "thumb_url": null
  },
  "error": null,
  "history": []
}
```

### Progress values

Use these values consistently:

- `pending`
- `running`
- `done`
- `failed`
- `skipped`

## Shared result schema

Write final result records under `publish-results/`.

Include:

- job id
- final state
- artifact URLs
- timestamps by step
- error details
- admin identity
- verification results

## API changes in `admin/src/worker.js`

### Replace current semantics

Do not overload `approve` to mean both editorial approval and publication.

Support explicit actions instead:

- `approve_for_publish`
- `claim_publish`
- `release_publish_claim`
- `retry_publish`
- `reject`
- `refresh_job_status`

### Files to change

- `admin/src/worker.js`

### Required API behavior

#### `/api/review`

Split behavior:

- `approve_for_publish`
  - create or update a publish job with state `approved_for_publish`
  - do not publish immediately

- `reject`
  - record rejection with reason
  - mark draft state appropriately

#### New endpoint: `/api/publish`

Support actions:

- `claim_publish`
- `release_publish_claim`
- `retry_publish`
- `get_publish_status`

#### Required UI responses

Return structured data including:

- `job_id`
- `state`
- `claimed_by`
- `lease_expires_at`
- `progress`
- `error`

## UI changes in `admin/src/worker.js`

### Drafts tab

Replace the current two-button model:

- Approve
- Reject

with a workflow-aware model:

- Approve for publish
- Claim publish
- Release claim
- Retry failed publish
- Reject

### Required per-draft display fields

Show:

- editorial status
- publish status
- claimed by
- lease expiry or running heartbeat
- last error message
- artifact links if partial or complete

### Required publish progress UI

Show step-level status for:

- publish audio/transcript
- generate viz
- generate cover
- publish site
- verify live

### Optional but strongly recommended

- optimistic button disabling while claim requests are in flight
- auto-refresh or polling of active job rows
- a separate section for `publish_running` and `publish_failed`

## New scripts to add

### 1. `scripts/publish_job_runner.py`

Implement the end-to-end publish pipeline for one job.

#### Responsibilities

1. Load job JSON
2. Validate claim ownership
3. Resolve draft stem
4. Run publish step
5. Run visualization generation
6. Run targeted cover generation or targeted backfill
7. Run `make publish-site`
8. Verify live artifacts
9. Update job/result records
10. Clean stale draft state only after success

#### Command contract

Support arguments like:

```bash
.venv/bin/python scripts/publish_job_runner.py \
  --job publish-jobs/pub_2026_03_26_000102.json \
  --admin-id admin-123 \
  --admin-name "mcgrof"
```

#### Step execution contract

Update job `progress` after each step. Do not wait until the end.

### 2. `scripts/run_publish_worker.py`

Implement a long-running or one-shot worker that:

- polls for `approved_for_publish` or `publish_claimed` jobs
- claims jobs when asked
- processes jobs already claimed by this admin
- renews leases
- resumes interrupted jobs

#### Modes

Support at least:

- `--claim-next`
- `--process-claimed`
- `--once`
- `--loop`

## Existing code paths to reuse

Reuse the current trusted publish commands rather than inventing new publishing logic.

### Publish draft

Use:

```bash
.venv/bin/python gen-podcast.py publish --draft <stem>
```

### Generate visualization

Use:

```bash
.venv/bin/python gen-podcast.py gen-viz --draft <public stem>
```

### Generate cover art

Prefer a targeted mode. If missing, add one.

Short-term fallback:

```bash
.venv/bin/python backfill_images.py
```

Better long-term contract:

```bash
.venv/bin/python backfill_images.py --episode-id <id>
```

### Publish site

Use:

```bash
make publish-site
```

## Recommended changes to existing scripts

### `backfill_images.py`

Add a targeted mode.

#### Required change

Support:

- `--episode-id <id>`
- optionally `--title-match <text>`

Reason:
Avoid running whole-database image backfill for every single publish.

### `gen-podcast.py`

Keep current publish behavior, but expose enough structured output for job runners.

#### Optional improvement

Add a machine-readable mode such as:

- `--json-status`

Reason:
Reduce fragile log scraping in worker code.

## Verification logic

Implement a dedicated verification helper.

### New script (optional but recommended)

- `scripts/verify_published_episode.py`

### Verification checks

For every publish job, verify:

- audio URL returns 200
- transcript URL returns 200
- episode permalink returns 200
- viz URL returns 200 if required
- cover image exists in episode page or DB
- homepage or month page references the episode correctly
- draft is absent from admin draft list after success

### Verification policy

Do not mark `live_verified` until all required checks pass.

## Draft cleanup rules

After successful publish completion:

- remove draft entry from admin manifest
- remove stale `drafts/ep<ID>.mp3` remote object if it exists
- keep local/public artifacts as normal published assets

Do not clean draft state before:

- publish succeeds
- site publish succeeds
- verification succeeds

## Failure handling

### Required failure capture

On any failure, write:

- failing step
- stderr or exception text
- partial artifact URLs if any
- admin identity
- timestamp

### Retry model

Allow retries only from safe states:

- `publish_failed`
- expired `publish_claimed`
- interrupted `publish_running` with stale lease

### Idempotence expectations

The worker must tolerate reruns.

Examples:

- publish command run again after audio already exists
- viz generation rerun for an existing viz
- `make publish-site` rerun after partial upload

## Admin identity model

Require each admin runner to have stable identity values:

- `admin_id`
- `admin_name`
- optional `host_name`

Store these in job history so the UI can answer:

- who approved
- who claimed
- who published
- who failed it

## Operational checklist for one publish job

Implement this exact order:

1. Claim job
2. Mark `publish_running`
3. Publish audio/transcript
4. Mark `published_audio_only`
5. Generate viz
6. Mark `viz_generated`
7. Generate cover / infographic
8. Mark `cover_generated`
9. Run `make publish-site`
10. Mark `site_published`
11. Verify live URLs and page state
12. Mark `live_verified`
13. Clean draft manifest / remote draft object
14. Write final result record

## Acceptance criteria

Do not call this complete until all of these work.

### Single-admin acceptance

- Admin can click approve for publish
- Admin can claim a publish job
- Worker publishes one draft end-to-end
- UI shows progress
- Final state becomes `live_verified`
- Draft disappears from admin list after success

### Multi-admin acceptance

- Two admins see the same approved publish job
- One admin claims it
- The other admin sees it as claimed and cannot double-run it
- Lease expiry allows reclaim after timeout
- Job history shows who published it

### Failure-path acceptance

- Viz failure marks job `publish_failed`
- Partial audio publish does not get mislabeled as done
- Retry resumes safely
- Site publish failure preserves enough state to recover

### Verification acceptance

After completion, all of these must be true:

- MP3 URL works
- SRT URL works
- pretty episode page works
- viz page works when required
- infographic / cover appears
- homepage or month page shows episode properly
- admin draft entry is gone

## Rollout plan

### Phase 1

Implement:

- publish job schema
- `publish_job_runner.py`
- targeted `backfill_images.py --episode-id`
- verification helper

### Phase 2

Implement:

- admin API changes for claim / release / retry
- drafts tab job state display

### Phase 3

Implement:

- `run_publish_worker.py`
- lease renewal
- multi-admin testing

### Phase 4

Implement:

- nicer job logs in UI
- artifact links in status view
- explicit `Publish` action separate from `Approve`

## Future extension: i18n

Do not implement now, but preserve the same architecture.

### Future tab

Add an `i18n` tab with:

- rows = published episodes
- columns = languages
- claimable translation jobs
- status per language

### Future backends

Track translation / TTS backends such as:

- ElevenLabs
- Kokoro

### Future job model

Reuse the same patterns:

- intent
- claim
- lease
- step progress
- artifact verification

That architecture should extend naturally once publish jobs work.
