import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

import worker, {
  displayTitle, humanizeSlug, OPAQUE_ID_RE,
  hasPrivateDrafts, listPrivateDrafts, createPrivateDraft,
  updatePrivateDraft, deletePrivateDraft,
} from './worker.js';
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

  async delete(key) {
    this.objects.delete(key);
  }

  async list({ prefix = '', limit } = {}) {
    let keys = [...this.objects.keys()]
      .filter((key) => key.startsWith(prefix));
    if (typeof limit === 'number') keys = keys.slice(0, limit);
    return {
      objects: keys.map((key) => ({ key })),
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


test('POST /api/review reject marks draft revisions and linked state rejected', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 70,
            title: 'Example Draft v1',
            draft_key: 'drafts/2026/03/example-v1.mp3',
            episode_key: 'paper://example',
            revision: 1,
          },
          {
            id: 71,
            title: 'Example Draft v2',
            draft_key: 'drafts/2026/03/example-v2.mp3',
            episode_key: 'paper://example',
            revision: 2,
          },
          {
            id: 99,
            title: 'Other Draft',
            draft_key: 'drafts/2026/03/other.mp3',
            episode_key: 'paper://other',
          },
        ],
      },
      'publish-jobs/pub_2026_03_29_120000.json': {
        job_id: 'pub_2026_03_29_120000',
        draft_key: 'drafts/2026/03/example-v2.mp3',
        title: 'Example Draft v2',
        state: 'approved_for_publish',
        created_at: '2026-03-29T12:00:00Z',
        updated_at: '2026-03-29T12:00:00Z',
        progress: {
          publish: 'pending',
          viz: 'pending',
          cover: 'pending',
          site: 'pending',
          verify: 'pending',
        },
        history: [],
      },
      'submissions/2026-03-29T12-00-00-000Z.json': {
        urls: ['https://arxiv.org/abs/1234.56789'],
        timestamp: '2026-03-29T12:00:00.000Z',
        updated_at: '2026-03-29T12:00:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/example-v2',
        status_history: [
          { status: 'submitted', at: '2026-03-29T12:00:00.000Z' },
          { status: 'draft_generated', at: '2026-03-29T12:10:00.000Z' },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/example-v2.mp3',
        action: 'reject',
        reason: 'Needs a rewrite',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.success, true);
  assert.equal(body.episode_key, 'paper://example');
  assert.deepEqual(body.rejected_keys.sort(), [
    'drafts/2026/03/example-v1.mp3',
    'drafts/2026/03/example-v2.mp3',
  ]);

  const manifest = JSON.parse(env.ADMIN_BUCKET.objects.get('manifest.json'));
  const rejectedDrafts = manifest.drafts.filter((draft) => draft.episode_key === 'paper://example');
  assert.equal(rejectedDrafts.length, 2);
  for (const draft of rejectedDrafts) {
    assert.equal(draft.revision_state, 'rejected');
    assert.equal(draft.rejected_reason, 'Needs a rewrite');
  }
  const otherDraft = manifest.drafts.find((draft) => draft.episode_key === 'paper://other');
  assert.equal(otherDraft.revision_state, undefined);

  const publishJob = JSON.parse(env.ADMIN_BUCKET.objects.get('publish-jobs/pub_2026_03_29_120000.json'));
  assert.equal(publishJob.state, 'publish_rejected');
  assert.equal(publishJob.release_reason, 'Needs a rewrite');
  assert.equal(publishJob.error.step, 'review');

  const submission = JSON.parse(env.ADMIN_BUCKET.objects.get('submissions/2026-03-29T12-00-00-000Z.json'));
  assert.equal(submission.status, 'rejected');
  assert.equal(submission.rejection_reason, 'Needs a rewrite');
  assert.equal(submission.status_history.at(-1).status, 'rejected');
});


test('POST /api/review reject creates a manifest tombstone from sidecar-only drafts', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [] },
    },
    podcast: {
      'drafts/2026/03/sidecar-only.mp3': 'audio-data',
      'drafts/2026/03/sidecar-only.json': JSON.stringify({
        title: 'Sidecar Only Draft',
        description: 'Recovered from sidecar metadata.',
        episode_id: 123,
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/sidecar-only.mp3',
        action: 'reject',
        reason: 'Throw this away',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.success, true);
  const manifest = JSON.parse(env.ADMIN_BUCKET.objects.get('manifest.json'));
  assert.equal(manifest.drafts.length, 1);
  assert.equal(manifest.drafts[0].draft_key, 'drafts/2026/03/sidecar-only.mp3');
  assert.equal(manifest.drafts[0].title, 'Sidecar Only Draft');
  assert.equal(manifest.drafts[0].revision_state, 'rejected');
});


test('POST /api/review reject updates submission without draft_stem via key match', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [] },
      'submissions/2026-03-29T18-38-55-995Z.json': {
        urls: ['https://arxiv.org/pdf/2503.99999'],
        timestamp: '2026-03-29T18:38:55.995Z',
        status: 'draft_generated',
        status_history: [{ status: 'draft_generated', at: '2026-03-29T19:00:00.000Z' }],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'submissions/2026-03-29T18-38-55-995Z.json',
        action: 'reject',
        reason: 'Missing draft stem',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.success, true);

  const submission = JSON.parse(
    env.ADMIN_BUCKET.objects.get('submissions/2026-03-29T18-38-55-995Z.json'),
  );
  assert.equal(submission.status, 'rejected');
  assert.equal(submission.rejection_reason, 'Missing draft stem');
  assert.equal(submission.status_history.at(-1).status, 'rejected');
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

test('GET /drafts server-rendered page includes reject modal for live button flow', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 18,
            title: 'Rejectable Draft',
            draft_key: 'drafts/2026/03/rejectable.mp3',
          },
        ],
      },
    },
    podcast: {
      'drafts/2026/03/rejectable.mp3': 'audio-data',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('id="reject-modal"'), 'server-rendered drafts page must include the reject modal');
  assert.ok(html.includes('id="reject-reason"'), 'server-rendered drafts page must include the reject reason textarea');
  assert.ok(html.includes('openRejectModal('), 'reject button wiring should still be present');
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


test('Queue page shows Draft Ready banner for draft_generated submissions', async () => {
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
  assert.ok(html.includes('Draft Ready'), 'page includes Draft Ready banner');
  assert.ok(html.includes('Review Drafts'), 'banner links to Drafts tab');
  // Draft-generated items are no longer counted in the pipeline
  assert.ok(html.includes('0 in generation pipeline'), 'draft_generated not in pipeline count');
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


test('GET /submit filters out draft_generated submissions', async () => {
  const env = makeEnv({
    admin: {
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/pdf/2401.11111'],
        timestamp: '2026-03-27T09:00:00.000Z',
        status: 'submitted',
      },
      'submissions/sub2.json': {
        urls: ['https://arxiv.org/pdf/2401.22222'],
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-27-ready-abc123',
      },
      'submissions/sub3.json': {
        urls: ['https://arxiv.org/pdf/2401.33333'],
        timestamp: '2026-03-27T11:00:00.000Z',
        status: 'approved_for_publish',
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
  assert.ok(html.includes('2401.11111'), 'submitted item still shown');
  assert.ok(!html.includes('2401.22222'), 'draft_generated filtered out');
  assert.ok(!html.includes('2401.33333'), 'approved_for_publish filtered out');
});


test('GET /drafts shows draft_generated submissions for review with summary description', async () => {
  const env = makeEnv({
    admin: {
      'submissions/sub1.json': {
        urls: ['https://arxiv.org/abs/2401.55555'],
        metadata: {
          'https://arxiv.org/abs/2401.55555': {
            enrichment_status: 'done',
            title: 'Interesting Paper',
            summary: 'Recovered summary for draft-generated submission cards.',
          },
        },
        timestamp: '2026-03-27T10:00:00.000Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-27-ready-abc123',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Draft Review'), 'drafts page renders');
  assert.ok(html.includes('pending review'), 'shows pending review count');
  assert.ok(html.includes('Recovered summary for draft-generated submission cards.'), 'uses submission metadata summary for description');
  assert.ok(html.includes('Interesting Paper'), 'includes source title in rendered sources');
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
  // Pipeline count: submitted + generating + failed = 3
  // (draft_generated moves to Drafts tab, not counted in pipeline)
  assert.ok(html.includes('3 in generation pipeline'), 'pipeline count is 3');
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

test('displayTitle never exposes opaque ep-NNN slug as last resort', () => {
  const item = { title: 'ep99', key: 'drafts/ep99.mp3' };
  assert.equal(displayTitle(item), 'Draft episode');
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
  assert.equal(body.drafts[0].title, 'Draft episode');
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

test('Queue page submission card uses URL fallback instead of opaque epNNN title', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': { sections: {} },
      'submissions/s1.json': {
        title: 'ep102',
        draft_stem: 'ep102',
        urls: ['https://arxiv.org/pdf/2603.17187'],
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

  assert.ok(html.includes('arXiv 2603.17187'));
  assert.ok(!html.includes('>Ep102<'));
  assert.ok(!html.includes('>ep102<'));
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


// ---------------------------------------------------------------------------
// Stale draft suppression via publish_completed job status
// ---------------------------------------------------------------------------

test('GET /api/drafts filters out drafts with publish_completed publish job', async () => {
  // Simulate a draft whose R2 MP3 still exists (cleanup failed) but
  // whose publish job has already completed successfully.
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [] },
      'publish-jobs/pub_2026_03_20_120000.json': JSON.stringify({
        job_id: 'pub_2026_03_20_120000',
        draft_key: 'drafts/leworldmodel.mp3',
        state: 'publish_completed',
        created_at: '2026-03-20T12:00:00Z',
        progress: { publish: 'done', viz: 'done', cover: 'done', site: 'done', verify: 'done' },
      }),
    },
    podcast: {
      'drafts/leworldmodel.mp3': 'audio-bytes',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  // The draft should be filtered out because its publish job is completed
  assert.equal(body.drafts.length, 0,
    'draft with publish_completed job should not appear in active drafts');
});


test('GET /api/drafts keeps drafts with pending publish job', async () => {
  // A draft whose publish job is still approved (not completed)
  // should remain visible.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [{ id: 50, title: 'Good Draft', date: '2026-03-25' }],
      },
      'publish-jobs/pub_2026_03_25_100000.json': JSON.stringify({
        job_id: 'pub_2026_03_25_100000',
        draft_key: 'drafts/ep50.mp3',
        state: 'approved_for_publish',
        created_at: '2026-03-25T10:00:00Z',
        progress: { publish: 'pending', viz: 'pending', cover: 'pending', site: 'pending', verify: 'pending' },
      }),
    },
    podcast: {
      'drafts/ep50.mp3': 'audio-bytes',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1,
    'draft with approved_for_publish job should remain visible');
});


// ---------------------------------------------------------------------------
// Queue editorial filtering includes submitted/pending submissions
// ---------------------------------------------------------------------------

test('Queue page filters editorial papers that match submitted submissions', async () => {
  const env = makeEnv({
    admin: {
      'queue/latest.json': JSON.stringify({
        bridge: [
          { arxiv_id: '2401.99999', title: 'Already Submitted Paper', abstract: 'Test' },
          { arxiv_id: '2401.88888', title: 'Untouched Paper', abstract: 'Test' },
        ],
      }),
      'submissions/sub1.json': JSON.stringify({
        urls: ['https://arxiv.org/pdf/2401.99999'],
        status: 'submitted',
        timestamp: '2026-03-27T10:00:00Z',
      }),
    },
    podcast: {},
  });

  const response = await worker.fetch(
    new Request('https://admin.test/queue'),
    env,
    {},
  );
  const html = await response.text();
  // The submitted paper should be filtered from editorial sections
  assert.ok(!html.includes('Already Submitted Paper'),
    'paper with submitted status should be filtered from editorial queue');
  assert.ok(html.includes('Untouched Paper'),
    'paper without submission should remain in editorial queue');
});


// ---------------------------------------------------------------------------
// Sidecar JSON fallback for drafts without manifest entries
// ---------------------------------------------------------------------------

test('GET /api/drafts uses sidecar JSON title/description when manifest entry is missing', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {
      'drafts/2026/03/2026-03-27-sparse-attention-ff01a2.mp3': 'audio',
      'drafts/2026/03/2026-03-27-sparse-attention-ff01a2.json': JSON.stringify({
        title: 'Sparse Attention Is All You Need',
        description: 'This episode explores sparse attention mechanisms.',
        source_urls: ['https://arxiv.org/pdf/2503.12345'],
        episode_id: 111,
        script: [],
        sources: [],
        topics: ['attention'],
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  const draft = body.drafts[0];
  assert.equal(draft.title, 'Sparse Attention Is All You Need');
  assert.equal(draft.description, 'This episode explores sparse attention mechanisms.');
  assert.equal(draft.episodeId, 111);
});


test('GET /api/drafts prefers manifest over sidecar when both exist', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [{
          id: 111,
          title: 'Manifest Title Wins',
          description: 'Manifest description.',
          draft_key: 'drafts/2026/03/2026-03-27-sparse-attention-ff01a2.mp3',
          filename: '2026-03-27-sparse-attention-ff01a2.mp3',
          basename: '2026-03-27-sparse-attention-ff01a2',
          date: '2026-03-27',
        }],
        conferences: {},
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-27-sparse-attention-ff01a2.mp3': 'audio',
      'drafts/2026/03/2026-03-27-sparse-attention-ff01a2.json': JSON.stringify({
        title: 'Sidecar Title Should Not Win',
        description: 'Sidecar description.',
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  assert.equal(body.drafts[0].title, 'Manifest Title Wins');
  assert.equal(body.drafts[0].description, 'Manifest description.');
});


test('GET /api/drafts gracefully handles missing sidecar JSON', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {
      'drafts/2026/03/2026-03-27-orphan-abc123.mp3': 'audio',
      // No sidecar JSON exists
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  // Title should fall back to displayTitle() humanization
  assert.ok(body.drafts[0].title, 'draft should still have a title from displayTitle fallback');
  assert.equal(body.drafts[0].description, '');
});

test('GET /drafts falls back to sidecar description when manifest entry is incomplete', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI and the Next Intelligence Explosion',
            draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3',
            description: '',
          },
        ],
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3': 'audio-data',
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.json': JSON.stringify({
        title: 'Agentic AI and the Next Intelligence Explosion',
        description: 'Recovered description from the draft sidecar JSON.',
        source_urls: ['https://arxiv.org/abs/2602.15902'],
        episode_id: 113,
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Recovered description from the draft sidecar JSON.'));
});

test('GET /api/drafts falls back to sidecar description when manifest has empty description', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI and the Next Intelligence Explosion',
            draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3',
            filename: '2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3',
            basename: '2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561',
            description: '',
          },
        ],
        conferences: {},
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3': 'audio-data',
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.json': JSON.stringify({
        title: 'Agentic AI and the Next Intelligence Explosion',
        description: 'Sidecar description fills the gap.',
        source_urls: ['https://arxiv.org/abs/2602.15902'],
        episode_id: 113,
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.drafts.length, 1);
  assert.equal(body.drafts[0].title, 'Agentic AI and the Next Intelligence Explosion');
  assert.equal(body.drafts[0].description, 'Sidecar description fills the gap.');
});


test('GET /drafts still renders bucket draft metadata when manifest draft key is stale', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI and the Next Intelligence Explosion',
            draft_key: 'public/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3',
            description: '',
          },
        ],
      },
      'submissions/agentic.json': {
        urls: ['https://arxiv.org/abs/2603.20639'],
        metadata: {
          'https://arxiv.org/abs/2603.20639': {
            enrichment_status: 'done',
            title: 'Agentic AI and the Next Intelligence Explosion',
            summary: 'Recovered submission summary for the Agentic AI draft card.',
          },
        },
        timestamp: '2026-03-28T20:02:42.230Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561',
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.mp3': 'audio-data',
      'drafts/2026/03/2026-03-28-agentic-ai-and-the-next-intelligence-exp-d06561.json': JSON.stringify({
        title: 'Agentic AI and the Next Intelligence Explosion',
        description: 'Recovered sidecar description.',
        source_urls: ['https://arxiv.org/abs/2603.20639'],
        episode_id: 113,
      }),
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.includes('Recovered sidecar description.'));
  assert.ok(html.includes('Agentic AI and the Next Intelligence Explosion'));
});


// ============================================================================
// Private Drafts — owner-scoped, never public
// ============================================================================

test('hasPrivateDrafts returns false when admin has none', async () => {
  const env = makeEnv();
  assert.equal(await hasPrivateDrafts(env, 'alice'), false);
});

test('hasPrivateDrafts returns true when admin has drafts', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'My note' } },
  });
  assert.equal(await hasPrivateDrafts(env, 'alice'), true);
});

test('hasPrivateDrafts returns false for different admin', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'My note' } },
  });
  assert.equal(await hasPrivateDrafts(env, 'bob'), false);
});

test('createPrivateDraft stores owner-scoped draft', async () => {
  const env = makeEnv();
  const result = await createPrivateDraft(env, 'alice', 'alice@example.com', {
    title: 'Test note',
    content: 'Some private content',
  });
  assert.ok(result.success);
  assert.equal(result.draft.owner_id, 'alice');
  assert.equal(result.draft.title, 'Test note');

  // Verify stored in admin bucket under correct prefix
  const stored = await env.ADMIN_BUCKET.get(`private-drafts/alice/${result.draft.id}.json`);
  assert.ok(stored);
  const data = await stored.json();
  assert.equal(data.owner_id, 'alice');
});

test('listPrivateDrafts returns only own drafts', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Alice note', updated_at: '2026-01-01T00:00:00Z' },
      'private-drafts/bob/pd_2.json': { id: 'pd_2', owner_id: 'bob', title: 'Bob note', updated_at: '2026-01-02T00:00:00Z' },
    },
  });
  const aliceDrafts = await listPrivateDrafts(env, 'alice');
  assert.equal(aliceDrafts.length, 1);
  assert.equal(aliceDrafts[0].title, 'Alice note');

  const bobDrafts = await listPrivateDrafts(env, 'bob');
  assert.equal(bobDrafts.length, 1);
  assert.equal(bobDrafts[0].title, 'Bob note');
});

test('updatePrivateDraft rejects wrong owner', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Alice note' } },
  });
  const result = await updatePrivateDraft(env, 'bob', { id: 'pd_1' });
  assert.ok(result.error);
  assert.match(result.error, /not found/i);
});

test('updatePrivateDraft updates own draft', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Old', content: 'old' } },
  });
  const result = await updatePrivateDraft(env, 'alice', { id: 'pd_1', title: 'New', content: 'new' });
  assert.ok(result.success);
  assert.equal(result.draft.title, 'New');
  assert.equal(result.draft.content, 'new');
});

test('deletePrivateDraft removes own draft', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Note' } },
  });
  const result = await deletePrivateDraft(env, 'alice', { id: 'pd_1' });
  assert.ok(result.success);

  const remaining = await listPrivateDrafts(env, 'alice');
  assert.equal(remaining.length, 0);
});

test('deletePrivateDraft rejects wrong owner', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Note' } },
  });
  const result = await deletePrivateDraft(env, 'bob', { id: 'pd_1' });
  assert.ok(result.error);
});

test('GET /api/private-drafts returns only current admin drafts', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Alice secret', updated_at: '2026-01-01T00:00:00Z' },
      'private-drafts/bob/pd_2.json': { id: 'pd_2', owner_id: 'bob', title: 'Bob secret', updated_at: '2026-01-02T00:00:00Z' },
    },
  });
  const response = await worker.fetch(
    new Request('https://admin.test/api/private-drafts', {
      headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
    }),
    env,
    {},
  );
  const data = JSON.parse(await response.text());
  assert.equal(data.drafts.length, 1);
  assert.equal(data.drafts[0].title, 'Alice secret');
});

test('POST /api/private-drafts create stores draft for current admin only', async () => {
  const env = makeEnv();
  const response = await worker.fetch(
    new Request('https://admin.test/api/private-drafts', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'cf-access-authenticated-user-email': 'alice@example.com',
      },
      body: JSON.stringify({ action: 'create', title: 'My secret note', content: 'Private content' }),
    }),
    env,
    {},
  );
  const data = JSON.parse(await response.text());
  assert.ok(data.success);
  assert.equal(data.draft.owner_id, 'alice');

  // Bob cannot see it
  const bobList = await listPrivateDrafts(env, 'bob');
  assert.equal(bobList.length, 0);
});

test('private drafts never appear in getDrafts or manifest', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Private note' },
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {},
  });

  // getDrafts must not include private drafts
  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const data = JSON.parse(await response.text());
  const titles = (data.drafts || []).map(d => d.title);
  assert.ok(!titles.includes('Private note'), 'Private draft must not appear in /api/drafts');
});

test('private drafts never appear in delegation export', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Secret' },
    },
  });
  const response = await worker.fetch(
    new Request('https://admin.test/api/delegation'),
    env,
    {},
  );
  const text = await response.text();
  assert.ok(!text.includes('Secret'), 'Private draft must not leak into delegation');
});

test('/private-drafts page shows Private Drafts tab when admin has drafts', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'My note', content: 'Content', updated_at: '2026-03-30T12:00:00Z' },
    },
  });
  const response = await worker.fetch(
    new Request('https://admin.test/private-drafts', {
      headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
    }),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(html.includes('Private Drafts'), 'Page should render Private Drafts heading');
  assert.ok(html.includes('My note'), 'Page should show the draft title');
  assert.ok(html.includes('never published'), 'Page should state drafts are never published');
  // Nav should contain the Private Drafts link
  assert.ok(html.includes('href="/private-drafts"'), 'Nav should include Private Drafts link');
});

test('nav hides Private Drafts tab when admin has no private drafts', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {},
  });
  const response = await worker.fetch(
    new Request('https://admin.test/drafts', {
      headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
    }),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(!html.includes('href="/private-drafts"'), 'Nav must not show Private Drafts tab when admin has none');
});

test('nav shows Private Drafts tab only for owning admin', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Secret' },
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {},
  });

  // Alice sees the tab
  const aliceRes = await worker.fetch(
    new Request('https://admin.test/drafts', {
      headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
    }),
    env,
    {},
  );
  const aliceHtml = await aliceRes.text();
  assert.ok(aliceHtml.includes('href="/private-drafts"'), 'Alice should see Private Drafts tab');

  // Bob does not see the tab
  const bobRes = await worker.fetch(
    new Request('https://admin.test/drafts', {
      headers: { 'cf-access-authenticated-user-email': 'bob@example.com' },
    }),
    env,
    {},
  );
  const bobHtml = await bobRes.text();
  assert.ok(!bobHtml.includes('href="/private-drafts"'), 'Bob must not see Private Drafts tab');
});

test('private drafts persist until explicitly deleted', async () => {
  const env = makeEnv();

  // Create a draft
  await createPrivateDraft(env, 'alice', 'alice@example.com', { title: 'Persistent', content: 'stays' });
  let drafts = await listPrivateDrafts(env, 'alice');
  assert.equal(drafts.length, 1);

  // Create another — both persist
  await createPrivateDraft(env, 'alice', 'alice@example.com', { title: 'Also persistent', content: 'stays too' });
  drafts = await listPrivateDrafts(env, 'alice');
  assert.equal(drafts.length, 2);

  // Delete one — the other persists
  await deletePrivateDraft(env, 'alice', { id: drafts[0].id });
  drafts = await listPrivateDrafts(env, 'alice');
  assert.equal(drafts.length, 1);
});



// =========================================================================
// Regression: stale published-draft resurfacing (GH fix-stale-published-drafts)
// =========================================================================

test('POST /api/review approve advances linked submission to approved_for_publish', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI',
            draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
          },
        ],
      },
      'submissions/2026-03-28T20-02-42-230Z.json': {
        urls: ['https://arxiv.org/abs/2603.20639'],
        timestamp: '2026-03-28T20:02:42.230Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-28-agentic-ai-d06561',
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
        action: 'approve',
        adminId: 'admin-1',
        adminName: 'mcgrof',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);

  // The linked submission must have advanced to approved_for_publish.
  const subRaw = env.ADMIN_BUCKET.objects.get(
    'submissions/2026-03-28T20-02-42-230Z.json',
  );
  const sub = JSON.parse(subRaw);
  assert.equal(sub.status, 'approved_for_publish',
    'submission must advance to approved_for_publish on draft approval');
  assert.ok(
    sub.status_history.some((h) => h.status === 'approved_for_publish'),
    'status_history must record the transition',
  );
});


test('GET /drafts hides submission card when publish job is publish_completed', async () => {
  // Scenario: publish job completed but submission was never advanced
  // (pre-fix state).  The Drafts page must not show the stale
  // submission as a draft card.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI and the Next Intelligence Explosion',
            draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
          },
        ],
      },
      'publish-jobs/pub_2026_03_29_180606.json': {
        job_id: 'pub_2026_03_29_180606',
        draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
        draft_stem: 'drafts/2026/03/2026-03-28-agentic-ai-d06561',
        title: 'Agentic AI and the Next Intelligence Explosion',
        state: 'publish_completed',
        created_at: '2026-03-29T18:06:06Z',
        updated_at: '2026-03-29T19:00:00Z',
        progress: {
          publish: 'done', viz: 'done', cover: 'done',
          site: 'done', verify: 'done',
        },
      },
      'submissions/2026-03-28T20-02-42-230Z.json': {
        urls: ['https://arxiv.org/abs/2603.20639'],
        metadata: {
          'https://arxiv.org/abs/2603.20639': {
            enrichment_status: 'done',
            title: 'Agentic AI and the Next Intelligence Explosion',
          },
        },
        timestamp: '2026-03-28T20:02:42.230Z',
        status: 'draft_generated',
        draft_stem: 'drafts/2026/03/2026-03-28-agentic-ai-d06561',
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3': 'audio-data',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/drafts'),
    env,
    {},
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  // The bucket draft itself should be filtered out by getDrafts()
  // (publish_completed).  The submission-card fallback must also be
  // suppressed.
  assert.ok(
    !html.includes('Agentic AI and the Next Intelligence Explosion'),
    'published episode must not reappear as a draft card via submission fallback',
  );
  // The page should show "no pending drafts" or an empty list.
  assert.ok(
    html.includes('No pending drafts') || html.includes('All caught up'),
    'drafts page should be empty when only draft is already published',
  );
});


test('GET /api/drafts filters completed drafts from submission-card fallback path', async () => {
  // Same scenario but via the JSON API.  The API path (getDrafts)
  // already filters publish_completed bucket drafts.  This test
  // verifies that no stale submission card leaks through.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 113,
            title: 'Agentic AI',
            draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
          },
        ],
      },
      'publish-jobs/pub_2026_03_29_180606.json': {
        job_id: 'pub_2026_03_29_180606',
        draft_key: 'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3',
        draft_stem: 'drafts/2026/03/2026-03-28-agentic-ai-d06561',
        title: 'Agentic AI',
        state: 'publish_completed',
        created_at: '2026-03-29T18:06:06Z',
        updated_at: '2026-03-29T19:00:00Z',
        progress: {
          publish: 'done', viz: 'done', cover: 'done',
          site: 'done', verify: 'done',
        },
      },
    },
    podcast: {
      'drafts/2026/03/2026-03-28-agentic-ai-d06561.mp3': 'audio-data',
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/drafts'),
    env,
    {},
  );
  const body = await response.json();

  assert.equal(body.drafts.length, 0,
    'API must not return drafts with completed publish jobs');
});


// =========================================================================
// Additional coverage: private drafts isolation & edge cases
// =========================================================================

test('hasPrivateDrafts returns false for null adminId', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Note' } },
  });
  assert.equal(await hasPrivateDrafts(env, null), false);
  assert.equal(await hasPrivateDrafts(env, undefined), false);
});

test('listPrivateDrafts returns empty for null adminId', async () => {
  const env = makeEnv({
    admin: { 'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'Note', updated_at: '2026-01-01T00:00:00Z' } },
  });
  const drafts = await listPrivateDrafts(env, null);
  assert.equal(drafts.length, 0);
});

test('private drafts do not appear on the main /drafts page HTML', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'PRIVATE_SENTINEL_TITLE', content: 'secret' },
      'manifest.json': { drafts: [], conferences: {} },
    },
    podcast: {},
  });
  const response = await worker.fetch(
    new Request('https://admin.test/drafts', {
      headers: { 'cf-access-authenticated-user-email': 'alice@example.com' },
    }),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(!html.includes('PRIVATE_SENTINEL_TITLE'),
    'Private draft title must never appear on the main Drafts page');
});

test('private drafts do not appear in /api/queue', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': { id: 'pd_1', owner_id: 'alice', title: 'QUEUE_LEAK_SENTINEL' },
    },
  });
  const response = await worker.fetch(
    new Request('https://admin.test/api/queue'),
    env,
    {},
  );
  const text = await response.text();
  assert.ok(!text.includes('QUEUE_LEAK_SENTINEL'),
    'Private draft must not leak into /api/queue');
});

test('/private-drafts page for bob shows none of alice drafts', async () => {
  const env = makeEnv({
    admin: {
      'private-drafts/alice/pd_1.json': {
        id: 'pd_1', owner_id: 'alice', title: 'Alice Only Note',
        content: 'Alice private content', updated_at: '2026-01-01T00:00:00Z',
      },
    },
  });
  const response = await worker.fetch(
    new Request('https://admin.test/private-drafts', {
      headers: { 'cf-access-authenticated-user-email': 'bob@example.com' },
    }),
    env,
    {},
  );
  const html = await response.text();
  assert.ok(!html.includes('Alice Only Note'),
    'Bob must not see Alice\'s private draft on /private-drafts page');
  assert.ok(html.includes('No private drafts'),
    'Bob should see empty state on /private-drafts');
});

test('updatePrivateDraft with missing id returns error', async () => {
  const env = makeEnv();
  const result = await updatePrivateDraft(env, 'alice', {});
  assert.ok(result.error);
  assert.match(result.error, /missing/i);
});

test('deletePrivateDraft with missing id returns error', async () => {
  const env = makeEnv();
  const result = await deletePrivateDraft(env, 'alice', {});
  assert.ok(result.error);
  assert.match(result.error, /missing/i);
});


// =========================================================================
// Additional coverage: stale published drafts — lifecycle edge cases
// =========================================================================

test('advanceLinkedSubmissions does not regress already-published submission', async () => {
  // A submission already at 'published' must not be regressed to
  // 'approved_for_publish' by a second approval action.
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 114,
            title: 'Already Published Paper',
            draft_key: 'drafts/2026/03/2026-03-30-already-published-abc123.mp3',
          },
        ],
      },
      'submissions/2026-03-30T10-00-00-000Z.json': {
        urls: ['https://arxiv.org/abs/2603.99999'],
        timestamp: '2026-03-30T10:00:00.000Z',
        status: 'published',
        draft_stem: 'drafts/2026/03/2026-03-30-already-published-abc123',
        status_history: [
          { status: 'submitted', at: '2026-03-30T10:00:00.000Z' },
          { status: 'draft_generated', at: '2026-03-30T11:00:00.000Z' },
          { status: 'approved_for_publish', at: '2026-03-30T12:00:00.000Z' },
          { status: 'published', at: '2026-03-30T13:00:00.000Z' },
        ],
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/2026-03-30-already-published-abc123.mp3',
        action: 'approve',
        adminId: 'admin-1',
        adminName: 'mcgrof',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);

  // Verify the submission was NOT regressed from published.
  const subRaw = env.ADMIN_BUCKET.objects.get(
    'submissions/2026-03-30T10-00-00-000Z.json',
  );
  const sub = JSON.parse(subRaw);
  assert.equal(sub.status, 'published',
    'already-published submission must not be regressed to approved_for_publish');
});

test('submission without draft_stem is not affected by advanceLinkedSubmissions', async () => {
  const env = makeEnv({
    admin: {
      'manifest.json': {
        drafts: [
          {
            id: 115,
            title: 'Some Draft',
            draft_key: 'drafts/2026/03/2026-03-30-some-draft-def456.mp3',
          },
        ],
      },
      'submissions/2026-03-30T11-00-00-000Z.json': {
        urls: ['https://arxiv.org/abs/2603.11111'],
        timestamp: '2026-03-30T11:00:00.000Z',
        status: 'submitted',
        // No draft_stem — this submission is unrelated to the draft
      },
    },
  });

  const response = await worker.fetch(
    new Request('https://admin.test/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'drafts/2026/03/2026-03-30-some-draft-def456.mp3',
        action: 'approve',
        adminId: 'admin-1',
        adminName: 'mcgrof',
      }),
    }),
    env,
    {},
  );
  const body = await response.json();
  assert.equal(body.success, true);

  // The unrelated submission must remain at 'submitted'.
  const subRaw = env.ADMIN_BUCKET.objects.get(
    'submissions/2026-03-30T11-00-00-000Z.json',
  );
  const sub = JSON.parse(subRaw);
  assert.equal(sub.status, 'submitted',
    'submission without draft_stem must not be modified');
});
