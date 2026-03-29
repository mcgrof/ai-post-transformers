// Systemd unit generation and Cloudflare Access identity helpers
// for the podcast-worker automation tab.
//
// The worker service runs two phases on each timer tick:
//   1. Generation pickup — process pending submissions into drafts
//   2. Publish pickup — claim and execute approved publish jobs

/**
 * Extract admin identity from Cloudflare Access JWT headers.
 * Falls back to a generic 'admin' identity when running outside
 * CF Access (local dev, tests, or unprotected deployments).
 */
export function getAdminIdentity(request) {
  const email =
    request.headers.get('cf-access-authenticated-user-email') || '';
  // Derive a short admin id from the email (local part) or fall back.
  const id = email ? email.split('@')[0] : 'admin';
  return { email, id };
}

/**
 * Generate a systemd user service unit that runs both the generation
 * worker (pick up pending submissions) and the publish worker
 * (claim and process approved publish jobs) in sequence.
 *
 * The service defaults to a queue DB under ~/.local/state but allows an
 * explicit QUEUE_DB override in the EnvironmentFile.
 */
export function generateSystemdService(adminId) {
  // Shell variables ($ADMIN_NAME, $QUEUE_DB) are resolved at runtime
  // by bash from the EnvironmentFile. They must NOT use ${} syntax here
  // because that is JS template interpolation.
  return `[Unit]
Description=AI Post Transformers podcast worker (${adminId})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/podcast-worker/env
WorkingDirectory=%h/devel/ai-post-transformers
ExecStart=/usr/bin/flock -n -E 0 %t/podcast-worker.lock /bin/bash -lc 'source ~/.enhance-bash >/dev/null 2>&1 && QUEUE_DB_PATH="$QUEUE_DB" && if [ -z "$QUEUE_DB_PATH" ]; then QUEUE_DB_PATH="$HOME/.local/state/ai-post-transformers/queue.db"; fi && mkdir -p "$(dirname "$QUEUE_DB_PATH")" && exec .venv/bin/python scripts/run_podcast_worker.py --admin-id ${adminId} --admin-name "$ADMIN_NAME" --once --verify-remote --queue-db "$QUEUE_DB_PATH"'
Restart=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
`;
}

/**
 * Generate a systemd user timer that fires the service periodically.
 */
export function generateSystemdTimer() {
  return `[Unit]
Description=Periodic trigger for podcast-worker

[Timer]
OnBootSec=1min
OnUnitInactiveSec=2min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
`;
}

/**
 * Generate a skeleton environment file with placeholders.
 */
export function generateEnvFile() {
  return `# Podcast-worker credentials — fill in before enabling the timer.
# R2 / S3-compatible object storage
AWS_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# Optional: override the SQLite queue database path used for bridged
# mode. If unset, the service defaults to:
#   $HOME/.local/state/ai-post-transformers/queue.db
# QUEUE_DB=/home/your-user/.local/state/ai-post-transformers/queue.db

# Optional admin display name
# ADMIN_NAME=
`;
}

/**
 * Generate shell commands that install units and enable the timer.
 */
export function generateInstallCommands() {
  return `# 1. Create config directory and save environment file
mkdir -p ~/.config/podcast-worker
# (copy the env file contents above into ~/.config/podcast-worker/env)

# 2. Install systemd user units
mkdir -p ~/.config/systemd/user
cp podcast-worker.service ~/.config/systemd/user/
cp podcast-worker.timer   ~/.config/systemd/user/

# 3. Disable the old split publish timer if it exists
systemctl --user disable --now podcast-publish-worker.timer 2>/dev/null || true

# 4. Reload and enable
systemctl --user daemon-reload
systemctl --user enable --now podcast-worker.timer

# 5. Check status
systemctl --user status podcast-worker.timer
journalctl --user -u podcast-worker.service -f
`;
}
