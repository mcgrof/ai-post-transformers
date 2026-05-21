// Admin user management.
//
// Two layers:
//
//   1. In-worker allowlist (R2 ADMIN_BUCKET / admins.json).
//      Per-email capability set: { admin, manage_admins, publish,
//      queue_refresh }. Authoritative inside the worker.
//
//   2. Cloudflare Access policy (the CF dashboard / API).
//      Gates who can reach the worker AT ALL. The allowlist is
//      useless if the email is not also on the CF Access policy.
//
// This module manages both. CF Access updates are best-effort:
// if the required secrets are not configured (CF_API_TOKEN,
// CF_ACCOUNT_ID, CF_ACCESS_APP_UUID, CF_ACCESS_POLICY_UUID), the
// in-worker side still updates and we return a warning so the UI
// can prompt the operator to update CF Access by hand.
//
// admins.json schema:
//   {
//     "version": 1,
//     "admins": [
//       {
//         "email": "user@example.com",
//         "capabilities": ["admin", "manage_admins", "publish", ...],
//         "added_by": "mcgrof@gmail.com" | "system",
//         "added_at": "2026-05-21T...Z",
//         "notes": "optional free-text"
//       }
//     ]
//   }

const ADMINS_KEY = 'admins.json';

// Capabilities the system understands. `admin` implies all the
// others; `manage_admins` is required to use this module's CRUD
// endpoints; the others gate matching feature areas:
//   submit          — can submit new podcast generations via the
//                     /submit page (POST /api/submit + helpers)
//   publish         — can approve a draft for publish
//   queue_refresh   — can trigger or own the queue-refresh lane
//
// Capability gating at the respective route handlers is a separate
// concern; this list is the canonical vocabulary the UI + storage
// agree on.
export const CAPABILITIES = Object.freeze(
  ['admin', 'manage_admins', 'submit', 'publish', 'queue_refresh']
);

/**
 * Load the admins.json document. Returns {version, admins} with a
 * sensible empty default if the object doesn't exist yet.
 */
export async function loadAdmins(env) {
  try {
    const obj = await env.ADMIN_BUCKET.get(ADMINS_KEY);
    if (!obj) return { version: 1, admins: [] };
    const text = await obj.text();
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object') {
      return { version: 1, admins: [] };
    }
    return {
      version: parsed.version || 1,
      admins: Array.isArray(parsed.admins) ? parsed.admins : [],
    };
  } catch (e) {
    return { version: 1, admins: [], error: e.message };
  }
}

/** Save admins.json back to R2. */
export async function saveAdmins(env, doc) {
  const body = JSON.stringify({
    version: 1,
    admins: doc.admins || [],
  }, null, 2);
  await env.ADMIN_BUCKET.put(ADMINS_KEY, body, {
    httpMetadata: { contentType: 'application/json' },
  });
}

/**
 * Resolve effective capability set for an identity.
 *
 * Bootstrap rule: if admins.json is empty (no admins yet), the FIRST
 * CF-Access-authenticated email gets all capabilities. This breaks
 * the chicken-and-egg problem when the file is first created. Once
 * any admin is on the list, this bootstrap shortcut stops applying.
 *
 * `admin` capability implies all other capabilities (set semantics).
 */
export function capabilitiesFor(identity, admins) {
  const email = (identity?.email || '').toLowerCase().trim();
  if (!email) return new Set();
  if (!admins || admins.length === 0) {
    return new Set(CAPABILITIES);
  }
  const match = admins.find(
    a => (a.email || '').toLowerCase().trim() === email
  );
  if (!match) return new Set();
  const caps = new Set(match.capabilities || []);
  if (caps.has('admin')) {
    for (const c of CAPABILITIES) caps.add(c);
  }
  return caps;
}

/** Convenience predicate. */
export function hasCapability(identity, capability, admins) {
  return capabilitiesFor(identity, admins).has(capability);
}

/**
 * Validate and normalize an email + capability list for storage.
 * Returns {email, capabilities} or throws.
 */
export function validateAdminInput(email, capabilities) {
  const cleaned = (email || '').toLowerCase().trim();
  // Conservative email regex — full RFC 5322 is overkill; we want
  // to catch obvious junk and accept normal addresses.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleaned)) {
    throw new Error(`invalid email: ${email}`);
  }
  if (!Array.isArray(capabilities) || capabilities.length === 0) {
    throw new Error('capabilities must be a non-empty array');
  }
  const allowed = new Set(CAPABILITIES);
  const caps = [];
  for (const c of capabilities) {
    const s = String(c).trim();
    if (!allowed.has(s)) {
      throw new Error(`unknown capability: ${s}`);
    }
    if (!caps.includes(s)) caps.push(s);
  }
  return { email: cleaned, capabilities: caps };
}

/** Add or replace an admin entry. Returns the updated doc. */
export function upsertAdmin(doc, { email, capabilities }, addedBy, notes = '') {
  const now = new Date().toISOString();
  const admins = (doc.admins || []).filter(
    a => (a.email || '').toLowerCase() !== email.toLowerCase()
  );
  admins.push({
    email,
    capabilities: [...capabilities],
    added_by: addedBy || 'system',
    added_at: now,
    notes: notes || '',
  });
  // Sort alphabetically for stable rendering + smaller diffs.
  admins.sort((a, b) =>
    (a.email || '').localeCompare(b.email || '')
  );
  return { ...doc, version: 1, admins };
}

/** Remove an admin by email. Returns {doc, removed: boolean}. */
export function removeAdmin(doc, email) {
  const cleaned = (email || '').toLowerCase().trim();
  const before = (doc.admins || []).length;
  const admins = (doc.admins || []).filter(
    a => (a.email || '').toLowerCase() !== cleaned
  );
  return {
    doc: { ...doc, version: 1, admins },
    removed: admins.length < before,
  };
}

// -----------------------------------------------------------------
// Cloudflare Access integration.
//
// Updates the policy that gates admin.podcast.do-not-panic.com so
// the email can actually log in. Configuration:
//
//   wrangler secret put CF_API_TOKEN          # required
//   wrangler vars: CF_ACCOUNT_ID,             # required
//                  CF_ACCESS_APP_UUID,        # required
//                  CF_ACCESS_POLICY_UUID      # required
//
// The token must have Account / Access: Apps and Policies / Edit.
//
// If any of these are missing, cfAccessConfigured() returns false
// and the higher-level API returns a warning rather than failing.
// -----------------------------------------------------------------

export function cfAccessConfigured(env) {
  return !!(
    env && env.CF_API_TOKEN && env.CF_ACCOUNT_ID &&
    env.CF_ACCESS_APP_UUID && env.CF_ACCESS_POLICY_UUID
  );
}

function cfApiBase(env) {
  return (
    `https://api.cloudflare.com/client/v4/accounts/${env.CF_ACCOUNT_ID}` +
    `/access/apps/${env.CF_ACCESS_APP_UUID}` +
    `/policies/${env.CF_ACCESS_POLICY_UUID}`
  );
}

async function cfApiGetPolicy(env, fetchImpl = fetch) {
  const r = await fetchImpl(cfApiBase(env), {
    headers: {
      Authorization: `Bearer ${env.CF_API_TOKEN}`,
      Accept: 'application/json',
    },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(
      `CF Access GET failed: HTTP ${r.status} ${body.slice(0, 200)}`
    );
  }
  const json = await r.json();
  if (!json || !json.success || !json.result) {
    throw new Error(`CF Access GET unexpected: ${JSON.stringify(json).slice(0, 200)}`);
  }
  return json.result;
}

async function cfApiPutPolicy(env, policy, fetchImpl = fetch) {
  // CF Access policy PUT requires the full object. Strip read-only
  // fields the API rejects on write.
  const payload = { ...policy };
  delete payload.id;
  delete payload.created_at;
  delete payload.updated_at;
  delete payload.uid;
  const r = await fetchImpl(cfApiBase(env), {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${env.CF_API_TOKEN}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(
      `CF Access PUT failed: HTTP ${r.status} ${body.slice(0, 200)}`
    );
  }
  return await r.json();
}

/**
 * Extract email entries from a CF Access policy.include[] array.
 * Returns a Set of lowercased emails.
 */
export function emailsInPolicy(policy) {
  const out = new Set();
  for (const inc of (policy?.include || [])) {
    if (inc && inc.email && typeof inc.email.email === 'string') {
      out.add(inc.email.email.toLowerCase().trim());
    }
  }
  return out;
}

/** Add an email to the CF Access policy include[] array. No-op if already present. */
export async function addToCFAccess(env, email, fetchImpl = fetch) {
  if (!cfAccessConfigured(env)) {
    return { ok: false, reason: 'not_configured' };
  }
  const policy = await cfApiGetPolicy(env, fetchImpl);
  const include = Array.isArray(policy.include) ? [...policy.include] : [];
  const lowered = email.toLowerCase().trim();
  if ([...emailsInPolicy({ include })].includes(lowered)) {
    return { ok: true, already: true };
  }
  include.push({ email: { email: lowered } });
  await cfApiPutPolicy(env, { ...policy, include }, fetchImpl);
  return { ok: true, added: true };
}

/** Remove an email from the CF Access policy include[] array. No-op if absent. */
export async function removeFromCFAccess(env, email, fetchImpl = fetch) {
  if (!cfAccessConfigured(env)) {
    return { ok: false, reason: 'not_configured' };
  }
  const policy = await cfApiGetPolicy(env, fetchImpl);
  const lowered = email.toLowerCase().trim();
  const include = (policy.include || []).filter(inc => {
    if (inc && inc.email && typeof inc.email.email === 'string') {
      return inc.email.email.toLowerCase().trim() !== lowered;
    }
    return true;
  });
  if (include.length === (policy.include || []).length) {
    return { ok: true, already_absent: true };
  }
  await cfApiPutPolicy(env, { ...policy, include }, fetchImpl);
  return { ok: true, removed: true };
}
