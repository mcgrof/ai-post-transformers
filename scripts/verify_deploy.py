#!/usr/bin/env python3
"""Verify live admin worker deployment state.

Uses Cloudflare Access service tokens from environment variables
to authenticate against the admin dashboard. This avoids weakening
admin security while providing a scriptable verification path.

Required environment variables:
    ADMIN_URL               Base URL of the admin dashboard
                            (e.g. https://podcast-admin.do-not-panic.com)
    CF_ACCESS_CLIENT_ID     Cloudflare Access service token client ID
    CF_ACCESS_CLIENT_SECRET Cloudflare Access service token client secret

Optional:
    VERIFY_TIMEOUT          Request timeout in seconds (default: 10)

Setup instructions:
    1. In the Cloudflare Zero Trust dashboard, create a Service Token
       under Access → Service Auth → Service Tokens.
    2. Copy the Client ID and Client Secret.
    3. Add an Access policy for the admin application that allows the
       service token (Service Auth → select the token).
    4. Export the variables in your local environment:
           export ADMIN_URL="https://podcast-admin.do-not-panic.com"
           export CF_ACCESS_CLIENT_ID="<your-client-id>"
           export CF_ACCESS_CLIENT_SECRET="<your-client-secret>"
    5. Run:  python scripts/verify_deploy.py

These secrets must NEVER be committed to git. Store them in a local
.env file (already in .gitignore) or export them in your shell
profile.
"""

import json
import os
import sys
import urllib.request
import urllib.error


def get_env(name, required=True):
    val = os.environ.get(name)
    if required and not val:
        print(f"ERROR: {name} not set", file=sys.stderr)
        sys.exit(1)
    return val


def cf_access_fetch(url, client_id, client_secret, timeout=10):
    """Fetch a URL through Cloudflare Access using service token."""
    req = urllib.request.Request(url, headers={
        'CF-Access-Client-Id': client_id,
        'CF-Access-Client-Secret': client_secret,
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        return e.code, {'error': body[:500]}
    except urllib.error.URLError as e:
        return 0, {'error': str(e.reason)}


def main():
    admin_url = get_env('ADMIN_URL').rstrip('/')
    client_id = get_env('CF_ACCESS_CLIENT_ID')
    client_secret = get_env('CF_ACCESS_CLIENT_SECRET')
    timeout = int(get_env('VERIFY_TIMEOUT', required=False) or '10')

    checks = []
    ok = True

    # 1. Check /api/version
    print(f"Checking {admin_url}/api/version ...")
    status, data = cf_access_fetch(
        f'{admin_url}/api/version', client_id, client_secret, timeout)
    if status == 200 and data.get('release'):
        release = data['release']
        print(f"  Release: {release}")
        checks.append(('version', True, release))
    else:
        print(f"  FAILED (status={status}): {data}")
        checks.append(('version', False, str(data)))
        ok = False

    # 2. Check /api/drafts (read-only, verifies auth + data path)
    print(f"Checking {admin_url}/api/drafts ...")
    status, data = cf_access_fetch(
        f'{admin_url}/api/drafts', client_id, client_secret, timeout)
    if status == 200 and 'drafts' in data:
        count = len(data.get('drafts', []))
        print(f"  Drafts: {count} active")
        checks.append(('drafts', True, f'{count} active'))
    else:
        print(f"  FAILED (status={status}): {data}")
        checks.append(('drafts', False, str(data)))
        ok = False

    # 3. Check /api/submissions (read-only)
    print(f"Checking {admin_url}/api/submissions ...")
    status, data = cf_access_fetch(
        f'{admin_url}/api/submissions', client_id, client_secret, timeout)
    if status == 200 and 'submissions' in data:
        count = len(data.get('submissions', []))
        print(f"  Submissions: {count}")
        checks.append(('submissions', True, f'{count}'))
    else:
        print(f"  FAILED (status={status}): {data}")
        checks.append(('submissions', False, str(data)))
        ok = False

    # Summary
    print()
    passed = sum(1 for _, s, _ in checks if s)
    total = len(checks)
    print(f"{'PASS' if ok else 'FAIL'}: {passed}/{total} checks passed")
    for name, success, detail in checks:
        mark = 'OK' if success else 'FAIL'
        print(f"  [{mark}] {name}: {detail}")

    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
