"""Tests for draft manifest helpers: entry building, R2 upsert,
sidecar enrichment, backfill, and generation worker integration.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.draft_manifest import (
    backfill_manifest,
    build_manifest_entry,
    enrich_sidecar_json,
    upsert_manifest_draft,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_row():
    return {
        "id": 111,
        "title": "Sparse Attention Is All You Need",
        "publish_date": "2026-03-27",
        "audio_file": "/home/user/devel/paper-feed/drafts/2026/03/2026-03-27-sparse-attention-ff01a2.mp3",
        "description": "This episode explores sparse attention.\n\nSource:\nhttps://arxiv.org/abs/2503.12345",
        "source_urls": json.dumps(["https://arxiv.org/pdf/2503.12345"]),
        "image_file": None,
    }


class FakeR2:
    """Minimal in-memory S3/R2 mock."""

    def __init__(self, initial=None):
        self.objects: dict[str, bytes] = {}
        if initial:
            for key, value in initial.items():
                body = json.dumps(value).encode() if isinstance(value, dict) else value
                self.objects[key] = body

    def put_object(self, *, Bucket, Key, Body, ContentType="application/json"):
        if isinstance(Body, str):
            Body = Body.encode("utf-8")
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        data = self.objects.get(Key)
        if data is None:
            raise Exception(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(data)}


# ---------------------------------------------------------------------------
# build_manifest_entry
# ---------------------------------------------------------------------------

class TestBuildManifestEntry:
    def test_basic_fields(self, sample_row):
        entry = build_manifest_entry(sample_row)
        assert entry["id"] == 111
        assert entry["title"] == "Sparse Attention Is All You Need"
        assert entry["date"] == "2026-03-27"
        assert "sparse attention" in entry["description"].lower()
        assert entry["source_urls"] == ["https://arxiv.org/pdf/2503.12345"]
        assert entry["filename"].endswith(".mp3")
        assert entry["basename"].endswith("ff01a2")

    def test_explicit_draft_key(self, sample_row):
        entry = build_manifest_entry(
            sample_row,
            draft_key="drafts/2026/03/custom.mp3",
            draft_stem="drafts/2026/03/custom",
        )
        assert entry["draft_key"] == "drafts/2026/03/custom.mp3"
        assert entry["filename"] == "custom.mp3"
        assert entry["basename"] == "custom"

    def test_source_urls_already_list(self, sample_row):
        sample_row["source_urls"] = ["https://example.com/paper.pdf"]
        entry = build_manifest_entry(sample_row)
        assert entry["source_urls"] == ["https://example.com/paper.pdf"]

    def test_source_urls_none(self, sample_row):
        sample_row["source_urls"] = None
        entry = build_manifest_entry(sample_row)
        assert entry["source_urls"] == []


# ---------------------------------------------------------------------------
# upsert_manifest_draft
# ---------------------------------------------------------------------------

class TestUpsertManifestDraft:
    def test_appends_to_empty_manifest(self, sample_row):
        client = FakeR2({"manifest.json": {"drafts": [], "conferences": {}}})
        entry = build_manifest_entry(sample_row)

        result = upsert_manifest_draft(
            entry, client=client, admin_bucket="podcast-admin"
        )

        assert len(result["drafts"]) == 1
        assert result["drafts"][0]["id"] == 111

        stored = json.loads(client.objects["manifest.json"])
        assert len(stored["drafts"]) == 1

    def test_replaces_existing_entry(self, sample_row):
        existing = {"drafts": [{"id": 111, "title": "Old Title"}], "conferences": {}}
        client = FakeR2({"manifest.json": existing})

        entry = build_manifest_entry(sample_row)
        result = upsert_manifest_draft(
            entry, client=client, admin_bucket="podcast-admin"
        )

        assert len(result["drafts"]) == 1
        assert result["drafts"][0]["title"] == "Sparse Attention Is All You Need"

    def test_preserves_other_entries(self, sample_row):
        existing = {"drafts": [{"id": 99, "title": "Other Episode"}], "conferences": {}}
        client = FakeR2({"manifest.json": existing})

        entry = build_manifest_entry(sample_row)
        result = upsert_manifest_draft(
            entry, client=client, admin_bucket="podcast-admin"
        )

        assert len(result["drafts"]) == 2
        ids = {d["id"] for d in result["drafts"]}
        assert ids == {99, 111}

    def test_creates_manifest_when_missing(self, sample_row):
        client = FakeR2()
        entry = build_manifest_entry(sample_row)

        result = upsert_manifest_draft(
            entry, client=client, admin_bucket="podcast-admin"
        )

        assert len(result["drafts"]) == 1


# ---------------------------------------------------------------------------
# enrich_sidecar_json
# ---------------------------------------------------------------------------

class TestEnrichSidecarJson:
    def test_adds_fields_to_existing_sidecar(self, tmp_path):
        sidecar = tmp_path / "draft.json"
        sidecar.write_text(json.dumps({
            "script": [{"speaker": "A", "text": "Hello"}],
            "sources": [],
            "topics": ["attention"],
        }))

        changed = enrich_sidecar_json(
            sidecar,
            title="Test Title",
            description="Test description.",
            source_urls=["https://arxiv.org/pdf/1234.56789"],
            episode_id=42,
        )

        assert changed is True
        data = json.loads(sidecar.read_text())
        assert data["title"] == "Test Title"
        assert data["description"] == "Test description."
        assert data["source_urls"] == ["https://arxiv.org/pdf/1234.56789"]
        assert data["episode_id"] == 42
        # Original fields preserved
        assert data["script"] == [{"speaker": "A", "text": "Hello"}]

    def test_no_change_when_values_match(self, tmp_path):
        sidecar = tmp_path / "draft.json"
        sidecar.write_text(json.dumps({
            "title": "Same",
            "description": "Same desc",
        }))

        changed = enrich_sidecar_json(
            sidecar, title="Same", description="Same desc",
        )
        assert changed is False

    def test_returns_false_for_missing_file(self, tmp_path):
        changed = enrich_sidecar_json(
            tmp_path / "nonexistent.json", title="X"
        )
        assert changed is False


# ---------------------------------------------------------------------------
# backfill_manifest
# ---------------------------------------------------------------------------

class TestBackfillManifest:
    @pytest.fixture
    def db_with_drafts(self, tmp_path):
        db_path = tmp_path / "papers.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                publish_date TEXT NOT NULL,
                spotify_url TEXT,
                elevenlabs_project_id TEXT,
                audio_file TEXT,
                created_at TEXT NOT NULL,
                source_urls TEXT,
                description TEXT,
                image_file TEXT
            )
        """)
        conn.execute("""
            INSERT INTO podcasts
            (title, publish_date, audio_file, created_at, source_urls, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "Draft Episode Alpha",
            "2026-03-28",
            str(tmp_path / "drafts" / "2026" / "03" / "2026-03-28-alpha-abc123.mp3"),
            "2026-03-28T10:00:00+00:00",
            json.dumps(["https://arxiv.org/pdf/2503.99999"]),
            "Alpha episode description.",
        ))
        conn.execute("""
            INSERT INTO podcasts
            (title, publish_date, audio_file, created_at, source_urls, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "Draft Episode Beta",
            "2026-03-28",
            str(tmp_path / "drafts" / "2026" / "03" / "2026-03-28-beta-def456.mp3"),
            "2026-03-28T11:00:00+00:00",
            json.dumps(["https://arxiv.org/pdf/2503.88888"]),
            "Beta episode description.",
        ))
        conn.commit()
        conn.close()

        # Create sidecar JSONs
        draft_dir = tmp_path / "drafts" / "2026" / "03"
        draft_dir.mkdir(parents=True)
        for name in ["2026-03-28-alpha-abc123", "2026-03-28-beta-def456"]:
            (draft_dir / f"{name}.json").write_text(json.dumps({
                "script": [], "sources": [], "topics": [],
            }))

        return db_path

    def test_dry_run_does_not_write(self, db_with_drafts):
        actions = backfill_manifest(
            db_path=db_with_drafts, dry_run=True,
        )
        assert len(actions) == 2
        assert all(a == "would_add" for _, _, a in actions)

    def test_backfill_adds_missing_entries(self, db_with_drafts):
        client = FakeR2({"manifest.json": {"drafts": [], "conferences": {}}})

        actions = backfill_manifest(
            db_path=db_with_drafts,
            client=client,
            admin_bucket="podcast-admin",
        )

        assert len(actions) == 2
        assert all(a == "added" for _, _, a in actions)

        manifest = json.loads(client.objects["manifest.json"])
        assert len(manifest["drafts"]) == 2
        titles = {d["title"] for d in manifest["drafts"]}
        assert "Draft Episode Alpha" in titles
        assert "Draft Episode Beta" in titles

    def test_backfill_skips_existing(self, db_with_drafts):
        existing_entry = {
            "id": 1,
            "title": "Draft Episode Alpha",
            "date": "2026-03-28",
            "draft_key": "drafts/2026/03/2026-03-28-alpha-abc123.mp3",
            "description": "Already has a description.",
        }
        client = FakeR2({"manifest.json": {"drafts": [existing_entry], "conferences": {}}})

        actions = backfill_manifest(
            db_path=db_with_drafts,
            client=client,
            admin_bucket="podcast-admin",
        )

        already = [a for _, _, a in actions if a == "already_in_manifest"]
        added = [a for _, _, a in actions if a == "added"]
        assert len(already) == 1
        assert len(added) == 1

    def test_backfill_updates_existing_with_empty_description(self, db_with_drafts):
        """When a manifest entry exists but has empty description and the
        DB now has one, backfill should update the manifest entry."""
        existing_entry = {
            "id": 1,
            "title": "Draft Episode Alpha",
            "date": "2026-03-28",
            "draft_key": "drafts/2026/03/2026-03-28-alpha-abc123.mp3",
            "description": "",
        }
        client = FakeR2({"manifest.json": {"drafts": [existing_entry], "conferences": {}}})

        actions = backfill_manifest(
            db_path=db_with_drafts,
            client=client,
            admin_bucket="podcast-admin",
        )

        updated = [a for _, _, a in actions if a == "updated"]
        assert len(updated) == 1

        manifest = json.loads(client.objects["manifest.json"])
        ep1 = next(d for d in manifest["drafts"] if d["id"] == 1)
        assert ep1["description"] == "Alpha episode description."

    def test_backfill_updates_existing_with_stale_draft_key(self, db_with_drafts):
        """Backfill should repair stale manifest paths, not only blank descriptions."""
        existing_entry = {
            "id": 1,
            "title": "Draft Episode Alpha",
            "date": "2026-03-28",
            "draft_key": "public/2026/03/2026-03-28-alpha-abc123.mp3",
            "draft_stem": "public/2026/03/2026-03-28-alpha-abc123",
            "filename": "2026-03-28-alpha-abc123.mp3",
            "basename": "2026-03-28-alpha-abc123",
            "description": "Alpha episode description.",
        }
        client = FakeR2({"manifest.json": {"drafts": [existing_entry], "conferences": {}}})

        actions = backfill_manifest(
            db_path=db_with_drafts,
            client=client,
            admin_bucket="podcast-admin",
        )

        updated = [a for _, _, a in actions if a == "updated"]
        assert len(updated) == 1

        manifest = json.loads(client.objects["manifest.json"])
        ep1 = next(d for d in manifest["drafts"] if d["id"] == 1)
        assert ep1["draft_key"] == "drafts/2026/03/2026-03-28-alpha-abc123.mp3"
        assert ep1["draft_stem"] == "drafts/2026/03/2026-03-28-alpha-abc123"


    def test_backfill_enriches_sidecar(self, db_with_drafts, tmp_path):
        client = FakeR2({"manifest.json": {"drafts": [], "conferences": {}}})

        actions = backfill_manifest(
            db_path=db_with_drafts,
            client=client,
            admin_bucket="podcast-admin",
        )

        sidecar = tmp_path / "drafts" / "2026" / "03" / "2026-03-28-alpha-abc123.json"
        data = json.loads(sidecar.read_text())
        assert data["title"] == "Draft Episode Alpha"
        assert data["description"] == "Alpha episode description."
        assert data["source_urls"] == ["https://arxiv.org/pdf/2503.99999"]


# ---------------------------------------------------------------------------
# Generation worker integration: _publish_draft_metadata
# ---------------------------------------------------------------------------

class TestPublishDraftMetadata:
    def test_updates_manifest_after_generation(self, tmp_path, monkeypatch):
        """After a draft is generated and uploaded, _publish_draft_metadata
        should push a manifest entry from the DB row."""
        # Set up a minimal DB with one draft episode
        db_path = tmp_path / "papers.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                publish_date TEXT NOT NULL,
                spotify_url TEXT,
                elevenlabs_project_id TEXT,
                audio_file TEXT,
                created_at TEXT NOT NULL,
                source_urls TEXT,
                description TEXT,
                image_file TEXT
            )
        """)
        conn.execute("""
            INSERT INTO podcasts
            (title, publish_date, audio_file, created_at, source_urls, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "Test Generation Draft",
            "2026-03-29",
            str(tmp_path / "drafts" / "2026" / "03" / "2026-03-29-test-gen-aabbcc.mp3"),
            "2026-03-29T12:00:00+00:00",
            json.dumps(["https://arxiv.org/pdf/2503.11111"]),
            "Test generation description.",
        ))
        conn.commit()
        conn.close()

        # Create sidecar JSON
        draft_dir = tmp_path / "drafts" / "2026" / "03"
        draft_dir.mkdir(parents=True)
        (draft_dir / "2026-03-29-test-gen-aabbcc.json").write_text(
            json.dumps({"script": [], "sources": [], "topics": []})
        )

        # Mock DB connection to use our test DB
        monkeypatch.setattr("scripts.run_generation_worker.ROOT", tmp_path)

        r2_client = FakeR2({"manifest.json": {"drafts": [], "conferences": {}}})

        def fake_get_connection(db_path_arg=None):
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        def fake_init_db(conn):
            pass

        monkeypatch.setattr("db.get_connection", fake_get_connection)
        monkeypatch.setattr("db.init_db", fake_init_db)
        monkeypatch.setattr("r2_upload.get_r2_client", lambda: r2_client)

        from scripts.run_generation_worker import _publish_draft_metadata

        _publish_draft_metadata(
            str(tmp_path / "drafts" / "2026" / "03" / "2026-03-29-test-gen-aabbcc")
        )

        # Verify manifest was updated
        manifest = json.loads(r2_client.objects["manifest.json"])
        assert len(manifest["drafts"]) == 1
        assert manifest["drafts"][0]["title"] == "Test Generation Draft"

        # Verify sidecar was enriched
        sidecar = draft_dir / "2026-03-29-test-gen-aabbcc.json"
        data = json.loads(sidecar.read_text())
        assert data["title"] == "Test Generation Draft"
        assert data["description"] == "Test generation description."

    def test_nonfatal_on_missing_episode(self, tmp_path, monkeypatch):
        """If no DB row matches the draft stem, the function logs and
        returns without error."""
        db_path = tmp_path / "papers.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                publish_date TEXT NOT NULL,
                spotify_url TEXT,
                elevenlabs_project_id TEXT,
                audio_file TEXT,
                created_at TEXT NOT NULL,
                source_urls TEXT,
                description TEXT,
                image_file TEXT
            )
        """)
        conn.commit()
        conn.close()

        monkeypatch.setattr("scripts.run_generation_worker.ROOT", tmp_path)

        def fake_get_connection(db_path_arg=None):
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        def fake_init_db(conn):
            pass

        monkeypatch.setattr("db.get_connection", fake_get_connection)
        monkeypatch.setattr("db.init_db", fake_init_db)

        from scripts.run_generation_worker import _publish_draft_metadata

        # Should not raise
        _publish_draft_metadata("drafts/2026/03/nonexistent-draft")
