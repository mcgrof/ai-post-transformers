import test from 'node:test';
import assert from 'node:assert/strict';

import worker from './worker.js';
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
