import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  CAPABILITIES,
  loadAdmins,
  saveAdmins,
  capabilitiesFor,
  hasCapability,
  validateAdminInput,
  upsertAdmin,
  removeAdmin,
  cfAccessConfigured,
  emailsInPolicy,
  addToCFAccess,
  removeFromCFAccess,
} from './admins.js';

// Minimal in-memory R2 mock that mirrors the bits of the
// Cloudflare Workers R2 binding we use here.
function mockBucket(initial = new Map()) {
  return {
    objects: initial,
    async get(key) {
      const v = this.objects.get(key);
      if (v === undefined) return null;
      return {
        async text() { return v; },
        async json() { return JSON.parse(v); },
      };
    },
    async put(key, body) { this.objects.set(key, body); },
    async delete(key) { this.objects.delete(key); },
  };
}

// ---------------------------------------------------------------- capabilities

test('CAPABILITIES vocabulary pins the known capability set', () => {
  // Adding a capability should be a deliberate code change with a
  // matching test update — never silent. This guards against
  // accidental renames or drops.
  assert.deepEqual(
    [...CAPABILITIES].sort(),
    ['admin', 'manage_admins', 'publish', 'queue_refresh', 'submit'].sort()
  );
});

test('validateAdminInput accepts the submit capability', () => {
  const out = validateAdminInput('co@host.com', ['submit']);
  assert.deepEqual(out.capabilities, ['submit']);
});

test('admin capability implies submit', () => {
  const admins = [{ email: 'boss@x.com', capabilities: ['admin'] }];
  assert.ok(hasCapability({ email: 'boss@x.com' }, 'submit', admins));
});

// ---------------------------------------------------------------- allowlist

test('loadAdmins returns empty doc when admins.json missing', async () => {
  const env = { ADMIN_BUCKET: mockBucket() };
  const doc = await loadAdmins(env);
  assert.equal(doc.version, 1);
  assert.deepEqual(doc.admins, []);
});

test('saveAdmins + loadAdmins round-trip', async () => {
  const env = { ADMIN_BUCKET: mockBucket() };
  await saveAdmins(env, {
    admins: [{ email: 'a@x.com', capabilities: ['admin'] }],
  });
  const doc = await loadAdmins(env);
  assert.equal(doc.admins.length, 1);
  assert.equal(doc.admins[0].email, 'a@x.com');
});

test('capabilitiesFor: bootstrap — empty admins.json grants full caps', () => {
  const caps = capabilitiesFor({ email: 'first@gmail.com' }, []);
  for (const c of CAPABILITIES) assert.ok(caps.has(c), 'missing ' + c);
});

test('capabilitiesFor: bootstrap does NOT apply once any admin exists', () => {
  const admins = [{ email: 'owner@x.com', capabilities: ['admin'] }];
  const caps = capabilitiesFor({ email: 'someone-else@x.com' }, admins);
  assert.equal(caps.size, 0);
});

test('capabilitiesFor: admin capability implies all others', () => {
  const admins = [{ email: 'boss@x.com', capabilities: ['admin'] }];
  const caps = capabilitiesFor({ email: 'boss@x.com' }, admins);
  for (const c of CAPABILITIES) assert.ok(caps.has(c));
});

test('capabilitiesFor: explicit caps are honored without implying admin', () => {
  const admins = [
    { email: 'pub@x.com', capabilities: ['publish'] },
    { email: 'admin@x.com', capabilities: ['admin'] },
  ];
  assert.ok(hasCapability({ email: 'pub@x.com' }, 'publish', admins));
  assert.ok(!hasCapability({ email: 'pub@x.com' }, 'manage_admins', admins));
  assert.ok(hasCapability({ email: 'admin@x.com' }, 'manage_admins', admins));
});

test('capabilitiesFor: email match is case-insensitive', () => {
  const admins = [{ email: 'Mixed@CASE.com', capabilities: ['publish'] }];
  assert.ok(hasCapability({ email: 'mixed@case.COM' }, 'publish', admins));
});

test('capabilitiesFor: missing email returns empty set', () => {
  assert.equal(
    capabilitiesFor({ email: '' }, [{ email: 'x@x.com', capabilities: ['admin'] }]).size,
    0
  );
});

// ---------------------------------------------------------------- validation

test('validateAdminInput: rejects bad email', () => {
  assert.throws(() => validateAdminInput('not-an-email', ['admin']), /invalid email/);
  assert.throws(() => validateAdminInput('', ['admin']), /invalid email/);
  assert.throws(() => validateAdminInput('a@b', ['admin']), /invalid email/);
});

test('validateAdminInput: rejects unknown capability', () => {
  assert.throws(
    () => validateAdminInput('a@b.com', ['admin', 'superuser']),
    /unknown capability: superuser/
  );
});

test('validateAdminInput: rejects empty capability array', () => {
  assert.throws(() => validateAdminInput('a@b.com', []), /non-empty/);
});

test('validateAdminInput: normalizes + dedupes capabilities', () => {
  const out = validateAdminInput(' Foo@BAR.com ', [' publish', 'publish', 'admin']);
  assert.equal(out.email, 'foo@bar.com');
  assert.deepEqual(out.capabilities.sort(), ['admin', 'publish']);
});

// ---------------------------------------------------------------- CRUD

test('upsertAdmin: replaces existing entry instead of duplicating', () => {
  let doc = { version: 1, admins: [] };
  doc = upsertAdmin(doc, { email: 'a@x.com', capabilities: ['publish'] }, 'me@x.com');
  doc = upsertAdmin(doc, { email: 'A@X.com', capabilities: ['admin'] }, 'me@x.com');
  assert.equal(doc.admins.length, 1);
  assert.deepEqual(doc.admins[0].capabilities, ['admin']);
});

test('upsertAdmin: sets added_by + added_at + notes', () => {
  let doc = { version: 1, admins: [] };
  doc = upsertAdmin(doc, { email: 'a@x.com', capabilities: ['publish'] },
                    'owner@x.com', 'co-host');
  assert.equal(doc.admins[0].added_by, 'owner@x.com');
  assert.equal(doc.admins[0].notes, 'co-host');
  assert.ok(doc.admins[0].added_at);
});

test('upsertAdmin: keeps list sorted by email for stable diffs', () => {
  let doc = { version: 1, admins: [] };
  doc = upsertAdmin(doc, { email: 'c@x.com', capabilities: ['admin'] }, 'm');
  doc = upsertAdmin(doc, { email: 'a@x.com', capabilities: ['admin'] }, 'm');
  doc = upsertAdmin(doc, { email: 'b@x.com', capabilities: ['admin'] }, 'm');
  assert.deepEqual(
    doc.admins.map(a => a.email),
    ['a@x.com', 'b@x.com', 'c@x.com']
  );
});

test('removeAdmin: removes by email (case-insensitive) and reports', () => {
  const doc = {
    version: 1,
    admins: [
      { email: 'a@x.com', capabilities: ['admin'] },
      { email: 'B@x.com', capabilities: ['publish'] },
    ],
  };
  const { doc: after, removed } = removeAdmin(doc, 'b@x.com');
  assert.equal(removed, true);
  assert.equal(after.admins.length, 1);
  assert.equal(after.admins[0].email, 'a@x.com');
});

test('removeAdmin: no-op on unknown email', () => {
  const doc = { version: 1, admins: [{ email: 'a@x.com', capabilities: ['admin'] }] };
  const { removed } = removeAdmin(doc, 'who@x.com');
  assert.equal(removed, false);
});

// ---------------------------------------------------------------- CF Access

test('cfAccessConfigured: false unless all four env vars are set', () => {
  assert.equal(cfAccessConfigured({}), false);
  assert.equal(cfAccessConfigured({
    CF_API_TOKEN: 't', CF_ACCOUNT_ID: 'a',
  }), false);
  assert.equal(cfAccessConfigured({
    CF_API_TOKEN: 't', CF_ACCOUNT_ID: 'a',
    CF_ACCESS_APP_UUID: 'u', CF_ACCESS_POLICY_UUID: 'p',
  }), true);
});

test('emailsInPolicy: extracts lowercased emails from include[]', () => {
  const policy = {
    include: [
      { email: { email: 'A@X.com' } },
      { email: { email: 'b@x.com' } },
      { everyone: {} },
    ],
  };
  const set = emailsInPolicy(policy);
  assert.ok(set.has('a@x.com'));
  assert.ok(set.has('b@x.com'));
  assert.equal(set.size, 2);
});

test('addToCFAccess: no-op with reason=not_configured when env missing', async () => {
  const r = await addToCFAccess({}, 'a@b.com');
  assert.deepEqual(r, { ok: false, reason: 'not_configured' });
});

test('removeFromCFAccess: no-op with reason=not_configured when env missing', async () => {
  const r = await removeFromCFAccess({}, 'a@b.com');
  assert.deepEqual(r, { ok: false, reason: 'not_configured' });
});

function cfEnv() {
  return {
    CF_API_TOKEN: 't',
    CF_ACCOUNT_ID: 'acct',
    CF_ACCESS_APP_UUID: 'app',
    CF_ACCESS_POLICY_UUID: 'pol',
  };
}

test('addToCFAccess: PUTs updated policy with new email appended', async () => {
  let lastPut = null;
  async function fakeFetch(url, opts = {}) {
    if (opts.method === 'PUT') {
      lastPut = JSON.parse(opts.body);
      return new Response(JSON.stringify({ success: true, result: lastPut }), { status: 200 });
    }
    return new Response(JSON.stringify({
      success: true,
      result: { id: 'pol', include: [{ email: { email: 'owner@x.com' } }] },
    }), { status: 200 });
  }
  const r = await addToCFAccess(cfEnv(), 'new@x.com', fakeFetch);
  assert.equal(r.ok, true);
  assert.equal(r.added, true);
  assert.ok(lastPut);
  const emails = emailsInPolicy(lastPut);
  assert.ok(emails.has('new@x.com'));
  assert.ok(emails.has('owner@x.com'));
});

test('addToCFAccess: returns already=true when email is already in the policy', async () => {
  let putCount = 0;
  async function fakeFetch(url, opts = {}) {
    if (opts.method === 'PUT') { putCount++; return new Response('{}', { status: 200 }); }
    return new Response(JSON.stringify({
      success: true,
      result: { id: 'pol', include: [{ email: { email: 'existing@x.com' } }] },
    }), { status: 200 });
  }
  const r = await addToCFAccess(cfEnv(), 'existing@x.com', fakeFetch);
  assert.equal(r.ok, true);
  assert.equal(r.already, true);
  assert.equal(putCount, 0, 'must not PUT when already present');
});

test('removeFromCFAccess: PUTs policy without the removed email', async () => {
  let lastPut = null;
  async function fakeFetch(url, opts = {}) {
    if (opts.method === 'PUT') {
      lastPut = JSON.parse(opts.body);
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }
    return new Response(JSON.stringify({
      success: true,
      result: { id: 'pol', include: [
        { email: { email: 'keep@x.com' } },
        { email: { email: 'drop@x.com' } },
      ]},
    }), { status: 200 });
  }
  const r = await removeFromCFAccess(cfEnv(), 'drop@x.com', fakeFetch);
  assert.equal(r.removed, true);
  assert.ok(lastPut);
  const emails = emailsInPolicy(lastPut);
  assert.ok(emails.has('keep@x.com'));
  assert.ok(!emails.has('drop@x.com'));
});

test('removeFromCFAccess: returns already_absent=true when email not in policy', async () => {
  let putCount = 0;
  async function fakeFetch(url, opts = {}) {
    if (opts.method === 'PUT') { putCount++; return new Response('{}', { status: 200 }); }
    return new Response(JSON.stringify({
      success: true,
      result: { id: 'pol', include: [{ email: { email: 'someone@x.com' } }] },
    }), { status: 200 });
  }
  const r = await removeFromCFAccess(cfEnv(), 'ghost@x.com', fakeFetch);
  assert.equal(r.ok, true);
  assert.equal(r.already_absent, true);
  assert.equal(putCount, 0);
});

test('addToCFAccess: surfaces non-OK API responses as errors', async () => {
  async function fakeFetch() {
    return new Response('Unauthorized', { status: 401 });
  }
  await assert.rejects(
    () => addToCFAccess(cfEnv(), 'x@x.com', fakeFetch),
    /CF Access GET failed: HTTP 401/
  );
});
