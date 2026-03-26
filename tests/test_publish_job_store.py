import io

from scripts.publish_job_store import (
    LocalPublishJobStore,
    R2PublishJobStore,
    get_publish_job_store,
)
from scripts.publish_jobs import claim_next_available, load_job, make_job_record, save_job


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Bucket, Prefix):
        keys = sorted(
            key for bucket, key in self.client.objects if bucket == Bucket and key.startswith(Prefix)
        )
        yield {"Contents": [{"Key": key} for key in keys]}


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)].encode("utf-8"))}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)


def test_get_publish_job_store_falls_back_to_local_without_r2_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)

    store = get_publish_job_store(root=tmp_path)

    assert isinstance(store, LocalPublishJobStore)


def test_r2_publish_job_store_round_trip_and_claims_next_job():
    client = FakeS3Client()
    store = R2PublishJobStore(client=client, bucket="podcast-admin")

    older = make_job_record(
        draft_key="drafts/2026/03/older.mp3",
        job_id="pub_2026_03_26_120000",
        created_at="2026-03-26T12:00:00+00:00",
    )
    newer = make_job_record(
        draft_key="drafts/2026/03/newer.mp3",
        job_id="pub_2026_03_26_120500",
        created_at="2026-03-26T12:05:00+00:00",
    )
    save_job(older, store=store)
    save_job(newer, store=store)

    claimed = claim_next_available(
        admin_id="admin-1",
        admin_name="mcgrof",
        lease_seconds=300,
        store=store,
    )

    assert claimed is not None
    assert claimed["job_id"] == "pub_2026_03_26_120000"
    assert claimed["state"] == "publish_claimed"

    loaded = load_job("pub_2026_03_26_120000", store=store)
    assert loaded["claimed_by_admin_id"] == "admin-1"
    assert loaded["lease_expires_at"] is not None
