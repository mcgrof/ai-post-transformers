import test from 'node:test';
import assert from 'node:assert/strict';

import worker from './worker.js';


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
