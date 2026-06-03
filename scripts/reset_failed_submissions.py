#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

QUEUE_DB = Path('/home/mcgrof/.local/state/ai-post-transformers/queue.db')
REASON = 'bulk retry after llm_backend fixes'


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(QUEUE_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT key, data_json, version FROM submissions WHERE status = 'generation_failed' ORDER BY updated_at"
    ).fetchall()

    print(f'found_failed={len(rows)}')
    for row in rows:
        key = row['key']
        data = json.loads(row['data_json'])
        data['status'] = 'submitted'
        data['updated_at'] = now
        data['claimed_by'] = None
        data['claim_token'] = None
        data['lease_expires_at'] = None
        data['last_heartbeat_at'] = None
        data['error'] = None
        data.pop('draft_stem', None)
        history = data.get('status_history')
        if not isinstance(history, list):
            history = []
        history.append({
            'status': 'submitted',
            'at': now,
            'by': 'mcgrof',
            'reason': REASON,
        })
        data['status_history'] = history
        new_version = row['version'] + 1
        conn.execute(
            "UPDATE submissions SET data_json = ?, version = ?, status = ?, claimed_by = NULL, claim_token = NULL, lease_expires_at = NULL, updated_at = ? WHERE key = ?",
            (json.dumps(data, sort_keys=True), new_version, 'submitted', now, key),
        )
        conn.execute(
            "INSERT INTO queue_history (table_name, record_key, action, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            ('submissions', key, 'field_update', json.dumps({'updates': ['status', 'error', 'claimed_by', 'claim_token', 'lease_expires_at'], 'reason': REASON}, sort_keys=True), now),
        )
        print(json.dumps({'key': key, 'urls': data.get('urls'), 'status': data.get('status')}))

    conn.commit()
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
