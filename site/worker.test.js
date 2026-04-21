import test from 'node:test';
import assert from 'node:assert/strict';

import worker, {
  isAllowedPath, CANONICAL_HOST, WORKERS_DEV_SUFFIX,
  DENIED_PREFIXES, ALLOWED_PREFIXES, ALLOWED_EXACT_FILES,
} from './worker.js';


class MockR2ObjectBody {
  constructor(body, size, httpMetadata = {}) {
    this.body = body;
    this.size = size;
    this.httpMetadata = httpMetadata;
  }

  writeHttpMetadata(headers) {
    if (this.httpMetadata.contentType) {
      headers.set('Content-Type', this.httpMetadata.contentType);
    }
  }
}


class MockBucket {
  constructor(seed = {}) {
    this.objects = new Map();
    for (const [key, value] of Object.entries(seed)) {
      const body = typeof value === 'string' ? value : JSON.stringify(value);
      this.objects.set(key, body);
    }
  }

  async get(key, opts) {
    if (!this.objects.has(key)) return null;
    const body = this.objects.get(key);
    const size = body.length;
    const obj = new MockR2ObjectBody(body, size);
    if (opts && opts.range) {
      obj.range = {
        offset: opts.range.offset || 0,
        length: opts.range.length || (size - (opts.range.offset || 0)),
      };
    }
    return obj;
  }
}


function makeEnv(seed = {}) {
  return { BUCKET: new MockBucket(seed) };
}

function makeRequest(path, opts = {}) {
  const host = opts.host || CANONICAL_HOST;
  const protocol = opts.protocol || 'https';
  const url = `${protocol}://${host}${path}`;
  return new Request(url, {
    headers: opts.headers || {},
  });
}


// ── isAllowedPath unit tests ──

test('isAllowedPath allows known exact files', () => {
  for (const f of ALLOWED_EXACT_FILES) {
    assert.ok(isAllowedPath(f), `expected ${f} to be allowed`);
  }
});

test('isAllowedPath allows known prefixes', () => {
  for (const prefix of ALLOWED_PREFIXES) {
    const key = prefix + 'some-file.mp3';
    assert.ok(isAllowedPath(key), `expected ${key} to be allowed`);
  }
});

test('isAllowedPath denies private/ and other sensitive prefixes', () => {
  for (const denied of DENIED_PREFIXES) {
    const key = denied.endsWith('/') ? denied + 'secret.mp3' : denied;
    assert.ok(!isAllowedPath(key), `expected ${key} to be denied`);
  }
});

test('isAllowedPath denies arbitrary unknown paths', () => {
  assert.ok(!isAllowedPath('admin-stuff/config.json'));
  assert.ok(!isAllowedPath('secret-data.txt'));
  assert.ok(!isAllowedPath('.env'));
  assert.ok(!isAllowedPath('internal/keys.json'));
});

test('isAllowedPath denies private even with nested paths', () => {
  assert.ok(!isAllowedPath('private/owner@test.com/episodes/ep1.mp3'));
  assert.ok(!isAllowedPath('private/abc123/episodes/secret.json'));
  assert.ok(!isAllowedPath('private-drafts/admin/note.json'));
});

test('isAllowedPath denies manifest.json', () => {
  assert.ok(!isAllowedPath('manifest.json'));
});


// ── Canonical host redirect tests ──

test('workers.dev hostname gets 301 redirect to canonical host', async () => {
  const env = makeEnv({ 'index.html': '<html>hi</html>' });
  const req = makeRequest('/feed.xml', { host: 'summer-frog-cfd1.mcgrof.workers.dev' });
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 301);
  const location = resp.headers.get('Location');
  assert.ok(location.startsWith(`https://${CANONICAL_HOST}`));
  assert.ok(location.includes('/feed.xml'));
});

test('workers.dev redirect preserves path and query', async () => {
  const env = makeEnv({});
  const req = makeRequest('/episodes/test.mp3?v=1', { host: 'test.workers.dev' });
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 301);
  const location = resp.headers.get('Location');
  assert.ok(location.includes('/episodes/test.mp3'));
  assert.ok(location.includes('v=1'));
});

test('unknown host returns 404', async () => {
  const env = makeEnv({ 'index.html': 'hi' });
  const req = makeRequest('/', { host: 'evil.example.com' });
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});


// ── Public content serving tests ──

test('root path serves index.html', async () => {
  const env = makeEnv({ 'index.html': '<html>hello</html>' });
  const req = makeRequest('/');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.equal(body, '<html>hello</html>');
});

test('feed.xml is served', async () => {
  const env = makeEnv({ 'feed.xml': '<rss>feed</rss>' });
  const req = makeRequest('/feed.xml');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});

test('public episode audio is served', async () => {
  const env = makeEnv({ 'episodes/2026-03-30-test-abc123.mp3': 'audio-data' });
  const req = makeRequest('/episodes/2026-03-30-test-abc123.mp3');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});

test('viz files are served', async () => {
  const env = makeEnv({ 'viz/chart.html': '<html>viz</html>' });
  const req = makeRequest('/viz/chart.html');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});

test('drafts are served (intentionally public)', async () => {
  const env = makeEnv({ 'drafts/2026/03/test-draft.mp3': 'audio' });
  const req = makeRequest('/drafts/2026/03/test-draft.mp3');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});


// ── Denied path tests ──

test('private/ paths are blocked with 404', async () => {
  const env = makeEnv({ 'private/owner1/episodes/secret.mp3': 'secret-audio' });
  const req = makeRequest('/private/owner1/episodes/secret.mp3');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});

test('private-drafts/ paths are blocked', async () => {
  const env = makeEnv({ 'private-drafts/admin/note.json': '{}' });
  const req = makeRequest('/private-drafts/admin/note.json');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});

test('submissions/ paths are blocked', async () => {
  const env = makeEnv({ 'submissions/sub1.json': '{}' });
  const req = makeRequest('/submissions/sub1.json');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});

test('manifest.json is blocked', async () => {
  const env = makeEnv({ 'manifest.json': '{}' });
  const req = makeRequest('/manifest.json');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});

test('arbitrary unknown paths return 404', async () => {
  const env = makeEnv({ 'secret.txt': 'data' });
  const req = makeRequest('/secret.txt');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});

test('path traversal attempts return 404', async () => {
  const env = makeEnv({ 'index.html': 'hi' });
  const req = makeRequest('/../../../etc/passwd');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});


// ── Range request tests ──

test('range requests work for public audio', async () => {
  const env = makeEnv({ 'episodes/test.mp3': 'abcdefghijklmnop' });
  const req = makeRequest('/episodes/test.mp3', {
    headers: { Range: 'bytes=0-3' },
  });
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 206);
  assert.ok(resp.headers.get('Content-Range'));
});

test('missing objects return 404 even if path is allowed', async () => {
  const env = makeEnv({});
  const req = makeRequest('/feed.xml');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 404);
});


test('year-month archive index.html is served', async () => {
  const env = makeEnv({
    '2026/04/index.html': '<html>April 2026 archive</html>',
  });
  const req = makeRequest('/2026/04/index.html');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.ok(body.includes('April 2026 archive'));
});


test('year-month archive bare directory rewrites to index.html', async () => {
  const env = makeEnv({
    '2026/04/index.html': '<html>April 2026 archive</html>',
  });
  const req = makeRequest('/2026/04/');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.ok(body.includes('April 2026 archive'));
});


test('episode URL without trailing slash serves index.html', async () => {
  const env = makeEnv({
    'episodes/some-episode-slug/index.html': '<html>Some Episode</html>',
  });
  const req = makeRequest('/episodes/some-episode-slug');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.ok(body.includes('Some Episode'));
});


test('episode URL with trailing slash still works', async () => {
  const env = makeEnv({
    'episodes/some-episode-slug/': '<html>Some Episode</html>',
  });
  const req = makeRequest('/episodes/some-episode-slug/');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});


test('conference URL without trailing slash serves index.html', async () => {
  const env = makeEnv({
    'conference/neurips-2025/index.html': '<html>NeurIPS 2025</html>',
  });
  const req = makeRequest('/conference/neurips-2025');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.ok(body.includes('NeurIPS 2025'));
});


test('episode subpath assets are not rewritten', async () => {
  const env = makeEnv({
    'episodes/some-slug/cover.png': 'png-data',
  });
  const req = makeRequest('/episodes/some-slug/cover.png');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
});


test('isAllowedPath accepts year-month archive paths', () => {
  assert.ok(isAllowedPath('2026/04/index.html'));
  assert.ok(isAllowedPath('2025/12/index.html'));
  assert.ok(isAllowedPath('2026/04/'));
  // Reject malformed year-month paths
  assert.ok(!isAllowedPath('2026/4/index.html'));        // single-digit month
  assert.ok(!isAllowedPath('2026/04/something.html'));    // non-index file
  assert.ok(!isAllowedPath('2026/04/secrets.json'));      // non-index file
  assert.ok(!isAllowedPath('20266/04/index.html'));       // 5-digit year
  assert.ok(!isAllowedPath('2026/04/sub/index.html'));    // nested
});


test('sister-podcasts.html is served', async () => {
  const env = makeEnv({
    'sister-podcasts.html': '<html>Sister Podcasts</html>',
  });
  const req = makeRequest('/sister-podcasts.html');
  const resp = await worker.fetch(req, env);
  assert.equal(resp.status, 200);
  const body = await resp.text();
  assert.ok(body.includes('Sister Podcasts'));
});


test('all root-level HTML files uploaded by publish-site are allowlisted', () => {
  // gen-podcast.py _publish_site uploads these four HTML files at
  // the root. Each must be in ALLOWED_EXACT_FILES or the site worker
  // will 404 them even after a successful publish-site run. This is
  // the class of regression that hit us with sister-podcasts.html
  // (which the publisher uploaded but the worker rejected).
  const rootHtmlUploadedByPublishSite = [
    'index.html',
    'sister-podcasts.html',
    'sponsor.html',
    'queue.html',
    'feed.xml',
    'queue.xml',
  ];
  for (const name of rootHtmlUploadedByPublishSite) {
    assert.ok(
      isAllowedPath(name),
      `${name} is uploaded by publish-site but not in the site worker allowlist`
    );
  }
});
