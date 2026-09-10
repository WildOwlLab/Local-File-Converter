"""Job store, held uploads and temp-file lifecycle."""
from __future__ import annotations

import time

import jobs
from jobs import DONE, FAILED, QUEUED, RUNNING, Job, JobStore, PendingStore


def make_store():
    return JobStore()


def test_create_returns_a_queued_job(tmp_path):
    store = make_store()
    job = store.create("a.png", "png", "jpg", "PNG (image)", tmp_path / "a.png")
    assert job.status == QUEUED
    assert store.get(job.id) is job


def test_job_ids_are_unique(tmp_path):
    store = make_store()
    ids = {store.create("a.png", "png", "jpg", "", tmp_path / "a.png").id
           for _ in range(200)}
    assert len(ids) == 200


def test_get_unknown_job_is_none():
    assert make_store().get("nope") is None


def test_update_sets_fields(tmp_path):
    store = make_store()
    job = store.create("a.png", "png", "jpg", "", tmp_path / "a.png")
    store.update(job.id, status=RUNNING, progress=42, stage="png -> jpg")
    assert (job.status, job.progress, job.stage) == (RUNNING, 42, "png -> jpg")


def test_update_unknown_job_returns_none():
    assert make_store().update("nope", status=DONE) is None


def test_finishing_stamps_a_finish_time(tmp_path):
    store = make_store()
    job = store.create("a.png", "png", "jpg", "", tmp_path / "a.png")
    assert job.finished_at is None
    store.update(job.id, status=DONE)
    assert job.finished_at is not None


def test_finish_time_is_not_overwritten(tmp_path):
    store = make_store()
    job = store.create("a.png", "png", "jpg", "", tmp_path / "a.png")
    store.update(job.id, status=DONE)
    first = job.finished_at
    store.update(job.id, progress=100)
    assert job.finished_at == first


def test_public_hides_download_until_done(tmp_path):
    job = Job(id="x", filename="a.png", source_ext="png", target_format="jpg")
    assert job.public()["download_url"] is None
    job.status = DONE
    job.output_path = tmp_path / "a.jpg"
    assert job.public()["download_url"] == "/download/x"
    assert job.public()["output_name"] == "a.jpg"


def test_output_name_falls_back_to_the_target_extension():
    job = Job(id="x", filename="holiday.png", source_ext="png", target_format="jpg")
    assert job.output_name == "holiday.jpg"


def test_public_exposes_measured_flag():
    job = Job(id="x", filename="a.mp4", source_ext="mp4", target_format="webm")
    assert job.public()["measured"] is False
    job.measured = True
    assert job.public()["measured"] is True


def test_sweep_removes_only_finished_jobs(tmp_path):
    store = make_store()
    old_done = store.create("a", "png", "jpg", "", tmp_path / "a")
    old_running = store.create("b", "png", "jpg", "", tmp_path / "b")
    store.update(old_done.id, status=DONE)
    store.update(old_running.id, status=RUNNING)
    old_done.created_at = old_running.created_at = time.time() - 10_000

    assert store.sweep(max_age=3600) == 1
    assert store.get(old_done.id) is None
    assert store.get(old_running.id) is not None


def test_sweep_keeps_recent_jobs(tmp_path):
    store = make_store()
    job = store.create("a", "png", "jpg", "", tmp_path / "a")
    store.update(job.id, status=FAILED)
    assert store.sweep(max_age=3600) == 0


def test_sweep_deletes_the_job_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    store = make_store()
    job = store.create("a", "png", "jpg", "", tmp_path / "a")
    work = jobs.job_dir(job.id)
    (work / "leftover.bin").write_bytes(b"x")
    store.update(job.id, status=DONE)
    job.created_at = time.time() - 10_000
    store.sweep(max_age=3600)
    assert not work.exists()


# ------------------------------------------------------------ held uploads

def test_pending_round_trip(tmp_path):
    pending = PendingStore()
    path = tmp_path / "u.png"
    path.write_bytes(b"x")
    held = pending.add(path, "u.png", 1)
    taken = pending.take(held.token)
    assert taken is not None and taken.filename == "u.png"


def test_a_token_can_only_be_claimed_once(tmp_path):
    pending = PendingStore()
    path = tmp_path / "u.png"
    path.write_bytes(b"x")
    held = pending.add(path, "u.png", 1)
    assert pending.take(held.token) is not None
    assert pending.take(held.token) is None


def test_unknown_token_is_none():
    assert PendingStore().take("nope") is None


def test_pending_sweep_deletes_the_held_bytes(tmp_path):
    pending = PendingStore()
    path = tmp_path / "u.png"
    path.write_bytes(b"x")
    held = pending.add(path, "u.png", 1)
    held.created_at = time.time() - 10_000
    assert pending.sweep(max_age=3600) == 1
    assert not path.exists()
    assert pending.take(held.token) is None


# --------------------------------------------------------- temp dir sweeping

def test_temp_sweep_removes_stale_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    stale_file = tmp_path / "old.bin"
    stale_file.write_bytes(b"x")
    stale_dir = tmp_path / "olddir"
    stale_dir.mkdir()
    (stale_dir / "inner").write_bytes(b"x")
    old = time.time() - 10_000
    for entry in (stale_file, stale_dir):
        import os
        os.utime(entry, (old, old))

    assert jobs.sweep_temp_dir(max_age=3600) == 2
    assert not stale_file.exists() and not stale_dir.exists()


def test_temp_sweep_keeps_gitkeep(tmp_path, monkeypatch):
    """temp/ is tracked through .gitkeep; sweeping it away breaks a fresh clone."""
    import os
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    keep = tmp_path / ".gitkeep"
    keep.write_bytes(b"")
    old = time.time() - 10_000
    os.utime(keep, (old, old))
    jobs.sweep_temp_dir(max_age=3600)
    assert keep.exists()


def test_temp_sweep_keeps_fresh_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    (tmp_path / "new.bin").write_bytes(b"x")
    assert jobs.sweep_temp_dir(max_age=3600) == 0


def test_temp_sweep_on_a_missing_directory_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path / "gone")
    assert jobs.sweep_temp_dir() == 0


def test_job_dirs_are_isolated_per_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    assert jobs.job_dir("aaa") != jobs.job_dir("bbb")
    assert jobs.job_dir("aaa").exists()


def test_max_age_env_override(monkeypatch):
    monkeypatch.setenv("TEMP_MAX_AGE_SECONDS", "60")
    assert jobs._max_age() == 60.0
    monkeypatch.setenv("TEMP_MAX_AGE_SECONDS", "nonsense")
    assert jobs._max_age() == 3600.0
    monkeypatch.delenv("TEMP_MAX_AGE_SECONDS")
    assert jobs._max_age() == 3600.0
