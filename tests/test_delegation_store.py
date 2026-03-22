"""Tests for durable delegation state persistence."""

from datetime import datetime, timezone

import pytest

from delegation_queue import ClaimConflictError, enqueue_job, register_volunteer


def _now():
    return datetime(2026, 3, 21, 18, 30, tzinfo=timezone.utc)


class TestSQLiteDelegationStateStore:
    def test_sqlite_store_persists_manifest_and_admin_queue_across_instances(
            self, tmp_path):
        from delegation_store import SQLiteDelegationStateStore

        db_path = tmp_path / "delegation-state.db"
        store = SQLiteDelegationStateStore(db_path, now=_now)

        manifest = store.mutate_manifest(
            lambda current: register_volunteer(
                current,
                "worker-a",
                capabilities=["generate"],
                locales=["en-US"],
                max_claims=2,
            ),
            expected_version=0,
        )
        manifest = store.mutate_manifest(
            lambda current: enqueue_job(
                current,
                "job-1",
                title="KV cache episode",
                locale="en-US",
                required_capabilities=["generate"],
            ),
            expected_version=manifest["version"],
        )
        store.save_admin_queue({
            "exported_at": "2026-03-21T18:30:00Z",
            "sections": {"public": [{"arxiv_id": "2401.00001"}]},
            "papers": [{
                "arxiv_id": "2401.00001",
                "queue_section": "public",
            }],
            "counts": {"public": 1},
        })

        reopened = SQLiteDelegationStateStore(db_path, now=_now)

        persisted = reopened.load_manifest()
        assert persisted["version"] == manifest["version"]
        assert persisted["volunteers"]["worker-a"]["max_claims"] == 2
        assert persisted["jobs"]["job-1"]["status"] == "queued"
        assert reopened.load_admin_queue()["counts"] == {"public": 1}

    def test_sqlite_store_rejects_stale_compare_and_swap_version(self, tmp_path):
        from delegation_store import SQLiteDelegationStateStore

        db_path = tmp_path / "delegation-state.db"
        store = SQLiteDelegationStateStore(db_path, now=_now)

        first = store.mutate_manifest(
            lambda current: register_volunteer(
                current,
                "worker-a",
                capabilities=["generate"],
                locales=["en-US"],
            ),
            expected_version=0,
        )

        store.mutate_manifest(
            lambda current: enqueue_job(
                current,
                "job-1",
                title="KV cache episode",
            ),
            expected_version=first["version"],
        )

        with pytest.raises(ClaimConflictError):
            store.mutate_manifest(
                lambda current: enqueue_job(
                    current,
                    "job-2",
                    title="stale update should fail",
                ),
                expected_version=first["version"],
            )
