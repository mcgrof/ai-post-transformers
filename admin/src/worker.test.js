import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

import worker, { displayTitle, humanizeSlug, OPAQUE_ID_RE } from './worker.js';
import { ADMIN_RELEASE_TAG } from './release.js';


class MockBucketObject {
  constructor(value) {
    this.value = value;
  }

  async json() {
    return JSON.parse(this.value);
  }
}


class MockBucket {
  constructor(seed = {}) {
    this.objects = new Map();
    for (const [key, value] of Object.entries(seed)) {
      this.putSync(key, value);
    }
  }

  putSync(key, value) {
    const body = typeof value === 'string' ? value : JSON.stringify(value);
    this.objects.set(key, body);
  }

  async get(key) {
    if (!this.objects.has(key)) {
      return null;
    }
    return new MockBucketObject(this.objects.get(key));
  }

  async put(key, value) {
    this.putSync(key, value);
  }

  async list({ prefix = '' } = {}) {
    return {
      objects: [...this.objects.keys()]
        .filter((key) => key.startsWith(prefix))
        .map((key) => ({ key })),
    };
  }
}


function makeEnv(seed = {}) {
  return {
    ADMIN_BUCKET: new MockBucket(seed.admin),
    PODCAST_BUCKET: new MockBucket(seed.podcast),
  };
}


test('GET /api/queue normalizes admin export payloads', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        exported_at: '2026-03-21T18:30:00Z',
        sections: {
          bridge: [
            {
              arxiv_id: '2401.00001',
              title: 'Bridge Paper',
              abstract: 'Bridge abstract',
            },
          ],
        },
        papers: [
          {
            arxiv_id: '2401.00001',
            title: 'Bridge Paper',
            abstract: 'Bridge abstract',
            queue_section: 'bridge',
            score: 0.82,
          },
        ],
        counts: { bridge: 1 },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/queue'),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.papers.length, 1);
  assert.equal(body.papers[0].queue_section, 'bridge');
  assert.equal(body.counts.bridge, 1);
});


test('GET /api/delegation serves stored export bundle and fallback trust boundaries', async () => {
  const seededEnv = makeEnv({
    admin: {
      'delegation/admin/latest.json': {
        manifest: {
          version: 3,
          jobs: [{ job_id: 'job-1', status: 'queued' }],
          volunteers: [],
          metrics: {},
        },
        admin_queue: {
          papers: [],
          sections: {},
          counts: {},
        },
        trust_boundaries: {
          trusted_operator: 'authoritative operator control plane',
          trusted_workers: 'authenticated workers claim from live state',
          static_exports: 'semi-trusted copies, never claim from them',
        },
      },
    },
  });

  const seededResponse = await worker.fetch(
    new Request('https://admin.test/api/delegation'),
    seededEnv,
    {},
  );
  const seededBody = await seededResponse.json();
  assert.equal(seededBody.manifest.version, 3);
  assert.equal(seededBody.manifest.jobs[0].job_id, 'job-1');

  const fallbackEnv = makeEnv({
    admin: {
      'queue/latest.json': {
        bridge: [
          {
            arxiv_id: '2401.00001',
            title: 'Bridge Paper',
            max_axis_score: 0.82,
          },
        ],
      },
    },
  });

  const fallbackResponse = await worker.fetch(
    new Request('https://admin.test/api/delegation'),
    fallbackEnv,
    {},
  );
  const fallbackBody = await fallbackResponse.json();

  assert.equal(fallbackResponse.status, 200);
  assert.equal(fallbackBody.manifest.version, 0);
  assert.equal(fallbackBody.admin_queue.papers[0].queue_section, 'bridge');
  assert.equal(
    fallbackBody.trust_boundaries.trusted_workers,
    'authenticated workers claim from live state',
  );
});


test('POST /api/review approve creates durable publish job record', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 17,
            title: 'Example Draft',
            draft_key: 'drafts/2026/03/example-draft.mp3',
          },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/example-draft.mp3',
        action: 'approve',
        adminId: 'admin-1',
        adminName: 'mcgrof',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.success, true);
  assert.equal(body.publish_job.state, 'approved_for_publish');
  assert.equal(body.publish_job.episode_id, 17);

  const keys = [...env.ADMIN_BUCKET.objects.keys()];
  const publishJobKey = keys.find((key) => key.startsWith('publish-jobs/'));
  assert.ok(publishJobKey);
  const savedJob = JSON.parse(env.ADMIN_BUCKET.objects.get(publishJobKey));
  assert.equal(savedJob.draft_key, 'drafts/2026/03/example-draft.mp3');
  assert.equal(savedJob.progress.publish, 'pending');
});


test('GET /api/drafts includes latest publish job status summary', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 17,
            title: 'Example Draft',
            draft_key: 'drafts/2026/03/example-draft.mp3',
          },
        ],
      },
      'publish-jobs/pub_2026_03_26_120000.json': {
        job_id: 'pub_2026_03_26_120000',
        draft_key: 'drafts/2026/03/example-draft.mp3',
        draft_stem: 'drafts/2026/03/example-draft',
        title: 'Example Draft',
        state: 'publish_claimed',
        created_at: '2026-03-26T12:00:00Z',
        updated_at: '2026-03-26T12:00:00Z',
        claimed_by_admin_id: 'admin-2',
        claimed_by_name: 'operator',
        claimed_at: '2026-03-26T12:01:00Z',
        lease_expires_at: '2099-03-26T12:16:00Z',
        last_heartbeat_at: '2026-03-26T12:01:30Z',
        progress: {
          publish: 'running',
          viz: 'pending',
          cover: 'pending',
          site: 'pending',
          verify: 'pending',
        },
      },
    },
    podcast: {
      'drafts/2026/03/example-draft.mp3': 'audio',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.drafts.length, 1);
  assert.equal(body.drafts[0].publish_job.state, 'publish_claimed');
  assert.equal(body.drafts[0].publish_job.claimed_by.admin_id, 'admin-2');
  assert.equal(body.drafts[0].publish_job.lease.active, true);
  assert.equal(body.drafts[0].publish_job.progress.publish, 'running');
});




test('POST /api/review approve without adminId still creates durable state', async () => {
  // The real frontend sends { key, action: 'approve' } with NO adminId/adminName.
  // This test verifies the actual browser click path creates durable shared state.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 99,
            title: 'HyperAgents Draft',
            draft_key: 'drafts/2026/03/hyperagents.mp3',
          },
        ],
      },
    },
    podcast: {
      'drafts/2026/03/hyperagents.mp3': 'audio-data',
    },
  });

  // Simulate the exact payload the frontend sends (no adminId, no adminName)
  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/hyperagents.mp3',
        action: 'approve',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.success, true);
  assert.ok(body.publish_job, 'approve response must include publish_job');
  assert.equal(body.publish_job.state, 'approved_for_publish');
  assert.ok(body.publish_job.job_id, 'publish_job must have a job_id');

  // Verify durable state: both review record and publish job written to R2
  const allKeys = [...env.ADMIN_BUCKET.objects.keys()];
  const reviewKeys = allKeys.filter((k) => k.startsWith('reviews/'));
  const publishJobKeys = allKeys.filter((k) => k.startsWith('publish-jobs/'));
  assert.ok(reviewKeys.length >= 1, 'review record must be written');
  assert.ok(publishJobKeys.length >= 1, 'publish job must be written');

  // Verify the publish job is retrievable via GET /api/drafts
  const draftsRes = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const draftsBody = await draftsRes.json();
  const draft = draftsBody.drafts.find(
    (d) => d.key === 'drafts/2026/03/hyperagents.mp3',
  );
  assert.ok(draft, 'draft must appear in drafts list');
  assert.ok(draft.publish_job, 'draft must have publish_job after approve');
  assert.equal(draft.publish_job.state, 'approved_for_publish');
});


test('POST /api/review approve is idempotent for already-approved drafts', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 50,
            title: 'Double Click Draft',
            draft_key: 'drafts/2026/03/double-click.mp3',
          },
        ],
      },
    },
  });

  // First approve
  const res1 = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/double-click.mp3',
        action: 'approve',
      }),
    }),
    env,
    {},
  );
  const body1 = await res1.json();
  assert.equal(body1.success, true);
  const firstJobId = body1.publish_job.job_id;

  // Second approve (user clicks again because UI didn't update)
  const res2 = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/double-click.mp3',
        action: 'approve',
      }),
    }),
    env,
    {},
  );
  const body2 = await res2.json();
  assert.equal(body2.success, true);
  assert.equal(body2.publish_job.state, 'approved_for_publish');

  // Should still have exactly one publish job (re-approved, not duplicated)
  const allKeys = [...env.ADMIN_BUCKET.objects.keys()];
  const publishJobKeys = allKeys.filter((k) => k.startsWith('publish-jobs/'));
  assert.equal(publishJobKeys.length, 1, 'must not create duplicate publish jobs');
});


test('GET /drafts server-rendered page reflects approved state in action buttons', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 17,
            title: 'Approved Draft',
            draft_key: 'drafts/2026/03/approved.mp3',
          },
        ],
      },
      'publish-jobs/pub_2026_03_26_090000.json': {
        job_id: 'pub_2026_03_26_090000',
        draft_key: 'drafts/2026/03/approved.mp3',
        title: 'Approved Draft',
        state: 'approved_for_publish',
        created_at: '2026-03-26T09:00:00Z',
        updated_at: '2026-03-26T09:00:00Z',
        progress: {
          publish: 'pending',
          viz: 'pending',
          cover: 'pending',
          site: 'pending',
          verify: 'pending',
        },
      },
    },
    podcast: {
      'drafts/2026/03/approved.mp3': 'audio-data',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  // The approve button should be disabled when draft is already approved
  assert.ok(html.includes('disabled'), 'approve button must be disabled for approved drafts');
  assert.ok(html.includes('Approved'), 'button label must show Approved state');
});


test('GET /drafts renders public-style clickable source blocks for malformed draft sources', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 42,
            title: 'Malformed Sources Draft',
            draft_key: 'drafts/2026/03/malformed.mp3',
            description: 'Body copy here. Sources: https://arxiv.org/abs/2502.15734https://arxiv.org/abs/2412.15605 2. FastGen https://arxiv.org/abs/2303.01843',
          },
        ],
      },
    },
    podcast: {
      'drafts/2026/03/malformed.mp3': 'audio',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('card-sources'));
  assert.ok(html.includes('href="https://arxiv.org/abs/2502.15734"'));
  assert.ok(html.includes('href="https://arxiv.org/abs/2412.15605"'));
  assert.ok(html.includes('href="https://arxiv.org/abs/2303.01843"'));
  assert.equal(html.includes('2502.15734https://arxiv.org/abs/2412.15605'), false);
});

test('GET /drafts shows the deployed admin release tag', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes(ADMIN_RELEASE_TAG));
  assert.ok(html.includes('copyReleaseTag(event)'));
  assert.ok(html.includes('data-release="' + ADMIN_RELEASE_TAG + '"'));
  assert.ok(html.includes('Copy release tag to clipboard'));
});

test('GET /drafts emits syntactically valid client script', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();
  const match = html.match(/<script>\n([\s\S]*?)\n<\/script>\n<\/body>/);

  assert.ok(match, 'expected embedded client script block');
  assert.doesNotThrow(() => new vm.Script(match[1]));
});

test('GET /api/version exposes the deployed admin release tag', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/version'),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.release, ADMIN_RELEASE_TAG);
});

test('POST /api/review supports claim release and retry publish actions', async () => {
  const env = makeEnv({
    admin: {
      'publish-jobs/pub_2026_03_26_120000.json': {
        job_id: 'pub_2026_03_26_120000',
        draft_key: 'drafts/2026/03/example-draft.mp3',
        draft_stem: 'drafts/2026/03/example-draft',
        title: 'Example Draft',
        state: 'approved_for_publish',
        created_at: '2026-03-26T12:00:00Z',
        updated_at: '2026-03-26T12:00:00Z',
        approved_by_admin_id: 'admin-1',
        approved_by_name: 'mcgrof',
        claimed_by_admin_id: null,
        claimed_by_name: null,
        claimed_at: null,
        lease_expires_at: null,
        last_heartbeat_at: null,
        released_at: null,
        release_reason: null,
        requirements: {
          viz: true,
          cover: true,
          publish_site: true,
          verify: true,
        },
        progress: {
          publish: 'failed',
          viz: 'pending',
          cover: 'pending',
          site: 'pending',
          verify: 'pending',
        },
        step_timestamps: {},
        artifacts: {},
        error: { step: 'publish', message: 'boom' },
        history: [],
      },
    },
  });

  const claimResponse = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'claim_publish',
        jobId: 'pub_2026_03_26_120000',
        adminId: 'admin-2',
        adminName: 'operator',
      }),
    }),
    env,
    {},
  );
  const claimBody = await claimResponse.json();
  assert.equal(claimBody.publish_job.state, 'publish_claimed');
  assert.equal(claimBody.publish_job.claimed_by_admin_id, 'admin-2');

  const releaseResponse = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'release_publish_claim',
        jobId: 'pub_2026_03_26_120000',
        adminId: 'admin-2',
        reason: 'pause',
      }),
    }),
    env,
    {},
  );
  const releaseBody = await releaseResponse.json();
  assert.equal(releaseBody.publish_job.state, 'publish_released');
  assert.equal(releaseBody.publish_job.release_reason, 'pause');

  const retryResponse = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'retry_publish',
        jobId: 'pub_2026_03_26_120000',
        adminId: 'admin-1',
        adminName: 'mcgrof',
      }),
    }),
    env,
    {},
  );
  const retryBody = await retryResponse.json();
  assert.equal(retryBody.publish_job.state, 'approved_for_publish');
  assert.equal(retryBody.publish_job.progress.publish, 'pending');
  assert.equal(retryBody.publish_job.error, null);
});


test('GET and POST /api/publish expose publish status and actions', async () => {
  const env = makeEnv({
    admin: {
      'publish-jobs/pub_2026_03_26_120000.json': {
        job_id: 'pub_2026_03_26_120000',
        draft_key: 'drafts/2026/03/example-draft.mp3',
        draft_stem: 'drafts/2026/03/example-draft',
        title: 'Example Draft',
        state: 'approved_for_publish',
        created_at: '2026-03-26T12:00:00Z',
        updated_at: '2026-03-26T12:00:00Z',
        claimed_by_admin_id: null,
        claimed_by_name: null,
        claimed_at: null,
        lease_expires_at: null,
        last_heartbeat_at: null,
        progress: {
          publish: 'pending',
          viz: 'pending',
          cover: 'pending',
          site: 'pending',
          verify: 'pending',
        },
        error: null,
      },
    },
  });

  const getResponse = await worker.fetch(
    new Request(
      'https://admin.test/api/publish?draftKey=drafts/2026/03/example-draft.mp3',
    ),
    env,
    {},
  );
  const getBody = await getResponse.json();
  assert.equal(getResponse.status, 200);
  assert.equal(getBody.publish_job.job_id, 'pub_2026_03_26_120000');
  assert.equal(getBody.publish_job.claimed_by, null);

  const claimResponse = await worker.fetch(
    new Request('https://admin.test/api/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'claim_publish',
        jobId: 'pub_2026_03_26_120000',
        adminId: 'admin-2',
        adminName: 'operator',
      }),
    }),
    env,
    {},
  );
  const claimBody = await claimResponse.json();
  assert.equal(claimBody.publish_job.state, 'publish_claimed');
  assert.equal(claimBody.publish_job.claimed_by.admin_id, 'admin-2');

  const releaseResponse = await worker.fetch(
    new Request('https://admin.test/api/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'release_publish_claim',
        jobId: 'pub_2026_03_26_120000',
        adminId: 'admin-2',
        reason: 'pause',
      }),
    }),
    env,
    {},
  );
  const releaseBody = await releaseResponse.json();
  assert.equal(releaseBody.publish_job.state, 'publish_released');
  assert.equal(releaseBody.publish_job.claimed_by, null);
  assert.equal(releaseBody.publish_job.release_reason, 'pause');
});


// =========================================================================
// Submission lifecycle tests
// =========================================================================

test('POST /api/submit stores submission with status submitted', async () => {
  const env = makeEnv();

  const response = await worker.fetch(
    new Request('https://admin.test/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        urls: ['https://arxiv.org/pdf/2401.00001'],
        instructions: 'Focus on memory systems',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(body.success, true);
  assert.equal(body.count, 1);

  const keys = [...env.ADMIN_BUCKET.objects.keys()];
  const subKey = keys.find((k) => k.startsWith('submissions/'));
  assert.ok(subKey, 'submission key was written to R2');

  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get(subKey));
  assert.equal(stored.status, 'submitted');
  assert.deepEqual(stored.urls, ['https://arxiv.org/pdf/2401.00001']);
  assert.equal(stored.instructions, 'Focus on memory systems');
  assert.ok(Array.isArray(stored.status_history));
  assert.equal(stored.status_history[0].status, 'submitted');
});


test('GET /api/submissions returns status and key fields', async () => {
  const env = makeEnv({
    admin: {
      'submissions/2026-03-27T10-00-00-000Z.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        instructions: 'test instructions',
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_running',
        claimed_by: 'worker-1',
        status_history: [
          { status: 'submitted', at: '2026-03-27T10:00:00.000Z' },
          { status: 'generation_running', at: '2026-03-27T10:01:00.000Z' },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions'),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(body.submissions.length, 1);
  const sub = body.submissions[0];
  assert.equal(sub.status, 'generation_running');
  assert.equal(sub.claimed_by, 'worker-1');
  assert.equal(sub.key, 'submissions/2026-03-27T10-00-00-000Z.json');
  assert.deepEqual(sub.urls, ['https://arxiv.org/pdf/2401.00001']);
  assert.equal(sub.instructions, 'test instructions');
});


test('POST /api/submissions/status updates submission status', async () => {
  const env = makeEnv({
    admin: {
      'submissions/2026-03-27T10-00-00-000Z.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        status_history: [{ status: 'submitted', at: '2026-03-27T10:00:00.000Z' }],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/2026-03-27T10-00-00-000Z.json',
        status: 'generation_claimed',
        claimed_by: 'worker-1',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(body.success, true);
  assert.equal(body.status, 'generation_claimed');

  const stored = JSON.parse(
    env.ADMIN_BUCKET.objects.get('submissions/2026-03-27T10-00-00-000Z.json'),
  );
  assert.equal(stored.status, 'generation_claimed');
  assert.equal(stored.claimed_by, 'worker-1');
  assert.equal(stored.status_history.length, 2);
  assert.equal(stored.status_history[1].status, 'generation_claimed');
});


test('POST /api/submissions/status rejects invalid status', async () => {
  const env = makeEnv({
    admin: {
      'submissions/test.json': {
        urls: ['https://example.com'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/test.json',
        status: 'invalid_status',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.ok(body.error);
  assert.ok(body.error.includes('Invalid status'));
});


test('POST /api/submissions/status records draft_stem and error', async () => {
  const env = makeEnv({
    admin: {
      'submissions/test.json': {
        urls: ['https://example.com/paper.pdf'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_running',
        status_history: [{ status: 'submitted', at: '2026-03-27T10:00:00.000Z' }],
      },
    },
  });

  // Simulate successful generation
  const successResponse = await worker.fetch(
    new Request('https://admin.test/api/submissions/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/test.json',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-27-example-abc123',
      }),
    }),
    env,
    {},
  );
  const successBody = await successResponse.json();
  assert.equal(successBody.success, true);

  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get('submissions/test.json'));
  assert.equal(stored.draft_stem, 'drafts/2026/03/2026-03-27-example-abc123');
  assert.equal(stored.status, 'draft_generated');
});


test('Queue page renders Pending Generation section for submitted papers', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        bridge: [{ arxiv_id: '2401.00001', title: 'Test', max_axis_score: 0.5 }],
      },
      'submissions/2026-03-27T10-00-00-000Z.json': {
        urls: ['https://arxiv.org/pdf/2401.99999'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Pending Generation'), 'page includes Pending Generation section');
  assert.ok(html.includes('2401.99999'), 'page includes submitted URL');
  assert.ok(html.includes('Submitted'), 'page shows Submitted badge');
  assert.ok(html.includes('1 in generation pipeline'), 'header shows pipeline count');
});


test('Queue page shows Generating section with claimed_by info', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/2026-03-27T10-00-00-000Z.json': {
        urls: ['https://arxiv.org/pdf/2401.55555'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_running',
        claimed_by: 'worker-mcgrof',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Generating'), 'page includes Generating section');
  assert.ok(html.includes('worker-mcgrof'), 'page shows who is generating');
  assert.ok(html.includes('Assigned to'), 'page shows assignment label');
});


test('Queue page hides already-approved submissions from pipeline', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.11111'],
        timestamp: '2026-03-27T09:00:00.000Z',
        status: 'approved_for_publish',
      },
      'submissions/sub2.json': {
        urls: ['https://arxiv.org/pdf/2401.22222'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'published',
      },
      'submissions/sub3.json': {
        urls: ['https://arxiv.org/pdf/2401.33333'],
        timestamp: '2026-03-27T11:00:00.000Z',
        status: 'submitted',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  // Only the submitted one should appear in the pipeline sections
  assert.ok(html.includes('2401.33333'), 'pending submission shown');
  assert.ok(html.includes('1 in generation pipeline'), 'only 1 in pipeline');
  // approved/published should NOT be in the generation sections
  assert.ok(!html.includes('Pending Generation') || !html.includes('2401.11111'),
    'approved submission not in pending generation');
});


test('Queue page filters published papers from editorial sections', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        bridge: [
          { arxiv_id: '2401.00001', title: 'Already Published Paper', max_axis_score: 0.9 },
          { arxiv_id: '2401.00002', title: 'New Paper', max_axis_score: 0.7 },
        ],
      },
      'submissions/pub.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-20T10:00:00.000Z',
        status: 'published',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(!html.includes('Already Published Paper'),
    'published paper should be filtered from editorial queue');
  assert.ok(html.includes('New Paper'),
    'unpublished paper should remain in editorial queue');
});


test('Queue page shows Draft Ready section for draft_generated submissions', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.77777'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-27-test-abc123',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Draft Ready'), 'page includes Draft Ready section');
  assert.ok(html.includes('2401.77777'), 'draft ready submission URL shown');
});


test('Queue page shows Generation Failed section with error info', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.88888'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_failed',
        error: 'PDF extraction failed',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Generation Failed'), 'page includes Failed section');
  assert.ok(html.includes('PDF extraction failed'), 'error message shown');
});


test('GET /submit server-renders submissions without client-side spinner', async () => {
  const env = makeEnv({
    admin: {
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.44444'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('2401.44444'), 'submission URL rendered server-side');
  assert.ok(html.includes('Submitted'), 'status badge rendered server-side');
  // Should NOT have a loading spinner for submissions
  assert.ok(!html.includes('Loading submissions'), 'no client-side loading spinner');
});


test('GET /submit shows empty state when no submissions exist', async () => {
  const env = makeEnv();

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('No submissions yet'), 'shows empty state');
  assert.ok(html.includes('Submit Papers'), 'form is present');
});


test('GET /submit shows claimed_by for in-progress submissions', async () => {
  const env = makeEnv({
    admin: {
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.66666'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_running',
        claimed_by: 'worker-alice',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('worker-alice'), 'claimed_by shown on submit page');
  assert.ok(html.includes('Assigned to'), 'assignment label shown');
});


// =========================================================================
// Corner-case and extended tests
// =========================================================================

test('POST /api/submit rejects empty URL list', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: [] }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.ok(body.error, 'should return an error for empty urls');
});


test('POST /api/submit rejects invalid URL format', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: ['not-a-url'] }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.ok(body.error, 'should return an error for invalid URL');
  assert.ok(body.error.includes('Invalid URL'), 'error mentions invalid URL');
});


test('POST /api/submit stores null instructions when omitted', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: ['https://arxiv.org/pdf/2401.99999'] }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);

  const keys = [...env.ADMIN_BUCKET.objects.keys()];
  const subKey = keys.find((k) => k.startsWith('submissions/'));
  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get(subKey));
  assert.equal(stored.instructions, null, 'instructions should be null when omitted');
});


test('POST /api/submissions/status resets failed submission to submitted for retry', async () => {
  const env = makeEnv({
    admin: {
      'submissions/failed.json': {
        urls: ['https://arxiv.org/pdf/2401.55555'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_failed',
        error: 'PDF extraction failed',
        status_history: [
          { status: 'submitted', at: '2026-03-27T10:00:00.000Z' },
          { status: 'generation_failed', at: '2026-03-27T10:05:00.000Z' },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/failed.json',
        status: 'submitted',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);
  assert.equal(body.status, 'submitted');

  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get('submissions/failed.json'));
  assert.equal(stored.status, 'submitted');
  assert.equal(stored.status_history.length, 3, 'retry adds new history entry');
  assert.equal(stored.status_history[2].status, 'submitted');
});


test('POST /api/submissions/status returns error for missing submission', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/nonexistent.json',
        status: 'generation_claimed',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.ok(body.error, 'should return error for missing submission');
  assert.ok(body.error.includes('not found'), 'error mentions not found');
});


test('Queue page shows "Open" pickup info for unassigned pending submissions', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.12345'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Open'), 'shows open pickup info for unassigned submission');
  assert.ok(html.includes('any generation worker'), 'explains any worker can claim');
});


test('Queue page shows retry button for failed submissions', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.88888'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_failed',
        error: 'Timeout',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Retry'), 'failed submission has retry button');
  assert.ok(html.includes('retrySubmission'), 'retry button calls retrySubmission');
});


test('Queue page filters papers in active generation from editorial sections', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        bridge: [
          { arxiv_id: '2401.00001', title: 'Being Generated', max_axis_score: 0.9 },
          { arxiv_id: '2401.00002', title: 'Still In Queue', max_axis_score: 0.7 },
        ],
      },
      'submissions/gen.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'generation_running',
        claimed_by: 'worker-1',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(!html.includes('Being Generated'),
    'paper in active generation should be filtered from editorial queue');
  assert.ok(html.includes('Still In Queue'),
    'unrelated paper should remain in editorial queue');
});


test('Queue page filters draft_generated papers from editorial sections', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        public: [
          { arxiv_id: '2401.00003', title: 'Draft Already Done', max_axis_score: 0.8 },
        ],
      },
      'submissions/done.json': {
        urls: ['https://arxiv.org/pdf/2401.00003'],
        timestamp: '2026-03-27T09:00:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/test-abc123',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(!html.includes('Draft Already Done'),
    'paper with ready draft should be filtered from editorial sections');
  assert.ok(html.includes('Draft Ready'),
    'paper should appear in Draft Ready section instead');
});


test('Queue page renders correctly with no submissions and no editorial papers', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [], public: [], memory: [] },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Editorial Queue'), 'page title present');
  assert.ok(html.includes('0 editorial papers'), 'shows zero editorial papers');
  assert.ok(html.includes('0 in generation pipeline'), 'shows zero in pipeline');
  assert.ok(!html.includes('Pending Generation'), 'no pending section when empty');
});


// ============================================================================
// METADATA ENRICHMENT TESTS
// ============================================================================

test('POST /api/submit stores metadata with pending enrichment_status', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        urls: ['https://arxiv.org/pdf/2401.00001', 'https://example.com/paper.pdf'],
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);

  // Find the stored submission
  const keys = [...env.ADMIN_BUCKET.objects.keys()].filter(k => k.startsWith('submissions/'));
  assert.equal(keys.length, 1);
  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get(keys[0]));
  assert.ok(stored.metadata, 'metadata field exists');
  assert.equal(stored.metadata['https://arxiv.org/pdf/2401.00001'].enrichment_status, 'pending');
  assert.equal(stored.metadata['https://example.com/paper.pdf'].enrichment_status, 'pending');
});

test('GET /api/submissions returns metadata field', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            title: 'Test Paper Title',
            published: '2024-01-15T00:00:00Z',
            summary: 'A test abstract.',
            arxiv_id: '2401.00001',
            enrichment_status: 'done',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.submissions.length, 1);
  assert.ok(body.submissions[0].metadata, 'metadata returned in API');
  const meta = body.submissions[0].metadata['https://arxiv.org/pdf/2401.00001'];
  assert.equal(meta.title, 'Test Paper Title');
  assert.equal(meta.enrichment_status, 'done');
});

test('Submit page renders enriched paper title instead of raw URL', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            title: 'Attention Is All You Need',
            published: '2024-01-15T00:00:00Z',
            summary: 'We propose a new architecture based on attention mechanisms.',
            arxiv_id: '2401.00001',
            enrichment_status: 'done',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(html.includes('Attention Is All You Need'), 'enriched title shown');
  assert.ok(html.includes('arXiv:2401.00001'), 'arXiv ID shown');
  assert.ok(html.includes('2024-01-15'), 'publication date shown');
  assert.ok(html.includes('attention mechanisms'), 'summary snippet shown');
});

test('Submit page shows enriching state for pending metadata', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            enrichment_status: 'pending',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(html.includes('Enriching metadata'), 'pending enrichment state shown');
});

test('Queue page renders enriched metadata in pending generation section', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { sections: {} },
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            title: 'FlashAttention-3',
            published: '2024-01-20T00:00:00Z',
            summary: 'A faster attention mechanism for transformers.',
            arxiv_id: '2401.00001',
            enrichment_status: 'done',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(html.includes('Pending Generation'), 'pending section exists');
  assert.ok(html.includes('FlashAttention-3'), 'enriched title in queue');
  assert.ok(html.includes('arXiv:2401.00001'), 'arXiv ID in queue');
});

test('Submit page handles submissions without metadata field gracefully', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        // No metadata field — legacy submission
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();
  assert.equal(response.status, 200);
  // Should render the URL as fallback
  assert.ok(html.includes('2401.00001'), 'URL still shown without metadata');
});

test('Submit page renders failed enrichment with fallback', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            enrichment_status: 'failed',
            arxiv_id: '2401.00001',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/submit'),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(html.includes('Metadata unavailable'), 'failed enrichment note shown');
});

test('POST /api/submissions/enrich accepts key parameter', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://example.com/paper.pdf'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://example.com/paper.pdf': { enrichment_status: 'pending' },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'submissions/s1.json' }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);
  assert.equal(body.key, 'submissions/s1.json');
});

test('POST /api/submissions/enrich without key enriches all submissions', async () => {
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://example.com/a.pdf'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: { 'https://example.com/a.pdf': { enrichment_status: 'pending' } },
      },
      'submissions/s2.json': {
        urls: ['https://example.com/b.pdf'],
        timestamp: '2026-03-27T10:01:00.000Z',
        status: 'submitted',
        metadata: { 'https://example.com/b.pdf': { enrichment_status: 'pending' } },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/submissions/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);
  assert.equal(body.count, 2);
});

test('Non-arXiv URL gets unsupported enrichment_status after enrichment', async () => {
  // Simulate what enrichSubmissionMetadata does for non-arXiv URLs
  // by directly checking the stored record after enrichment runs
  const env = makeEnv({
    admin: {
      'submissions/s1.json': {
        urls: ['https://example.com/paper.pdf'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://example.com/paper.pdf': { enrichment_status: 'pending' },
        },
      },
    },
  });

  // Trigger enrichment synchronously (no ctx.waitUntil in tests)
  await worker.fetch(
    new Request('https://admin.test/api/submissions/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: 'submissions/s1.json' }),
    }),
    env,
    {},
  );

  // Check the stored record
  const stored = JSON.parse(env.ADMIN_BUCKET.objects.get('submissions/s1.json'));
  assert.equal(stored.metadata['https://example.com/paper.pdf'].enrichment_status, 'unsupported');
});

test('Queue page handles all submission statuses simultaneously', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { bridge: [] },
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.10001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
      },
      'submissions/s2.json': {
        urls: ['https://arxiv.org/pdf/2401.10002'],
        timestamp: '2026-03-27T10:01:00.000Z',
        status: 'generation_running',
        claimed_by: 'worker-bob',
      },
      'submissions/s3.json': {
        urls: ['https://arxiv.org/pdf/2401.10003'],
        timestamp: '2026-03-27T10:02:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/test',
      },
      'submissions/s4.json': {
        urls: ['https://arxiv.org/pdf/2401.10004'],
        timestamp: '2026-03-27T10:03:00.000Z',
        status: 'generation_failed',
        error: 'OOM',
      },
      'submissions/s5.json': {
        urls: ['https://arxiv.org/pdf/2401.10005'],
        timestamp: '2026-03-27T10:04:00.000Z',
        status: 'published',
      },
      'submissions/s6.json': {
        urls: ['https://arxiv.org/pdf/2401.10006'],
        timestamp: '2026-03-27T10:05:00.000Z',
        status: 'rejected',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  // Pipeline count should include submitted + generating + draft_ready + failed = 4
  assert.ok(html.includes('4 in generation pipeline'), 'pipeline count is 4');
  // Sections present
  assert.ok(html.includes('Pending Generation'), 'pending section present');
  assert.ok(html.includes('Generating'), 'generating section present');
  assert.ok(html.includes('Draft Ready'), 'draft ready section present');
  assert.ok(html.includes('Generation Failed'), 'failed section present');
  // Published and rejected should NOT appear in any pipeline section
  assert.ok(!html.includes('2401.10005'), 'published submission not in pipeline');
  assert.ok(!html.includes('2401.10006'), 'rejected submission not in pipeline');
  // Worker assignment visible
  assert.ok(html.includes('worker-bob'), 'generating worker shown');
});


// ── displayTitle fallback chain ──────────────────────────────────

test('displayTitle prefers enriched paper title over opaque key', () => {
  const item = {
    key: 'submissions/sub_abc.json',
    urls: ['https://arxiv.org/pdf/2401.00001'],
    metadata: {
      'https://arxiv.org/pdf/2401.00001': {
        title: 'Attention Is All You Need',
        enrichment_status: 'done',
      },
    },
  };
  assert.equal(displayTitle(item), 'Attention Is All You Need');
});

test('displayTitle uses explicit title when not opaque', () => {
  assert.equal(
    displayTitle({ title: 'My Great Episode', key: 'drafts/2026/03/ep102.mp3' }),
    'My Great Episode',
  );
});

test('displayTitle rejects opaque ep-NNN title and humanizes slug', () => {
  const item = {
    title: 'ep102',
    key: 'drafts/2026/03/2026-03-27-understanding-kv-caches-abc123.mp3',
  };
  const result = displayTitle(item);
  assert.ok(!OPAQUE_ID_RE.test(result), `title must not be opaque ID, got: ${result}`);
  assert.ok(result.includes('Understanding'), `expected humanized slug, got: ${result}`);
});

test('displayTitle falls back to humanized draft_stem', () => {
  const item = { title: 'ep55', draft_stem: 'drafts/2026/03/2026-03-27-sparse-attention-ff01a2' };
  const result = displayTitle(item);
  assert.ok(!OPAQUE_ID_RE.test(result), `title must not be opaque, got: ${result}`);
  assert.ok(result.includes('Sparse'), `expected humanized slug, got: ${result}`);
});

test('displayTitle returns humanized opaque slug as last resort', () => {
  // No enrichment, opaque title, key only has the opaque stem —
  // humanizeSlug title-cases it since no better data exists.
  const item = { title: 'ep99', key: 'drafts/ep99.mp3' };
  assert.equal(displayTitle(item), 'Ep99');
});

test('humanizeSlug strips date prefix and hash suffix', () => {
  assert.equal(humanizeSlug('2026-03-27-kv-cache-routing-abc123'), 'Kv Cache Routing');
  assert.equal(humanizeSlug('flash-attention'), 'Flash Attention');
  assert.equal(humanizeSlug('ep102'), 'Ep102');
});

// ── Regression: opaque IDs must not appear as draft card titles ───

test('Draft card title shows humanized name, not raw epNNN, when manifest has no title', async () => {
  // Simulate a draft MP3 whose filename is ep102-based but manifest
  // lacks a real title — the admin UI must NOT show "ep102" as the
  // primary card title.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 102,
            // title intentionally omitted — forces fallback
            draft_key: 'drafts/2026/03/2026-03-27-understanding-kv-caches-abc123.mp3',
          },
        ],
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-27-understanding-kv-caches-abc123.mp3': 'audio',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  const title = body.drafts[0].title;
  assert.ok(!OPAQUE_ID_RE.test(title), `draft title must not be opaque ID, got: "${title}"`);
  assert.ok(title.includes('Understanding'), `draft title should be humanized, got: "${title}"`);
});

test('Draft card title does not show bare baseName like ep102 when manifest title is missing', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [] },
    },
    podcast: {
      'drafts/2026/03/ep102.mp3': 'audio',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  // ep102 with no useful slug — should still return "ep102" as last
  // resort but wrapped through displayTitle (no better data exists).
  // The key test: when a BETTER name exists, it must be preferred.
  assert.equal(body.drafts[0].title, 'Ep102');
});

test('Queue page submission card shows enriched title, not internal key', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { sections: {} },
      'submissions/s1.json': {
        urls: ['https://arxiv.org/pdf/2401.00001'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'submitted',
        metadata: {
          'https://arxiv.org/pdf/2401.00001': {
            title: 'FlashAttention-3: Fast Exact Attention',
            enrichment_status: 'done',
          },
        },
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();

  assert.ok(
    html.includes('FlashAttention-3: Fast Exact Attention'),
    'submission card heading must show enriched paper title',
  );
  assert.ok(
    !html.includes('>submissions/s1.json<'),
    'raw R2 key must not appear as card heading',
  );
});

// ── Regression: queue section papers must go through displayTitle ──

test('Queue page editorial paper card uses displayTitle, not raw p.title', async () => {
  // Simulate a queue paper whose title field is an opaque internal ID.
  // The server-side queue card renderer must apply displayTitle() so the
  // opaque ID never reaches the HTML output as the primary card title.
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        public: [
          {
            arxiv_id: '2401.99999',
            title: 'ep102',
            abstract: 'Test abstract',
            key: 'drafts/2026/03/2026-03-27-understanding-kv-caches-abc123.mp3',
          },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();
  // displayTitle must reject "ep102" and fall back to humanized slug
  assert.ok(
    !html.includes('>ep102<'),
    'opaque ep-NNN must not appear as queue card title in HTML',
  );
  assert.ok(
    html.includes('Understanding'),
    'humanized slug should appear as queue card title',
  );
});

test('Queue page editorial paper title is HTML-escaped', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': {
        public: [
          {
            arxiv_id: '2401.99999',
            title: 'Foo <script>alert(1)</script> Bar',
            abstract: 'Test abstract',
          },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(
    !html.includes('<script>alert(1)</script>'),
    'paper title must be HTML-escaped in queue card',
  );
  assert.ok(
    html.includes('&lt;script&gt;'),
    'angle brackets must be escaped',
  );
});

test('Client-side drafts page does not fall back to raw R2 key', async () => {
  // The /api/drafts endpoint applies displayTitle() so draft.title is
  // never null for a valid draft.  But the client-side JS must not fall
  // back to draft.key if title is somehow falsy — it should show
  // "Untitled" instead of a raw R2 path.
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [] },
    },
    podcast: {
      'drafts/2026/03/ep102.mp3': 'audio',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  // The API applies displayTitle, so title should be "Ep102" (humanized),
  // never the raw key "drafts/2026/03/ep102.mp3".
  assert.ok(
    body.drafts[0].title !== 'drafts/2026/03/ep102.mp3',
    'draft title must not be raw R2 key',
  );
});
