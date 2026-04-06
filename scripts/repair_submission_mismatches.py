#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r2_upload import get_r2_client

ADMIN_BUCKET = 'podcast-admin'
PUBLIC_BUCKET = 'ai-post-transformers'
# Only repair published submissions with orphaned drafts.
# NEVER revert rejected submissions — rejection is an intentional
# operator decision, not a state drift to repair.
TARGET_STATUSES = {'published'}
REASON = 'automatic mismatch repair: draft exists but submission status drifted'


def list_keys(client, bucket: str, prefix: str) -> list[str]:
    keys = []
    kwargs = {'Bucket': bucket, 'Prefix': prefix}
    while True:
        resp = client.list_objects_v2(**kwargs)
        keys.extend(obj['Key'] for obj in resp.get('Contents', []))
        if not resp.get('IsTruncated'):
            break
        kwargs['ContinuationToken'] = resp['NextContinuationToken']
    return keys


def load_json(client, bucket: str, key: str):
    raw = client.get_object(Bucket=bucket, Key=key)['Body'].read()
    text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
    return json.loads(text)


def exists(client, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def main() -> int:
    client = get_r2_client()
    now = datetime.now(timezone.utc).isoformat()

    repaired = []
    for key in list_keys(client, ADMIN_BUCKET, 'submissions/'):
        try:
            sub = load_json(client, ADMIN_BUCKET, key)
        except Exception:
            continue

        status = sub.get('status')
        if status not in TARGET_STATUSES:
            continue

        draft_stem = sub.get('draft_stem')
        if not draft_stem:
            continue

        draft_mp3 = f'{draft_stem}.mp3'
        if not exists(client, PUBLIC_BUCKET, draft_mp3):
            continue

        sub['status'] = 'draft_generated'
        sub['updated_at'] = now
        sub['error'] = None
        history = sub.get('status_history')
        if not isinstance(history, list):
            history = []
        history.append({
            'status': 'draft_generated',
            'at': now,
            'by': 'mcgrof',
            'reason': REASON,
            'previous_status': status,
        })
        sub['status_history'] = history
        client.put_object(
            Bucket=ADMIN_BUCKET,
            Key=key,
            Body=json.dumps(sub, indent=2) + '\n',
            ContentType='application/json',
        )
        repaired.append({
            'key': key,
            'from': status,
            'to': 'draft_generated',
            'draft': draft_mp3,
        })

    print(json.dumps({'repaired_count': len(repaired), 'repaired': repaired}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
