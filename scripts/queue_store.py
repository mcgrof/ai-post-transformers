"""SQLite-backed transactional queue store for generation and publish state.

Replaces the R2 JSON read-modify-write hot path with BEGIN IMMEDIATE
transactions and version-based compare-and-swap, following the same
design spirit as delegation_store.py.

R2 remains the backend for blobs, artifacts, and payloads.  This store
handles only the lightweight coordination state: submission lifecycle,
publish job lifecycle, leases, claim tokens, and heartbeats.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


class CASConflictError(Exception):
    """Raised when a compare-and-swap update detects a version mismatch."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


# -----------------------------------------------------------------------
# SQLite implementation
# -----------------------------------------------------------------------


class SQLiteQueueStore:
    """Transactional SQLite store for submission and publish job state.

    Uses BEGIN IMMEDIATE + version-based CAS for every mutation,
    preventing the lost-update races inherent in the R2 read-modify-write
    pattern.
    """

    def __init__(self, path: str | Path, now=None):
        self.path = str(path)
        self._now = now
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _now_dt(self) -> datetime:
        if self._now:
            return self._now()
        return _utcnow()

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS submissions (
                    key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'submitted',
                    claimed_by TEXT,
                    claim_token TEXT,
                    lease_expires_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_submissions_status
                    ON submissions(status);
                CREATE INDEX IF NOT EXISTS idx_submissions_claimed_by
                    ON submissions(claimed_by);

                CREATE TABLE IF NOT EXISTS publish_jobs (
                    job_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'approved_for_publish',
                    claimed_by_admin_id TEXT,
                    lease_expires_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_publish_jobs_state
                    ON publish_jobs(state);

                CREATE TABLE IF NOT EXISTS publish_results (
                    job_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS queue_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_queue_history_record
                    ON queue_history(table_name, record_key);
            """)

    def describe(self) -> str:
        return f"sqlite:{self.path}"

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _record_history(self, conn, table: str, key: str, action: str,
                        details: dict | None = None):
        conn.execute(
            "INSERT INTO queue_history (table_name, record_key, action, "
            "details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (table, key, action,
             json.dumps(details, sort_keys=True) if details else None,
             self._now_iso()),
        )

    def get_history(self, table: str, key: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT action, details_json, created_at FROM queue_history "
                "WHERE table_name = ? AND record_key = ? ORDER BY id",
                (table, key),
            ).fetchall()
        return [
            {"action": r[0],
             "details": json.loads(r[1]) if r[1] else None,
             "timestamp": r[2]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Submissions
    # ------------------------------------------------------------------

    def save_submission(self, key: str, data: dict,
                        expected_version: int | None = None) -> int:
        now = self._now_iso()
        status = data.get("status", "submitted")
        claimed_by = data.get("claimed_by")
        claim_token = data.get("claim_token")
        lease_expires_at = data.get("lease_expires_at")
        data_json = json.dumps(data, sort_keys=True)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version FROM submissions WHERE key = ?", (key,)
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO submissions "
                    "(key, data_json, version, status, claimed_by, "
                    "claim_token, lease_expires_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                    (key, data_json, status, claimed_by,
                     claim_token, lease_expires_at, now),
                )
                self._record_history(conn, "submissions", key, "created",
                                     {"status": status})
                conn.commit()
                return 1

            current_version = row[0]
            if (expected_version is not None
                    and current_version != expected_version):
                raise CASConflictError(
                    f"submission {key}: expected version "
                    f"{expected_version}, got {current_version}"
                )
            new_version = current_version + 1
            result = conn.execute(
                "UPDATE submissions SET data_json = ?, version = ?, "
                "status = ?, claimed_by = ?, claim_token = ?, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE key = ? AND version = ?",
                (data_json, new_version, status, claimed_by,
                 claim_token, lease_expires_at, now,
                 key, current_version),
            )
            if result.rowcount != 1:
                raise CASConflictError(
                    f"submission {key}: concurrent update detected"
                )
            self._record_history(conn, "submissions", key, "updated",
                                 {"status": status, "version": new_version})
            conn.commit()
            return new_version

    def load_submission(self, key: str) -> tuple[dict, int] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json, version FROM submissions WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def list_submissions(self, status: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT key, data_json FROM submissions "
                    "WHERE status = ? ORDER BY updated_at",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key, data_json FROM submissions "
                    "ORDER BY updated_at",
                ).fetchall()
        results = []
        for key, data_json in rows:
            data = json.loads(data_json)
            data["_key"] = key
            results.append(data)
        return results

    def claim_submission(self, key: str, admin_id: str,
                         lease_seconds: int = 1800) -> dict | None:
        """Atomically claim a submission with a lease and claim token.

        Returns the claimed record or None if the key does not exist.
        Raises CASConflictError on concurrent claim.
        """
        now = self._now_dt()
        token = str(uuid.uuid4())
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data_json, version FROM submissions WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None

            data = json.loads(row[0])
            current_version = row[1]

            data["status"] = "generation_claimed"
            data["claimed_by"] = admin_id
            data["claim_token"] = token
            data["lease_expires_at"] = lease_expires
            data["last_heartbeat_at"] = now.isoformat()
            data["updated_at"] = now.isoformat()

            history = data.get("status_history", [])
            history.append({
                "status": "generation_claimed",
                "at": now.isoformat(),
                "by": admin_id,
            })
            data["status_history"] = history

            new_version = current_version + 1
            result = conn.execute(
                "UPDATE submissions SET data_json = ?, version = ?, "
                "status = ?, claimed_by = ?, claim_token = ?, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE key = ? AND version = ?",
                (json.dumps(data, sort_keys=True), new_version,
                 "generation_claimed", admin_id, token, lease_expires,
                 now.isoformat(), key, current_version),
            )
            if result.rowcount != 1:
                raise CASConflictError(
                    f"submission {key}: concurrent claim detected"
                )
            self._record_history(conn, "submissions", key, "claimed",
                                 {"admin_id": admin_id, "token": token})
            conn.commit()

        data["_key"] = key
        return data

    def heartbeat_submission(self, key: str, claim_token: str,
                             lease_seconds: int = 1800) -> dict | None:
        """Extend lease if claim_token matches. Returns None on mismatch."""
        now = self._now_dt()
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data_json, version, claim_token FROM submissions "
                "WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None

            data = json.loads(row[0])
            current_version = row[1]
            stored_token = row[2]

            if stored_token != claim_token:
                return None

            data["lease_expires_at"] = lease_expires
            data["last_heartbeat_at"] = now.isoformat()

            new_version = current_version + 1
            conn.execute(
                "UPDATE submissions SET data_json = ?, version = ?, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE key = ? AND version = ?",
                (json.dumps(data, sort_keys=True), new_version,
                 lease_expires, now.isoformat(), key, current_version),
            )
            self._record_history(conn, "submissions", key, "heartbeat",
                                 {"token": claim_token})
            conn.commit()

        data["_key"] = key
        return data

    def verify_claim_token(self, key: str, expected_token: str) -> bool:
        """Check if the submission's claim_token matches expected_token."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT claim_token FROM submissions WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return False
        return row[0] == expected_token

    def release_stale_submissions(self) -> int:
        """Release submissions with expired leases back to submitted."""
        now = self._now_dt()
        released = 0

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT key, data_json, version FROM submissions "
                "WHERE status IN ('generation_claimed', 'generation_running') "
                "AND lease_expires_at IS NOT NULL "
                "AND claim_token IS NOT NULL "
                "AND lease_expires_at < ?",
                (now.isoformat(),),
            ).fetchall()

            for key, data_json, version in rows:
                data = json.loads(data_json)
                owner = data.get("claimed_by", "unknown")

                data["status"] = "submitted"
                data["claimed_by"] = None
                data["claim_token"] = None
                data["lease_expires_at"] = None
                data["last_heartbeat_at"] = None
                data["release_reason"] = "lease expired during generation"
                data["updated_at"] = now.isoformat()

                history = data.get("status_history", [])
                history.append({"status": "submitted", "at": now.isoformat()})
                data["status_history"] = history

                new_version = version + 1
                conn.execute(
                    "UPDATE submissions SET data_json = ?, version = ?, "
                    "status = 'submitted', claimed_by = NULL, "
                    "claim_token = NULL, lease_expires_at = NULL, "
                    "updated_at = ? WHERE key = ? AND version = ?",
                    (json.dumps(data, sort_keys=True), new_version,
                     now.isoformat(), key, version),
                )
                self._record_history(
                    conn, "submissions", key, "stale_released",
                    {"previous_owner": owner})
                released += 1

            conn.commit()
        return released

    def active_submissions_for_admin(self, admin_id: str) -> list[dict]:
        """Return submissions actively being generated by admin_id."""
        now = self._now_dt()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, data_json FROM submissions "
                "WHERE status IN ('generation_claimed', 'generation_running') "
                "AND claimed_by = ? "
                "AND lease_expires_at > ?",
                (admin_id, now.isoformat()),
            ).fetchall()
        results = []
        for key, data_json in rows:
            data = json.loads(data_json)
            data["_key"] = key
            results.append(data)
        return results

    def find_pending_submissions(self) -> list[dict]:
        """Return submissions in claimable states, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, data_json FROM submissions "
                "WHERE status IN ('submitted', 'pending') "
                "ORDER BY updated_at, key",
            ).fetchall()
        results = []
        for key, data_json in rows:
            data = json.loads(data_json)
            data["_key"] = key
            results.append(data)
        return results

    def update_submission(self, key: str, updates: dict) -> dict:
        """Atomic read-modify-write for a submission record."""
        now = self._now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data_json, version FROM submissions WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise KeyError(f"submission not found: {key}")

            data = json.loads(row[0])
            current_version = row[1]

            data.update(updates)
            data["updated_at"] = now

            history = data.get("status_history", [])
            entry = {
                "status": updates.get("status", data.get("status")),
                "at": now,
            }
            if "claimed_by" in updates:
                entry["by"] = updates["claimed_by"]
            history.append(entry)
            data["status_history"] = history

            status = data.get("status", "submitted")
            claimed_by = data.get("claimed_by")
            claim_token = data.get("claim_token")
            lease_expires_at = data.get("lease_expires_at")

            new_version = current_version + 1
            result = conn.execute(
                "UPDATE submissions SET data_json = ?, version = ?, "
                "status = ?, claimed_by = ?, claim_token = ?, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE key = ? AND version = ?",
                (json.dumps(data, sort_keys=True), new_version,
                 status, claimed_by, claim_token, lease_expires_at,
                 now, key, current_version),
            )
            if result.rowcount != 1:
                raise CASConflictError(
                    f"submission {key}: concurrent update detected"
                )
            self._record_history(conn, "submissions", key, "field_update",
                                 {"updates": list(updates.keys())})
            conn.commit()

        data["_key"] = key
        return data

    # ------------------------------------------------------------------
    # Publish jobs (same interface as LocalPublishJobStore)
    # ------------------------------------------------------------------

    def load_job(self, job_or_path: str) -> dict:
        from scripts.publish_jobs import validate_job

        job_id = _normalize_job_id(job_or_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM publish_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"publish job not found: {job_id}")

        job = json.loads(row[0])
        validate_job(job)
        return job

    def save_job(self, job: dict) -> str:
        from scripts.publish_jobs import validate_job

        validate_job(job)
        job_id = job["job_id"]
        state = job.get("state", "approved_for_publish")
        claimed = job.get("claimed_by_admin_id")
        lease = job.get("lease_expires_at")
        now = self._now_iso()
        data_json = json.dumps(job, sort_keys=True)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version FROM publish_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO publish_jobs "
                    "(job_id, data_json, version, state, "
                    "claimed_by_admin_id, lease_expires_at, updated_at) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (job_id, data_json, state, claimed, lease, now),
                )
                self._record_history(
                    conn, "publish_jobs", job_id, "created",
                    {"state": state})
            else:
                current_version = row[0]
                new_version = current_version + 1
                conn.execute(
                    "UPDATE publish_jobs SET data_json = ?, version = ?, "
                    "state = ?, claimed_by_admin_id = ?, "
                    "lease_expires_at = ?, updated_at = ? "
                    "WHERE job_id = ?",
                    (data_json, new_version, state, claimed, lease, now,
                     job_id),
                )
                self._record_history(
                    conn, "publish_jobs", job_id, "updated",
                    {"state": state, "version": new_version})
            conn.commit()
        return job_id

    def save_result(self, job_id: str, result: dict) -> str:
        now = self._now_iso()
        data_json = json.dumps(result, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO publish_results "
                "(job_id, data_json, updated_at) VALUES (?, ?, ?)",
                (job_id, data_json, now),
            )
            conn.commit()
        return job_id

    def load_result(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data_json FROM publish_results WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def list_results(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id, data_json FROM publish_results "
                "ORDER BY updated_at, job_id"
            ).fetchall()
        results = []
        for job_id, data_json in rows:
            data = json.loads(data_json)
            data.setdefault("job_id", job_id)
            results.append(data)
        return results

    def list_jobs(self) -> list[dict]:
        from scripts.publish_jobs import validate_job

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data_json FROM publish_jobs "
                "ORDER BY updated_at, job_id"
            ).fetchall()
        results = []
        for (data_json,) in rows:
            job = json.loads(data_json)
            validate_job(job)
            results.append(job)
        return results


# -----------------------------------------------------------------------
# In-memory implementation (for tests)
# -----------------------------------------------------------------------


class InMemoryQueueStore:
    """In-memory store for tests.  Same interface as SQLiteQueueStore."""

    def __init__(self, now=None):
        self._now = now
        self._submissions: dict[str, tuple[dict, int]] = {}
        self._publish_jobs: dict[str, tuple[dict, int]] = {}
        self._publish_results: dict[str, dict] = {}
        self._history: list[dict] = []

    def describe(self) -> str:
        return "memory"

    def _now_dt(self) -> datetime:
        if self._now:
            return self._now()
        return _utcnow()

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def _record_history(self, table, key, action, details=None):
        self._history.append({
            "table_name": table,
            "record_key": key,
            "action": action,
            "details": details,
            "timestamp": self._now_iso(),
        })

    def get_history(self, table: str, key: str) -> list[dict]:
        return [
            h for h in self._history
            if h["table_name"] == table and h["record_key"] == key
        ]

    # --- Submissions ---

    def save_submission(self, key, data, expected_version=None):
        existing = self._submissions.get(key)
        if existing is None:
            self._submissions[key] = (deepcopy(data), 1)
            self._record_history("submissions", key, "created",
                                 {"status": data.get("status")})
            return 1
        _, current_version = existing
        if (expected_version is not None
                and current_version != expected_version):
            raise CASConflictError(
                f"submission {key}: expected version "
                f"{expected_version}, got {current_version}"
            )
        new_version = current_version + 1
        self._submissions[key] = (deepcopy(data), new_version)
        self._record_history("submissions", key, "updated",
                             {"status": data.get("status"),
                              "version": new_version})
        return new_version

    def load_submission(self, key):
        result = self._submissions.get(key)
        if result is None:
            return None
        data, version = result
        return deepcopy(data), version

    def list_submissions(self, status=None):
        results = []
        for key in sorted(self._submissions.keys()):
            data, _ = self._submissions[key]
            if status and data.get("status") != status:
                continue
            d = deepcopy(data)
            d["_key"] = key
            results.append(d)
        return results

    def claim_submission(self, key, admin_id, lease_seconds=1800):
        result = self._submissions.get(key)
        if result is None:
            return None
        data, version = deepcopy(result[0]), result[1]
        now = self._now_dt()
        token = str(uuid.uuid4())
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()

        data["status"] = "generation_claimed"
        data["claimed_by"] = admin_id
        data["claim_token"] = token
        data["lease_expires_at"] = lease_expires
        data["last_heartbeat_at"] = now.isoformat()
        data["updated_at"] = now.isoformat()

        history = data.get("status_history", [])
        history.append({
            "status": "generation_claimed",
            "at": now.isoformat(),
            "by": admin_id,
        })
        data["status_history"] = history

        self._submissions[key] = (deepcopy(data), version + 1)
        self._record_history("submissions", key, "claimed",
                             {"admin_id": admin_id, "token": token})
        data["_key"] = key
        return data

    def heartbeat_submission(self, key, claim_token, lease_seconds=1800):
        result = self._submissions.get(key)
        if result is None:
            return None
        data, version = deepcopy(result[0]), result[1]
        if data.get("claim_token") != claim_token:
            return None
        now = self._now_dt()
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        data["lease_expires_at"] = lease_expires
        data["last_heartbeat_at"] = now.isoformat()
        self._submissions[key] = (deepcopy(data), version + 1)
        data["_key"] = key
        return data

    def verify_claim_token(self, key, expected_token):
        result = self._submissions.get(key)
        if result is None:
            return False
        data, _ = result
        return data.get("claim_token") == expected_token

    def release_stale_submissions(self):
        now = self._now_dt()
        released = 0
        for key in list(self._submissions.keys()):
            data, version = self._submissions[key]
            status = data.get("status")
            if status not in ("generation_claimed", "generation_running"):
                continue
            lease = data.get("lease_expires_at")
            if not lease or not data.get("claim_token"):
                continue
            if datetime.fromisoformat(lease) >= now:
                continue
            owner = data.get("claimed_by", "unknown")
            data = deepcopy(data)
            data["status"] = "submitted"
            data["claimed_by"] = None
            data["claim_token"] = None
            data["lease_expires_at"] = None
            data["last_heartbeat_at"] = None
            data["release_reason"] = "lease expired during generation"
            data["updated_at"] = now.isoformat()
            history = data.get("status_history", [])
            history.append({"status": "submitted", "at": now.isoformat()})
            data["status_history"] = history
            self._submissions[key] = (data, version + 1)
            self._record_history("submissions", key, "stale_released",
                                 {"previous_owner": owner})
            released += 1
        return released

    def active_submissions_for_admin(self, admin_id):
        now = self._now_dt()
        results = []
        for key, (data, _) in self._submissions.items():
            if data.get("status") not in (
                "generation_claimed", "generation_running"
            ):
                continue
            if data.get("claimed_by") != admin_id:
                continue
            lease = data.get("lease_expires_at")
            if not lease or datetime.fromisoformat(lease) <= now:
                continue
            d = deepcopy(data)
            d["_key"] = key
            results.append(d)
        return results

    def find_pending_submissions(self):
        results = []
        for key in sorted(self._submissions.keys()):
            data, _ = self._submissions[key]
            if data.get("status") in ("submitted", "pending"):
                d = deepcopy(data)
                d["_key"] = key
                results.append(d)
        results.sort(key=lambda s: s.get("timestamp", ""))
        return results

    def update_submission(self, key, updates):
        result = self._submissions.get(key)
        if result is None:
            raise KeyError(f"submission not found: {key}")
        data, version = deepcopy(result[0]), result[1]
        now = self._now_iso()
        data.update(updates)
        data["updated_at"] = now
        history = data.get("status_history", [])
        entry = {
            "status": updates.get("status", data.get("status")),
            "at": now,
        }
        if "claimed_by" in updates:
            entry["by"] = updates["claimed_by"]
        history.append(entry)
        data["status_history"] = history
        new_version = version + 1
        self._submissions[key] = (deepcopy(data), new_version)
        self._record_history("submissions", key, "field_update",
                             {"updates": list(updates.keys()),
                              "version": new_version})
        data["_key"] = key
        return data

    # --- Publish jobs ---

    def load_job(self, job_or_path):
        from scripts.publish_jobs import validate_job

        job_id = _normalize_job_id(str(job_or_path))
        result = self._publish_jobs.get(job_id)
        if result is None:
            raise KeyError(f"publish job not found: {job_id}")
        job = deepcopy(result[0])
        validate_job(job)
        return job

    def save_job(self, job):
        from scripts.publish_jobs import validate_job

        validate_job(job)
        job_id = job["job_id"]
        existing = self._publish_jobs.get(job_id)
        version = (existing[1] + 1) if existing else 1
        self._publish_jobs[job_id] = (deepcopy(job), version)
        action = "created" if existing is None else "updated"
        self._record_history("publish_jobs", job_id, action,
                             {"state": job.get("state"), "version": version})
        return job_id

    def save_result(self, job_id, result):
        self._publish_results[job_id] = deepcopy(result)
        return job_id

    def load_result(self, job_id):
        result = self._publish_results.get(job_id)
        if result is None:
            return None
        return deepcopy(result)

    def list_results(self):
        results = []
        for job_id in sorted(self._publish_results.keys()):
            data = deepcopy(self._publish_results[job_id])
            data.setdefault("job_id", job_id)
            results.append(data)
        return results

    def list_jobs(self):
        from scripts.publish_jobs import validate_job

        results = []
        for job_id in sorted(self._publish_jobs.keys()):
            job = deepcopy(self._publish_jobs[job_id][0])
            validate_job(job)
            results.append(job)
        return results


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _normalize_job_id(raw: str) -> str:
    """Extract a bare job_id from a path, key, or filename."""
    if raw.startswith("publish-jobs/"):
        raw = raw[len("publish-jobs/"):]
    if raw.endswith(".json"):
        raw = raw[:-5]
    p = Path(raw)
    return p.stem if "/" in raw else raw


def get_queue_store(
    *,
    mode: str = "auto",
    path: str | Path | None = None,
    now=None,
) -> SQLiteQueueStore | InMemoryQueueStore:
    """Factory for QueueStore instances.

    mode="sqlite"  — always use SQLite at *path*
    mode="memory"  — always use in-memory (tests)
    mode="auto"    — SQLite if *path* is given, otherwise in-memory
    """
    if mode == "memory":
        return InMemoryQueueStore(now=now)
    if mode == "sqlite" or (mode == "auto" and path):
        if not path:
            raise ValueError("sqlite mode requires a path")
        return SQLiteQueueStore(path, now=now)
    return InMemoryQueueStore(now=now)
