# Delegation Queue Architecture

This document describes the delegation system that exists in this
repository today, plus the storage and deployment model it is designed
to sit behind when exposed through Cloudflare Workers.

If you are operating the queue, also read
[`docs/runbooks/delegation-operations.md`](/home/mcgrof/devel/ai-post-transformers/docs/runbooks/delegation-operations.md).

## What Exists Today

The implemented core is
[`delegation_queue.py`](/home/mcgrof/devel/ai-post-transformers/delegation_queue.py).
It is a pure state-transition module:

- `create_manifest()` creates an empty queue manifest.
- `register_volunteer()` records capabilities, locales, and claim caps.
- `approve_volunteer()` flips a volunteer into claimable state.
- `enqueue_job()` inserts a queued job with retry budget.
- `claim_job()` performs atomic claim checks against manifest version.
- `release_job()` hands work back without counting a failure.
- `record_job_result()` records success or failure and requeues or fails.
- `export_manifest_snapshot()` produces a stable operator/debug view.
- `export_admin_queue_payload()` exports queue sections for admin UI use.

The durable persistence boundary now lives in
[`delegation_store.py`](/home/mcgrof/devel/ai-post-transformers/delegation_store.py).
It defines a SQL/D1-style contract:

- `load_manifest()` reads the authoritative manifest.
- `mutate_manifest(mutator, expected_version=...)` applies one atomic
  compare-and-swap update.
- `load_admin_queue()` and `save_admin_queue()` keep the operator export
  payload separate from live claim state.

The request contract now lives in
[`delegation_backend.py`](/home/mcgrof/devel/ai-post-transformers/delegation_backend.py).
It exposes trusted operator and trusted worker flows on top of that
store boundary.

The test contract lives in
[`tests/test_delegation_queue.py`](/home/mcgrof/devel/ai-post-transformers/tests/test_delegation_queue.py).
Those tests define the real behavior more precisely than prose.

## Mental Model

Treat the system as a claim-based work queue with explicit trust
boundaries:

1. Operators enqueue jobs and approve volunteers.
2. Volunteers claim only work they are allowed to do.
3. Workers complete or release claims.
4. The queue tracks retries, failures, and per-locale/per-volunteer
   metrics.

The state machine stays pure, but persistence and API exposure now sit
behind an explicit store contract so the live queue can move from local
SQLite to D1 without changing queue semantics.

## Components

| Component | Current status | Responsibility | Trust level |
| --- | --- | --- | --- |
| Queue state machine | Implemented | Claim/release/result transitions | Trusted |
| Volunteer registry | Implemented | Approval, capability, locale, claim cap | Trusted |
| Metrics buckets | Implemented | Success/failure totals by volunteer and locale | Trusted |
| Admin export helpers | Implemented | Stable snapshots and flat/sectioned payloads | Trusted |
| Cloudflare admin UI | Adjacent | Human-facing dashboard and control plane shell | Semi-trusted |
| Durable queue storage | Implemented contract | Atomic manifest CAS plus admin payload persistence | Trusted |
| Trusted worker polling API | Implemented contract | Poll, claim, heartbeat, release, and report results | Trusted |
| Static or copied queue exports | Adjacent | Human review and debugging copies | Semi-trusted |

## Manifest Schema

The top-level manifest fields are:

- `version`: monotonic integer used for optimistic concurrency.
- `jobs`: map of `job_id -> job`.
- `volunteers`: map of `volunteer_id -> volunteer`.
- `metrics`: aggregate counters plus `by_volunteer` and `by_locale`.

Each job tracks:

- `job_id`, `title`, `locale`
- `required_capabilities`
- `status`: `queued`, `claimed`, `completed`, `failed`
- `claimed_by`, `claimed_at`
- `last_heartbeat_at`, `lease_expires_at`
- `failure_count`, `max_retries`
- `override`: admin override metadata when used
- `history`: append-only claim/release/result events

Each volunteer tracks:

- `volunteer_id`
- `approved`
- `approved_by` after approval
- `capabilities`
- `locales`
- `max_claims`
- `active_claims`

## State Machine

### Job lifecycle

```text
queued
  -> claimed     by successful claim
claimed
  -> queued      by release
claimed
  -> completed   by successful result
claimed
  -> queued      by failed result with retries remaining
claimed
  -> failed      by failed result after retry budget exhausted
```

### Claim contract

`claim_job()` accepts an `expected_version`. When supplied, it must
match the current manifest `version` or the claim fails with
`ClaimConflictError`.

This is the system's anti-double-claim guard. It is deliberately simple:
read manifest, choose job, attempt claim with the version you saw. If
another actor updated the manifest first, retry from a fresh read.

## Polling Flow

The backend now exposes a trusted worker polling contract around the
state machine:

```text
trusted worker polls authoritative state
  -> server filters eligible queued jobs by approval/capability/locale
  -> worker submits claim(job_id, worker_id, expected_version)
  -> server returns lease_expires_at and heartbeat metadata
  -> if claim succeeds, run the delegated task
  -> heartbeat while work is in progress
  -> on operator handoff, release_job()
  -> on success/failure, record_job_result()
  -> poll again
```

Operator note:

- Polling should be idempotent.
- Claims must always include the manifest version last observed.
- Clients must assume every network retry can race another claimant.
- Lease metadata is authoritative server state, not a client timer.
- Leases show liveness for trusted workers, but operator release or
  failure decisions still control reclamation.

## Trusted Operator / Trusted Worker API

The current backend contract distinguishes three views:

- `trusted_operator`: authoritative operator control plane and export.
- `trusted_workers`: authenticated workers polling and claiming from live
  state.
- `static_exports`: semi-trusted copies for inspection only, never live
  claiming.

Trusted worker endpoints are expected to support:

- poll
- claim with `expected_version`
- heartbeat to refresh `lease_expires_at`
- release without burning retry budget
- result reporting for success or failure

## Worker / Volunteer Model

The code uses the term `volunteer`, but the operational model is a
worker pool with explicit approval:

- A volunteer is an identity plus capability and locale metadata.
- Approval is mandatory for normal claims.
- `max_claims` is the local fairness and load-shedding control.
- `active_claims` is the live concurrency counter.

This gives a useful split:

- Operators control who may work.
- Volunteers advertise what they can work on.
- The queue decides whether a specific claim is valid.

## i18n Job Model

Locale matching is implemented in `_locale_matches()` and validated by
tests.

Rules:

- `*` matches any locale.
- Exact locale matches are accepted, for example `en-US`.
- Language-prefix matches are accepted, for example volunteer locale
  `en` can claim `en-US`.
- Non-matching locales fail with `LocaleMismatchError`.

That model is a good fit for translated descriptions, subtitle fixes,
localized intros, or region-specific review passes.

Suggested job types:

- `generate`: generate localized script/audio assets.
- `review`: editorial or factual review in the target language.
- `subtitle`: localized subtitle cleanup.
- `publish`: final packaging or metadata update.

If a future deployment adds explicit `job_type`, keep locale checking
orthogonal to capability checking. They solve different problems.

## Admin Override Model

`claim_job(..., admin_override={...})` bypasses approval, capability,
locale, and claim-cap checks. The override metadata is attached to the
job and written into claim history.

Use it only when one of these is true:

- a trusted operator is manually assigning a blocked job
- the volunteer metadata is temporarily stale
- a recovery action is required faster than registry updates

Do not use overrides as the default scheduler. They are a break-glass
tool.

## Trust Boundaries

### Trusted

- Queue manifest persistence
- Trusted operator approval and override actions
- Trusted worker polling and mutation endpoints
- Metrics and audit history
- CI tests that pin the state machine contract

### Semi-trusted

- Browser-based operator sessions until authenticated
- Any queue payload copied into R2 or static artifacts

Design rule:

Never trust a worker's local view of queue state. Only the server-side
manifest version and lease metadata are authoritative.

## D1 / SQL vs R2 Tradeoffs

The repository already uses SQLite locally in
[`db.py`](/home/mcgrof/devel/ai-post-transformers/db.py) and uses R2 for
published artifacts. For a deployed delegation queue:

### Use SQL/D1 for queue state

Good fit for:

- current manifest version
- volunteers and approvals
- live claims
- retry counters
- audit history
- operator notes

Why:

- supports compare-and-swap style updates
- queryable by volunteer, locale, and job status
- easier recovery after process restarts
- better operator introspection than object blobs

### Use R2 for artifacts, not live coordination

Good fit for:

- generated audio
- transcripts
- rendered queue exports
- immutable debug snapshots
- periodic manifest backups

Why not as the primary queue store:

- object storage is awkward for atomic claim arbitration
- last-writer-wins blob replacement makes races harder to reason about
- filtering jobs by capability/locale/history becomes expensive and crude

Practical split:

- D1/SQL is the system of record for live delegation state.
- R2 stores generated outputs and periodic backups/export snapshots.

## Failure Modes

These are the failure modes the current code already models or clearly
implies:

| Failure mode | Current behavior | Operator action |
| --- | --- | --- |
| Unapproved claimant | `ApprovalRequiredError` | approve or reject volunteer |
| Stale claim race | `ClaimConflictError` | refresh manifest and retry |
| Missing capability | `CapabilityMismatchError` | fix volunteer metadata or reassign |
| Locale mismatch | `LocaleMismatchError` | reassign to matching volunteer |
| Volunteer overload | `CapacityExceededError` | wait, raise cap, or reassign |
| Worker gives up | `release_job()` | job returns to `queued` |
| Worker run fails | `record_job_result(... success=False)` | investigate and retry |
| Retry budget exhausted | job becomes `failed` | operator intervention required |

Additional deployment-layer failures to plan for:

- poller crash after claim but before result
- duplicate result submission after a timeout
- operator override used without audit review
- stale volunteer registry cached at the edge
- manifest backup drift between SQL and R2

## Security Considerations and Threat Model

### Assets to protect

- operator approval authority
- override path
- volunteer identity mapping
- unpublished scripts/audio/transcripts
- queue history and failure telemetry

### Threats

- unauthorized volunteer claims
- forged admin overrides
- replay of stale claims
- volunteer over-claiming for denial of service
- locale or capability spoofing to access restricted work
- queue snapshot leakage exposing unpublished material
- accidental destructive operator actions

### Controls already present in code

- approval gate for normal claims
- capability matching
- locale matching
- per-volunteer claim caps
- manifest version check for stale claims
- claim history for auditability
- snapshot export redacts per-job history by default

### Controls recommended in deployment

- authenticate trusted operator and trusted worker identities separately
- require server-side override authorization
- store audit logs outside the browser session
- server-generate or validate poll/claim/result requests
- track leases with heartbeat refresh
- restrict R2 exposure for unpublished queue data
- back up manifest history before schema or scheduler changes

## CI and Test Coverage

CI is currently
[`pytest -q`](/home/mcgrof/devel/ai-post-transformers/.github/workflows/pytest.yml)
on every push and pull request through
[`pytest.yml`](/home/mcgrof/devel/ai-post-transformers/.github/workflows/pytest.yml).

Delegation coverage currently verifies:

- admin approval gate
- durable SQLite compare-and-swap persistence
- trusted worker poll filtering
- lease issuance and heartbeat refresh
- stale-version claim rejection
- release without failure accounting
- retry exhaustion behavior
- capability enforcement
- claim-cap enforcement
- locale matching behavior
- metrics by volunteer and locale
- admin override metadata
- snapshot redaction
- admin queue export shape

If you change the queue contract, update
[`tests/test_delegation_queue.py`](/home/mcgrof/devel/ai-post-transformers/tests/test_delegation_queue.py)
in the same patch.

## Backup and Bus-Factor Plan

The queue module is small enough that the real bus-factor risk is not
code size. It is operator knowledge.

Minimum plan:

1. Keep this architecture doc and the runbook in-tree.
2. Back up live manifest state to R2 as immutable timestamped snapshots.
3. Keep the canonical queue schema in SQL, not in one operator's local
   JSON file.
4. Require override reasons and approver identity in every manual claim.
5. Periodically rehearse recovery from:
   - lost worker
   - stale manifest
   - accidental override
   - restore from backup

## Recommended Deployment Shape

For this repo, the concrete deployment shape should be:

```text
Cloudflare Worker admin/API
  -> D1/SQL queue tables for live manifests, claims, volunteers
  -> delegation_queue.py-compatible state transitions in server logic
  -> R2 for generated assets and periodic manifest backups
  -> pytest in CI to pin queue semantics
```

Keep the Python module as the semantic reference implementation. If a
future Worker or service reimplements the same logic, the tests should be
ported with it so behavior stays aligned.
