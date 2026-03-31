# Queue/Submission UX Findings and Fixes

## Issues found and resolved (prior pass)

### 1. Submit page spinner never clears

**Root cause**: The `/submit` page rendered a client-side "Loading
submissions..." spinner and relied on `loadSubmissions()` fetching
`/api/submissions` from the browser. When Cloudflare Access
intercepted the XHR with a 302 redirect to the auth gate, the
fetch returned an opaque redirect and the spinner never resolved.

**Fix**: Server-render submissions data directly into the submit
page HTML at request time. The `/submit` route now calls
`getSubmissions(env)` and passes the result to `submitPage()`,
which renders the submissions list inline. No client-side fetch
needed for the initial page load. The form submit handler
(`handleSubmit`) was also improved: the submit button now shows
"Submitting..." while the POST is in flight, displays a count in
the success toast ("Submitted 2 papers — pending generation
pickup"), clears both the URL and instructions fields, and
restores the button state in a `finally` block.

### 2. Queue page showed already-approved/published episodes

**Root cause**: The queue page's submission section filtered only
`published` and `rejected` statuses, so submissions in
`approved_for_publish` and `draft_generated` states still appeared.
These are past the generation stage and belong on the Drafts page.

**Fix**: Replaced the single flat "Submissions" section with four
distinct pipeline stages:
- **Pending Generation** — `submitted`/`pending` status, waiting
  for any worker with a systemd timer to pick them up
- **Generating** — `generation_claimed`/`generation_running`,
  showing who claimed the work
- **Draft Ready** — `draft_generated`, with a link to the Drafts
  page for review
- **Generation Failed** — `generation_failed`, showing the error

Submissions that have reached `approved_for_publish`, `published`,
or `rejected` are excluded from all queue pipeline sections.

### 3. No "who is generating" info

**Root cause**: The `claimed_by` field was stored in submission
records but never displayed in either the queue page or submit
page UI.

**Fix**: Both the server-rendered queue page and the submit page
now show "Assigned to: worker-name" when a submission has a
`claimed_by` value. The Pending Generation section header explains
that "anyone with systemd timer can claim these." The client-side
`loadSubmissions()` function (used for dynamic refresh after form
submit) also now renders `claimed_by` and `instructions`.

### 4. Stale published papers in editorial queue

**Root cause**: The editorial queue (`queue/latest.json`) is a
snapshot from the scoring pipeline. Papers whose episodes have
already been published or approved via the submission flow still
appear in the queue sections.

**Fix**: `queuePageWithData` now collects arXiv IDs from all
submissions with `approved_for_publish` or `published` status,
then filters those IDs out of the editorial queue sections before
rendering. This suppresses papers like "LeWorldModel" that are
already public episodes.

### 5. Queue page used flat papers array instead of sections

**Bug found**: `queuePageWithData` accessed `data.papers` expecting
an object keyed by section name (`{bridge: [...], public: [...]}`),
but `normalizeQueuePayload` returns `papers` as a flat array and
section-keyed data in `data.sections`. This meant the editorial
queue sections always rendered empty for old-format queue exports.

**Fix**: Changed `queuePageWithData` to use `data.sections` for
the section-keyed paper lists.

### 6. Pre-existing: missing `vm` import in worker.test.js

The test "GET /drafts emits syntactically valid client script"
used `new vm.Script()` but never imported the `node:vm` module.
Added the import.

### 7. Submit UX: optimistic rendering after POST

**Root cause**: After a successful `POST /api/submit`, `handleSubmit`
called `loadSubmissions()` which made a client-side fetch to
`/api/submissions`. If Cloudflare Access intercepted that fetch, the
submissions container was replaced with "Failed to load submissions",
leaving the operator without confirmation of what happened.

**Fix**: After successful POST, `handleSubmit` now constructs an HTML
row from the form data and prepends it to the submissions container
directly. No re-fetch needed. The success toast still fires. If the
container had the "No submissions yet" empty state, it is removed
before inserting the new row. Additionally, `handleSubmit` now checks
`res.ok` before parsing JSON, showing a clear "Server error (status)"
toast on non-200 responses.

`loadSubmissions()` was also hardened: on fetch failure it now shows a
toast ("Could not refresh submissions list") instead of wiping the
container with an error div, preserving whatever server-rendered
content was already visible.

### 8. Per-row pickup info for pending submissions

**Root cause**: The Pending Generation section header said "anyone with
systemd timer can claim these" but individual submission rows did not
indicate their assignment state.

**Fix**: `renderSubmissionRow` now shows "Open — any generation worker
can claim" for submissions in `submitted`/`pending` status with no
`claimed_by`. Submissions with a `claimed_by` value still show
"Assigned to: name" regardless of status.

### 9. Broader stale-paper filtering in editorial queue

**Root cause**: Editorial queue sections only filtered arXiv IDs from
submissions with `approved_for_publish` or `published` status. Papers
in `generation_claimed`, `generation_running`, or `draft_generated`
still appeared as editorial items even though they were already being
processed through the submission pipeline.

**Fix**: Extended the filter set to include `generation_claimed`,
`generation_running`, and `draft_generated` statuses. Any paper with
an active or completed submission is now suppressed from the editorial
queue sections, preventing duplicates between the generation pipeline
and editorial queue. Renamed `completedArxivIds` to `handledArxivIds`
to reflect the broader scope.

### 10. Retry button for failed submissions

**Root cause**: Failed submissions (`generation_failed`) in the queue
page had no recovery action — the operator had to manually reset the
status or re-submit.

**Fix**: `renderSubmissionRow` now renders a retry button for
submissions with `generation_failed` status. Clicking it POSTs to
`/api/submissions/status` to reset the status back to `submitted`,
then reloads the page after a brief delay. The
`updateSubmissionStatus` endpoint already accepts `submitted` as a
valid target status, so no server-side changes were needed.

### 11. Submission metadata enrichment

**Root cause**: Recent submissions and queue views only showed raw
URLs, giving the operator no context about what paper a submission
refers to without clicking through to arXiv.

**Fix**: Added a second-pass enrichment system that fetches paper
metadata (title, publication date, abstract) from the arXiv Atom
API after a submission is recorded:

- `submitPapers` stores initial `metadata` with `pending`
  enrichment status per URL, then fires `enrichSubmissionMetadata`
  via `ctx.waitUntil()` for non-blocking background enrichment.
- `enrichSubmissionMetadata` extracts arXiv IDs from URLs, fetches
  the arXiv Atom API, parses title/published/summary from the XML,
  and writes enriched metadata back to the R2 submission record.
- Non-arXiv URLs are marked `unsupported` (URL shown as fallback).
  Failed arXiv fetches are marked `failed` with a "Metadata
  unavailable" note in the UI.
- A `POST /api/submissions/enrich` endpoint allows manual
  re-enrichment of one or all submissions.
- All three rendering paths (server-side submit page, server-side
  queue page, client-side `loadSubmissions()`) show enriched
  metadata when available: paper title as a link, publication date,
  arXiv ID, and a 180-char abstract snippet.
- When enrichment is pending, an "Enriching metadata..." indicator
  is shown instead of the raw URL.
- The optimistic rendering after form submit also shows the pending
  enrichment state.
- `getSubmissions` now exposes the `metadata` field in its API
  response.

### 12. Opaque internal IDs surfaced as queue card titles

**Root cause**: Draft cards fell back to `d.title || d.key` where
`d.title` was populated from the R2 filename basename (e.g. `ep102`)
when the manifest lacked a real title. Submission rows had no
heading title at all. The publish job record also fell back to the
bare draft stem basename.

**Fix**: Added a `displayTitle(item)` helper with a proper fallback
chain: enriched paper title → explicit title (rejected if it matches
the opaque `/^ep\d+$/i` pattern) → humanized slug from key/draft_stem
(strips date prefix and hash suffix, converts hyphens to spaces,
title-cases) → raw key as last resort. Applied to server-rendered
draft cards, client-fetched draft cards (via API response), submission
row headings, and publish job record creation. Eight regression tests
cover the fallback chain and verify opaque IDs never leak into the
primary card title when better metadata exists.

---

## Issues found and resolved (current pass — 2026-03-27)

### 13. gen-podcast.py duplicate dead code after __main__

**Root cause**: Lines 679–706 after the first `if __name__ ==
"__main__": main()` contained a copy of the admin manifest cleanup
block (which already runs inside `_publish_episode`) followed by a
second `if __name__ == "__main__": main()`. This dead code
referenced `episode` which is not in scope at module level, so it
would crash with a `NameError` if somehow reached. It also
duplicated the cleanup work already done in `_publish_episode`.

**Fix**: Removed the entire dead block, leaving only the single
`if __name__ == "__main__": main()` at the end of the file.

### 14. Stale draft suppression for publish_completed jobs

**Root cause**: `getDrafts()` in worker.js filtered drafts by
`revision_state` (superseded, rejected, published), but this only
works when the manifest entry carries revision metadata. When a
publish job completes but the R2 cleanup fails (the draft MP3
deletion is non-fatal), the MP3 lingers on the podcast bucket with
no matching manifest entry. Since `episode` is null, `revision_state`
is null, and the draft passes all filters — appearing as an active
draft card even though the episode is already published.

**Fix**: Added an additional filter condition: drafts whose latest
publish job is in `publish_completed` state are excluded from the
active drafts list. This catches the case where R2 cleanup failed
but the publish job itself succeeded. The draft remains in
`all_drafts` for audit purposes.

### 15. Queue editorial sections showed submitted/pending papers

**Root cause**: `handledStatuses` in `queuePageWithData` included
`generation_claimed`, `generation_running`, `draft_generated`,
`approved_for_publish`, and `published` — but omitted `submitted`
and `pending`. A paper that had just been submitted but not yet
claimed still appeared in the editorial queue sections, creating a
confusing duplicate between the Pending Generation section and the
editorial queue.

**Fix**: Added `submitted` and `pending` to `handledStatuses`.
Any paper whose arXiv ID matches a submission in any non-terminal
pipeline state is now filtered from editorial queue sections.

## Files changed (current pass)

- `gen-podcast.py` — removed 28 lines of dead code (duplicate
  admin cleanup block + duplicate `__main__` guard)
- `admin/src/worker.js` — stale draft suppression via
  publish_completed job state (getDrafts filter), queue editorial
  filtering extended to submitted/pending statuses
- `admin/src/worker.test.js` — 3 new tests: publish_completed
  draft filtering, approved_for_publish draft retention, submitted
  paper filtered from editorial queue

## Test results (current pass)

All test suites pass:
- 64/64 JS worker tests (3 new)
- 21/21 JS systemd tests
- 16/16 Python publish tests
- 8/8 Python systemd tests

## Audit results: areas verified as correct

### Systemd unit/timer and worker invocation

The systemd service unit (`admin/src/systemd.js`) references
`run_podcast_worker.py` which is the combined worker script that
runs generation pickup then publish pickup in sequence. The
`run_podcast_worker.py` script exists, imports both
`run_generation_worker.run_once` and `run_publish_worker.run_once`,
and chains them correctly with error isolation (generation failures
do not block publish). The timer fires every 2 minutes with
Persistent=true for catch-up. The EnvironmentFile at
`~/.config/podcast-worker/env` provides R2 credentials. All
systemd tests pass including `systemd-analyze verify`.

### Publish job processing flow

The publish job runner (`scripts/publish_job_runner.py`) correctly
resolves draft stems through the `_resolve_publish_draft` fallback
chain: episode audio_file → draft_key → raw draft_stem. Legacy
draft stems (where the draft_key is an opaque `drafts/ep{id}.mp3`
key) are resolved through the episode's actual audio_file in the
DB. The state machine transitions (approved → claimed → running →
completed/failed) are enforced and tested. Heartbeats refresh the
15-minute lease after each step.

### Draft revision tracking

The `draft_revisions.py` module correctly normalizes episode keys,
detects revisions, assigns revision numbers, and manages state
transitions (active → approved → published, with superseding). The
stale draft cleanup function catches pre-migration published
episodes still marked as active.

### Metadata enrichment

The enrichment system correctly handles all three rendering paths
(server-side submit, server-side queue, client-side dynamic). The
`displayTitle` fallback chain properly prevents opaque IDs from
surfacing.

## What remains

- **Stale papers without submissions**: Papers published manually
  (outside the submission flow) still appear in the editorial
  queue. Filtering requires cross-referencing published episodes
  with queue arXiv IDs at render time.

- **Submission deduplication**: Nothing prevents submitting the
  same URLs twice.

- **Enrichment for non-arXiv sources**: Only arXiv URLs get
  metadata enrichment. Semantic Scholar, DOI, or HTML scraping
  could handle other paper sources.

- **Enrichment retry UI**: The `POST /api/submissions/enrich`
  endpoint exists but is not wired to a UI control.

- **Enrichment staleness**: Failed enrichments stay failed
  permanently. A periodic background sweep could retry them.

## Local-machine steps needed

None for the current fixes — all changes are to tracked source
files. To deploy:

1. Deploy the updated worker.js to Cloudflare:
   `cd admin && npx wrangler deploy`

2. The gen-podcast.py fix takes effect on next invocation (no
   deploy needed, it runs locally via systemd timer or CLI).
