"""Durable persistence boundary for delegation state."""

from __future__ import annotations

from copy import deepcopy
import json
import sqlite3

from delegation_queue import (
    ClaimConflictError,
    create_manifest,
    export_admin_queue_payload,
)


class InMemoryDelegationStateStore:
    """Atomic in-memory store used by tests and local wiring."""

    def __init__(self, now=None, lease_duration_seconds=900):
        self._now = now
        self._lease_duration_seconds = lease_duration_seconds
        self._manifest = create_manifest()
        self._admin_queue = export_admin_queue_payload({})

    def load_manifest(self):
        return deepcopy(self._manifest)

    def mutate_manifest(self, mutator, expected_version=None):
        current = deepcopy(self._manifest)
        if expected_version is not None and current["version"] != expected_version:
            raise ClaimConflictError(
                f"stale manifest version {expected_version}, "
                f"current version is {current['version']}"
            )
        updated = mutator(current)
        self._manifest = deepcopy(updated)
        return deepcopy(self._manifest)

    def load_admin_queue(self):
        return deepcopy(self._admin_queue)

    def save_admin_queue(self, payload):
        self._admin_queue = deepcopy(payload)

    def now(self):
        if self._now is None:
            return None
        return self._now()

    def lease_duration_seconds(self):
        return self._lease_duration_seconds


class SQLiteDelegationStateStore:
    """SQLite-backed durable state store with compare-and-swap updates."""

    def __init__(self, path, now=None, lease_duration_seconds=900):
        self.path = str(path)
        self._now = now
        self._lease_duration_seconds = lease_duration_seconds
        self._init_db()

    def load_manifest(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM delegation_state WHERE id = 1"
            ).fetchone()
        return json.loads(row[0])

    def mutate_manifest(self, mutator, expected_version=None):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT manifest_json, manifest_version "
                "FROM delegation_state WHERE id = 1"
            ).fetchone()
            current = json.loads(row[0])
            current_version = row[1]
            if (expected_version is not None and
                    current_version != expected_version):
                raise ClaimConflictError(
                    f"stale manifest version {expected_version}, "
                    f"current version is {current_version}"
                )

            updated = mutator(current)
            result = conn.execute(
                "UPDATE delegation_state "
                "SET manifest_json = ?, manifest_version = ? "
                "WHERE id = 1 AND manifest_version = ?",
                (
                    json.dumps(updated, sort_keys=True),
                    updated["version"],
                    current_version,
                ),
            )
            if result.rowcount != 1:
                raise ClaimConflictError(
                    f"stale manifest version {current_version}, "
                    "store changed during update"
                )
            conn.commit()
        return updated

    def load_admin_queue(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM delegation_admin_queue WHERE id = 1"
            ).fetchone()
        return json.loads(row[0])

    def save_admin_queue(self, payload):
        with self._connect() as conn:
            conn.execute(
                "UPDATE delegation_admin_queue SET payload_json = ? WHERE id = 1",
                (json.dumps(payload, sort_keys=True),),
            )
            conn.commit()

    def now(self):
        if self._now is None:
            return None
        return self._now()

    def lease_duration_seconds(self):
        return self._lease_duration_seconds

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delegation_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "manifest_json TEXT NOT NULL, "
                "manifest_version INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS delegation_admin_queue ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "payload_json TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO delegation_state "
                "(id, manifest_json, manifest_version) VALUES (?, ?, ?)",
                (1, json.dumps(create_manifest(), sort_keys=True), 0),
            )
            conn.execute(
                "INSERT OR IGNORE INTO delegation_admin_queue "
                "(id, payload_json) VALUES (?, ?)",
                (1, json.dumps(export_admin_queue_payload({}), sort_keys=True)),
            )
            conn.commit()
