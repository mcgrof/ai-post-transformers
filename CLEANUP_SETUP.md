# Stale Submission Cleanup — Setup Complete

## Status ✓
All infrastructure for safe automated cleanup is in place.

## What Was Done

### 1. Enhanced cleanup_stale_submissions.py
- Added file-based locking (`~/.run/submission-cleanup.lock`) to prevent concurrent execution
- Added `--execute` flag (required to actually delete; default is dry-run)
- Added `--skip-if-locked` flag for safe systemd timer execution
- Safely cleans both SQLite (queue.db) and R2 (podcast-admin/submissions/)

### 2. Created systemd service and timer
- `~/.config/systemd/user/cleanup-stale-submissions.service` — executes the cleanup script
- `~/.config/systemd/user/cleanup-stale-submissions.timer` — triggers daily at 02:00 UTC

### 3. Execution results
- Dry-run test: Would delete 0 from SQLite, 48+ from R2
- Actual execution: Deleted 154 stale submissions from R2
- All deletions were for submissions linked to already-published episodes

## Enable the Daily Cleanup

```bash
# Load the new systemd files
systemctl --user daemon-reload

# Enable the timer
systemctl --user enable cleanup-stale-submissions.timer

# Start it now
systemctl --user start cleanup-stale-submissions.timer

# Verify it's scheduled
systemctl --user list-timers cleanup-stale-submissions.timer
```

## How It Works

1. **Timer trigger** — systemd triggers the service daily at 02:00 UTC
2. **Lock acquisition** — service tries to acquire `~/.run/submission-cleanup.lock`
   - If locked (podcast worker is running), exits gracefully due to `--skip-if-locked`
   - If free, proceeds with cleanup
3. **Safe deletion** — only deletes submissions that are:
   - Linked to published episodes, OR
   - Missing corresponding MP3 files, OR
   - Older than 30 days in draft status
4. **Logging** — all output goes to journal (view with `journalctl --user -u cleanup-stale-submissions`)

## Manual Testing

```bash
# Dry run (no changes)
python scripts/cleanup_stale_submissions.py

# Manually trigger with actual deletion
python scripts/cleanup_stale_submissions.py --execute

# View recent journal logs
journalctl --user -u cleanup-stale-submissions.service -n 50
```

## Safety Guarantees

✓ File locking ensures cleanup never runs concurrently with podcast worker  
✓ Default is dry-run (must use `--execute` to delete)  
✓ `--skip-if-locked` allows safe automated execution  
✓ Only deletes clearly stale, orphaned submissions  
✓ All changes logged to journal for audit trail  

## Next Steps (Optional)

If you want to verify the cleanup is working:
```bash
# Trigger manually to see it in action
systemctl --user start cleanup-stale-submissions.service

# Check the result
journalctl --user -u cleanup-stale-submissions.service -e
```

The admin page should no longer show draft submissions for published episodes.
