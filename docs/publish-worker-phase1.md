# Publish Worker Phase 1

Phase 1 adds a small durable publish job layer without changing
`rss.py`.

## Job records

Publish jobs use the shared admin-bucket JSON shape. The worker now
prefers the shared `podcast-admin` R2 bucket when the normal R2
credentials are present and falls back to local files otherwise.

Local fallback paths:

- `publish-jobs/*.json`
- `publish-results/*.json`

The shared job shape matches the admin bucket records created by
`/api/review` on `approve` or `approve_for_publish`.

Core states:

- `approved_for_publish`
- `publish_claimed`
- `publish_running`
- `publish_released`
- `publish_failed`
- `publish_completed`

Per-step progress keys:

- `publish`
- `viz`
- `cover`
- `site`
- `verify`

## Runner

Run one job directly:

```bash
.venv/bin/python scripts/publish_job_runner.py \
  --job publish-jobs/pub_2026_03_26_120000.json \
  --admin-id admin-123 \
  --admin-name mcgrof
```

The runner reuses the current trusted commands in this order:

1. `gen-podcast.py publish --draft ...`
2. `gen-podcast.py gen-viz --draft ...`
3. `backfill_images.py --episode-id ...`
4. `make publish-site`
5. local artifact verification

Add `--verify-remote` if you want live `HEAD` checks against the
public URLs after the local checks pass.

## Worker

Claim and process one job:

```bash
.venv/bin/python scripts/run_publish_worker.py \
  --admin-id admin-123 \
  --admin-name mcgrof \
  --once
```

Other modes:

- `--claim-next`
- `--process-claimed`
- `--loop`

Storage selection:

- default: `--store auto`
- force local fallback: `--store local --local-root /path/to/repo`
- force shared bucket: `--store r2`

## Notes

This is only the first control-plane pass. The admin UI still posts
`approve`, but that action now creates a durable publish job record
instead of only writing `pending_publish`.
