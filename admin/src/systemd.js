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
 */
export function generateSystemdService(adminId) {
  return `[Unit]
Description=AI Post Transformers podcast worker (${adminId})
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/podcast-worker/env
ExecStart=%h/devel/ai-post-transformers/.venv/bin/python \\
    %h/devel/ai-post-transformers/scripts/run_podcast_worker.py \\
    --admin-id ${adminId} \\
    --once \\
    --verify-remote \\
    --store auto
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
OnUnitActiveSec=2min
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

# Optional: override publish-job store backend (auto|local|r2)
# PUBLISH_STORE=auto
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

# 3. Reload and enable
systemctl --user daemon-reload
systemctl --user enable --now podcast-worker.timer

# 4. Check status
systemctl --user status podcast-worker.timer
journalctl --user -u podcast-worker.service -f
`;
}
