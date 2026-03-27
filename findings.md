# Findings: Admin Automation / Systemd Tab

## What changed

### New files

- **admin/src/systemd.js** — Exports five functions:
  - `getAdminIdentity(request)` — reads the
    `cf-access-authenticated-user-email` header set by Cloudflare
    Access when the admin site is behind a Zero Trust policy.
    Falls back to `{ email: '', id: 'admin' }` when the header is
    absent (local dev, tests, unprotected deploys).
  - `generateSystemdService(adminId)` — returns a systemd user
    service unit (Type=oneshot) that runs
    `scripts/run_publish_worker.py --once` under the given admin
    identity, loading R2 credentials from an env file.
  - `generateSystemdTimer()` — returns a user timer that fires
    the service every two minutes with Persistent=true so missed
    runs catch up after sleep/reboot.
  - `generateEnvFile()` — returns a skeleton env file with
    R2/S3-compatible credential placeholders.
  - `generateInstallCommands()` — returns copy-pasteable shell
    commands for installing units and enabling the timer.

- **admin/src/systemd.test.js** — 17 fast Node tests covering
  CF Access header extraction (present, absent, dotted email),
  service/timer section structure, oneshot type, EnvironmentFile
  reference, %h specifier usage, and install command content.

- **tests/test_systemd_units.py** — 7 Python tests that call
  the JS generators via `node -e` and validate:
  - Required INI sections parse correctly with configparser.
  - Admin ID is embedded in ExecStart.
  - `systemd-analyze verify --user` passes for both the service
    and timer units (skipped gracefully when systemd-analyze is
    not installed).

### Modified files

- **admin/src/worker.js** — imports the new systemd module, adds
  an "Automation" nav link, a `/automation` page route that
  renders the generated units in `<pre>` blocks, and a
  `/api/systemd` JSON endpoint returning the same data.
  The page greeting shows the CF-Access-derived email when
  available.

- **admin/src/release.js** — bumped to `admin-v2026.03.27.0`.

## Design decisions

**Cloudflare Access identity** — the worker reads the
`cf-access-authenticated-user-email` header, which CF Access
populates after JWT validation at the edge. No token verification
is done in the worker itself because CF Access already gates the
route. The fallback ensures the page works in local dev.

**Type=oneshot + timer** — the publish worker already supports
`--once` (claim one job, process, exit). Wrapping it in a oneshot
service with a two-minute timer is simpler and more debuggable
than a long-running `--loop` daemon: each invocation gets its own
journal entry, and systemd handles restart/backoff automatically.

**%h specifier** — the service unit uses `%h` (home directory
specifier) rather than hard-coding a path, so the same unit file
works for any user without edits.

## Pre-existing test issue

`worker.test.js` test 10 ("GET /drafts emits syntactically valid
client script") fails with "vm is not defined" because the test
references `vm.Script` without importing `node:vm`. This predates
the current change set.

## How to verify

```bash
# JS tests (17 tests)
node --test admin/src/systemd.test.js

# Python tests including systemd-analyze verify (7 tests)
.venv/bin/python -m pytest tests/test_systemd_units.py -v

# Full publish test gate
make test-publish
```
