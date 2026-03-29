import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getAdminIdentity,
  generateSystemdService,
  generateSystemdTimer,
  generateEnvFile,
  generateInstallCommands,
} from './systemd.js';


// ---------------------------------------------------------------------------
// getAdminIdentity
// ---------------------------------------------------------------------------

test('getAdminIdentity extracts email from CF Access header', () => {
  const request = new Request('https://admin.test/', {
    headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
  });
  const id = getAdminIdentity(request);
  assert.equal(id.email, 'alice@example.com');
  assert.equal(id.id, 'alice');
});

test('getAdminIdentity falls back when header is missing', () => {
  const request = new Request('https://admin.test/');
  const id = getAdminIdentity(request);
  assert.equal(id.email, '');
  assert.equal(id.id, 'admin');
});

test('getAdminIdentity handles email with dots in local part', () => {
  const request = new Request('https://admin.test/', {
    headers: { 'cf-access-authenticated-user-email': 'luis.chamberlain@kernel.org' },
  });
  const id = getAdminIdentity(request);
  assert.equal(id.id, 'luis.chamberlain');
  assert.equal(id.email, 'luis.chamberlain@kernel.org');
});


// ---------------------------------------------------------------------------
// generateSystemdService — combined podcast worker
// ---------------------------------------------------------------------------

test('service unit contains admin id', () => {
  const unit = generateSystemdService('mcgrof');
  assert.ok(unit.includes('--admin-id mcgrof'));
  assert.ok(unit.includes('Description=AI Post Transformers podcast worker (mcgrof)'));
});

test('service unit has required systemd sections', () => {
  const unit = generateSystemdService('test-user');
  assert.ok(unit.includes('[Unit]'));
  assert.ok(unit.includes('[Service]'));
  assert.ok(unit.includes('[Install]'));
});

test('service unit uses oneshot type', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('Type=oneshot'));
});

test('service unit references EnvironmentFile', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('EnvironmentFile='));
  assert.ok(unit.includes('podcast-worker/env'));
});

test('service unit uses %h specifier for home directory', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('%h/'));
});

test('service unit runs from the repo working directory', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('WorkingDirectory=%h/devel/ai-post-transformers'));
});

test('service unit uses flock to skip duplicate launches', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('/usr/bin/flock -n -E 0'));
  assert.ok(unit.includes('%t/podcast-worker.lock'));
});

test('service unit runs run_podcast_worker.py with --once', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('run_podcast_worker.py'));
  assert.ok(unit.includes('--once'));
});

test('service unit passes --queue-db for bridged mode', () => {
  const unit = generateSystemdService('u');
  assert.ok(unit.includes('--queue-db'));
});

test('service unit runs combined worker not publish-only', () => {
  const unit = generateSystemdService('u');
  // Should reference the combined worker, not the publish-only one
  assert.ok(unit.includes('run_podcast_worker.py'));
  assert.ok(!unit.includes('run_publish_worker.py'));
});


// ---------------------------------------------------------------------------
// generateSystemdTimer
// ---------------------------------------------------------------------------

test('timer unit has required sections', () => {
  const unit = generateSystemdTimer();
  assert.ok(unit.includes('[Unit]'));
  assert.ok(unit.includes('[Timer]'));
  assert.ok(unit.includes('[Install]'));
});

test('timer unit has OnUnitInactiveSec for periodic firing', () => {
  const unit = generateSystemdTimer();
  assert.ok(unit.includes('OnUnitInactiveSec='));
  assert.ok(!unit.includes('OnUnitActiveSec='));
});

test('timer unit targets timers.target', () => {
  const unit = generateSystemdTimer();
  assert.ok(unit.includes('WantedBy=timers.target'));
});

test('timer unit has Persistent=true', () => {
  const unit = generateSystemdTimer();
  assert.ok(unit.includes('Persistent=true'));
});

test('timer description references podcast-worker', () => {
  const unit = generateSystemdTimer();
  assert.ok(unit.includes('podcast-worker'));
});


// ---------------------------------------------------------------------------
// generateEnvFile
// ---------------------------------------------------------------------------

test('env file contains R2/S3 placeholders', () => {
  const env = generateEnvFile();
  assert.ok(env.includes('AWS_ENDPOINT_URL='));
  assert.ok(env.includes('AWS_ACCESS_KEY_ID='));
  assert.ok(env.includes('AWS_SECRET_ACCESS_KEY='));
});

test('env file references podcast-worker', () => {
  const env = generateEnvFile();
  assert.ok(env.includes('Podcast-worker'));
});

test('env file contains QUEUE_DB placeholder', () => {
  const env = generateEnvFile();
  assert.ok(env.includes('QUEUE_DB='));
  assert.ok(env.includes('queue.db'));
});


// ---------------------------------------------------------------------------
// generateInstallCommands
// ---------------------------------------------------------------------------

test('install commands include daemon-reload', () => {
  const cmds = generateInstallCommands();
  assert.ok(cmds.includes('systemctl --user daemon-reload'));
});

test('install commands disable old split timers and enable the new one', () => {
  const cmds = generateInstallCommands();
  assert.ok(cmds.includes('disable --now podcast-publish-worker.timer'));
  assert.ok(cmds.includes('enable --now podcast-worker.timer'));
});

test('install commands reference config directory', () => {
  const cmds = generateInstallCommands();
  assert.ok(cmds.includes('~/.config/podcast-worker'));
  assert.ok(cmds.includes('~/.config/systemd/user'));
});

test('install commands reference podcast-worker service', () => {
  const cmds = generateInstallCommands();
  assert.ok(cmds.includes('podcast-worker.service'));
  assert.ok(cmds.includes('podcast-worker.timer'));
});
