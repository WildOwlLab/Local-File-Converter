"""The HTTP surface, exercised through the real app."""
from __future__ import annotations

import io

import pytest
from conftest import FACTORIES

import jobs
import main


def identify(client, upload_tuple):
    return client.post("/convert", files={"file": upload_tuple})


# ------------------------------------------------------------------- /health

def test_health_reports_tools_and_the_cap(client):
    body = client.get("/health").json()
    assert set(body) == {"ok", "tools", "missing", "max_upload_mb"}
    assert body["max_upload_mb"] == main.MAX_UPLOAD_MB
    assert isinstance(body["missing"], list)
    assert body["ok"] == (not body["missing"])


def test_health_lists_all_five_tools(client):
    assert set(client.get("/health").json()["tools"]) == {
        "imagemagick", "ffmpeg", "pandoc", "libreoffice", "calibre"}


# ---------------------------------------------------------------- /supported

def test_supported_returns_the_matrix(client):
    body = client.get("/supported").json()
    assert "png" in body["matrix"]
    assert "jpg" in body["matrix"]["png"]["targets"]
    assert set(body["tools_present"]) == set(body["matrix"]["png"]["tools"]) | {
        "imagemagick", "ffmpeg", "pandoc", "libreoffice", "calibre"}


def test_supported_separates_direct_from_chained(client):
    entry = client.get("/supported").json()["matrix"]["md"]
    assert "html" in entry["direct"]
    assert "pdf" in entry["chained"]
    assert not set(entry["direct"]) & set(entry["chained"])


# ------------------------------------------------- /convert: identify only

def test_identify_returns_a_token_and_the_real_type(client, upload):
    body = identify(client, upload("png")).json()
    assert body["job_id"] is None
    assert body["upload_token"]
    assert body["detected_ext"] == "png"
    assert body["detection_source"] == "content"
    assert "jpg" in body["suggested_targets"]


def test_identify_holds_the_bytes_on_the_server(client, upload):
    body = identify(client, upload("png")).json()
    held = jobs.pending.take(body["upload_token"])
    assert held is not None and held.path.exists()


def test_identify_flags_a_renamed_file(client):
    """A .txt renamed .png is reported as text, not handed to ImageMagick."""
    response = client.post("/convert", files={
        "file": ("movie.png", b"This is prose, not a picture.\n", "image/png")})
    body = response.json()
    assert body["detected_ext"] == "txt"
    assert body["extension_mismatch"] is True
    assert body["claimed_ext"] == "png"


def test_identify_splits_direct_and_chained_targets(client, upload):
    body = identify(client, upload("md")).json()
    assert "html" in body["direct_targets"]
    assert "pdf" in body["chained_targets"]


# ------------------------------------------------------- /convert: the errors

def test_empty_file_is_rejected(client):
    response = client.post("/convert", files={"file": ("empty.png", b"", "image/png")})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"]["message"].lower()


def test_no_file_and_no_token_is_unprocessable(client):
    assert client.post("/convert", data={"target_format": "png"}).status_code == 422


def test_unsupported_pair_is_refused_with_alternatives(client, upload):
    response = client.post("/convert",
                           files={"file": upload("png")},
                           data={"target_format": "mp3"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Cannot convert PNG to MP3" in detail["message"]
    assert "jpg" in detail["supported_targets"]


def test_oversize_upload_is_rejected_before_conversion(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)
    response = client.post("/convert", files={
        "file": ("big.png", io.BytesIO(b"x" * 4096), "image/png")})
    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]


def test_a_rejected_oversize_upload_leaves_no_file(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)
    client.post("/convert", files={"file": ("big.png", io.BytesIO(b"x" * 4096), "image/png")})
    leftovers = [p for p in jobs.TEMP_DIR.iterdir() if p.name != ".gitkeep"]
    assert leftovers == []


def test_stale_token_is_a_409_the_frontend_can_act_on(client):
    response = client.post("/convert", data={
        "upload_token": "definitely-not-a-real-token", "target_format": "jpg"})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "expired_token"


def test_a_token_cannot_be_reused(client, upload):
    token = identify(client, upload("png")).json()["upload_token"]
    first = client.post("/convert", data={"upload_token": token, "target_format": "jpg"})
    assert first.status_code == 200
    second = client.post("/convert", data={"upload_token": token, "target_format": "jpg"})
    assert second.status_code == 409


# -------------------------------------------------------- /status, /download

def test_unknown_job_status_is_404(client):
    assert client.get("/status/nope").status_code == 404


def test_unknown_job_download_is_404(client):
    assert client.get("/download/nope").status_code == 404


def test_download_before_done_is_409(client, tmp_path):
    job = jobs.store.create("a.png", "png", "jpg", "PNG (image)", tmp_path / "a.png")
    assert client.get(f"/download/{job.id}").status_code == 409


def test_download_of_a_vanished_file_is_410(client, tmp_path):
    job = jobs.store.create("a.png", "png", "jpg", "PNG (image)", tmp_path / "a.png")
    jobs.store.update(job.id, status=jobs.DONE, output_path=tmp_path / "gone.jpg")
    assert client.get(f"/download/{job.id}").status_code == 410


def test_status_shape(client, tmp_path):
    job = jobs.store.create("a.png", "png", "jpg", "PNG (image)", tmp_path / "a.png")
    body = client.get(f"/status/{job.id}").json()
    for key in ("job_id", "status", "progress", "measured", "stage",
                "error", "details", "output_name", "download_url"):
        assert key in body


# -------------------------------------------------------------- sanitisation

@pytest.mark.parametrize("raw,expected", [
    ("../../escape.png", "escape.png"),
    ("/etc/passwd", "passwd"),
    ("a<b>c:d.png", "a_b_c_d.png"),
    ("", "upload"),
    ("   ", "upload"),
    ("...", "upload"),
])
def test_safe_filename_defuses_paths_and_illegal_characters(raw, expected):
    assert main.safe_filename(raw) == expected


@pytest.mark.parametrize("raw", [
    "../../escape.png",
    "..\\..\\escape.png",     # a Windows-style path, wherever we are running
    "/etc/passwd",
    "....//....//escape.png",
    "sub/dir/file.png",
])
def test_no_sanitised_name_can_escape_its_directory(raw, tmp_path):
    """The exact output differs by platform -- on POSIX a backslash is a legal
    filename character, so it is replaced rather than treated as a separator --
    but the property that matters holds everywhere: what comes back is a single
    path component that cannot climb out of the job directory.

    The job directory is a real one from tmp_path rather than a hardcoded
    "/jobs/abc", which resolves to D:\\jobs\\abc on a Windows runner and made
    this assertion fail for a reason that had nothing to do with the property
    being tested."""
    from pathlib import PurePosixPath, PureWindowsPath

    cleaned = main.safe_filename(raw)
    assert cleaned
    assert "/" not in cleaned and "\\" not in cleaned
    assert PurePosixPath(cleaned).name == cleaned
    assert PureWindowsPath(cleaned).name == cleaned
    assert not cleaned.startswith(".")

    job_dir = (tmp_path / "jobs" / "abc").resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    resolved = (job_dir / cleaned).resolve()
    assert resolved.parent == job_dir
    assert job_dir in resolved.parents


@pytest.mark.parametrize("raw", ["con.png", "CON.png", "nul.txt", "com1.jpg", "lpt9.bmp"])
def test_windows_reserved_device_names_are_defused(raw):
    """CON, NUL and friends are devices whatever the extension. Opening one
    for writing does not create a file."""
    assert main.safe_filename(raw) != raw
    assert main.safe_filename(raw).startswith("file_")


def test_very_long_filenames_are_trimmed():
    name = "a" * 300 + ".png"
    result = main.safe_filename(name)
    assert len(result) <= 120
    assert result.endswith(".png")


def test_control_characters_are_stripped():
    assert "\n" not in main.safe_filename("we\nird.png")


def test_a_hostile_filename_survives_a_round_trip(client):
    response = client.post("/convert", files={
        "file": ("../../con.png", FACTORIES["png"](), "image/png")})
    assert response.status_code == 200
    assert "/" not in response.json()["filename"]
    assert response.json()["filename"].startswith("file_")
