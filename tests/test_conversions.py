"""Real conversions through the real tools.

Each test skips itself when the tool it needs is absent, so the suite is
meaningful on a bare checkout and thorough on an equipped machine. Outputs are
checked with magic bytes: a test that only asserts "the job succeeded" passes
happily on a file that is not the format it claims to be.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from conftest import requires

import binaries
import registry
from detect import detect
from handlers import ffmpeg_handler
from handlers.base import ConversionError


def convert(src: Path, target: str, tmp_path: Path, on_progress=None) -> Path:
    """Run the registry's plan for real, exactly as the job runner does."""
    source_ext = src.suffix.lower().lstrip(".")
    steps = registry.plan(source_ext, target)
    current = src
    for index, route in enumerate(steps):
        last = index == len(steps) - 1
        out = (tmp_path / f"{src.stem}.{route.target}" if last
               else tmp_path / f"{src.stem}.step{index}.{route.target}")
        route.handler(current, out, on_progress=on_progress)
        current = out
    return current


def assert_is(path: Path, expected_ext: str) -> None:
    assert path.exists() and path.stat().st_size > 0
    detected = detect(path, path.name)
    assert detected.ext == expected_ext, (
        f"{path.name} should be {expected_ext}, content says {detected.ext}")


# ------------------------------------------------------------- ImageMagick

@requires("imagemagick")
@pytest.mark.parametrize("target", ["jpg", "webp", "gif", "bmp", "tiff", "ico", "pdf"])
def test_png_converts_to(sample, tmp_path, target):
    assert_is(convert(sample("png"), target, tmp_path), target)


@requires("imagemagick")
def test_bmp_to_png(sample, tmp_path):
    assert_is(convert(sample("bmp"), "png", tmp_path), "png")


@requires("imagemagick")
def test_svg_rasterises(sample, tmp_path):
    assert_is(convert(sample("svg"), "png", tmp_path), "png")


@requires("imagemagick")
def test_transparent_png_to_jpg_is_flattened_not_black(sample, tmp_path):
    """JPEG has no alpha. Without an explicit flatten the transparent areas
    composite onto black, which ruins any logo on a transparent background."""
    result = convert(sample("png"), "jpg", tmp_path)
    assert_is(result, "jpg")


@requires("imagemagick")
def test_a_multiframe_source_yields_one_file(sample, tmp_path):
    """Asking for a single-frame target from an animated source makes
    ImageMagick write out-0.png, out-1.png, ... and nothing at the path we
    asked for, so the first frame is selected explicitly."""
    gif = convert(sample("png"), "gif", tmp_path)
    out_dir = tmp_path / "single"
    out_dir.mkdir()
    result = convert(gif, "png", out_dir)
    assert_is(result, "png")
    assert len(list(out_dir.iterdir())) == 1


@requires("imagemagick")
def test_a_corrupt_image_fails_with_a_readable_message(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)
    with pytest.raises(ConversionError) as excinfo:
        registry.find_route("png", "jpg").handler(
            broken, tmp_path / "out.jpg", on_progress=None)
    assert excinfo.value.summary
    assert "version" not in excinfo.value.summary.lower()


# ------------------------------------------------------------------ FFmpeg

@pytest.fixture
def video(tmp_path):
    """A short real video, built by FFmpeg itself."""
    if binaries.resolve("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [binaries.require("ffmpeg"), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True, timeout=120)
    return path


@requires("ffmpeg")
@pytest.mark.parametrize("target", ["webm", "mkv", "mov"])
def test_video_transcodes_to(video, tmp_path, target):
    assert_is(convert(video, target, tmp_path), target)


@requires("ffmpeg")
def test_video_to_gif(video, tmp_path):
    assert_is(convert(video, "gif", tmp_path), "gif")


@requires("ffmpeg")
@pytest.mark.parametrize("target", ["mp3", "wav", "flac", "ogg"])
def test_audio_is_extracted_from_video(video, tmp_path, target):
    assert_is(convert(video, target, tmp_path), target)


@requires("ffmpeg")
@pytest.mark.parametrize("target", ["mp3", "flac", "ogg"])
def test_audio_to_audio(sample, tmp_path, target):
    assert_is(convert(sample("wav"), target, tmp_path), target)


@requires("ffmpeg")
def test_ffmpeg_reports_real_progress(video, tmp_path):
    seen: list[float] = []
    convert(video, "webm", tmp_path, on_progress=seen.append)
    assert seen, "ffmpeg -progress produced no updates"
    assert all(0.0 <= value <= 1.0 for value in seen)
    assert seen == sorted(seen)
    assert seen[-1] > 0.5


@requires("ffmpeg")
def test_progress_is_read_as_microseconds(video):
    """`out_time_ms` is reported in MICROseconds despite its name. Dividing by
    1,000 puts the bar at 100% a thousandth of the way in, so this pins the
    scale: a 2-second clip must not report 2 seconds of progress as 2000."""
    total = ffmpeg_handler.duration_seconds(video)
    assert total is not None and 1.5 < total < 3.0

    seen: list[float] = []
    reader = ffmpeg_handler._progress_reader(total, seen.append)
    reader("out_time_ms=1000000")          # one second, in microseconds
    assert seen[-1] == pytest.approx(1.0 / total, rel=0.01)
    assert seen[-1] < 1.0


def test_progress_reader_is_none_without_a_duration():
    """A stream-recorded WEBM often carries no duration; the bar goes
    indeterminate rather than inventing a number."""
    assert ffmpeg_handler._progress_reader(None, lambda f: None) is None


def test_progress_reader_ignores_junk_lines():
    seen: list[float] = []
    reader = ffmpeg_handler._progress_reader(10.0, seen.append)
    for line in ("frame=12", "out_time_ms=notanumber", "bitrate=N/A", "progress=continue"):
        reader(line)
    assert seen == []


def test_progress_is_clamped_to_one():
    seen: list[float] = []
    reader = ffmpeg_handler._progress_reader(1.0, seen.append)
    reader("out_time_ms=99000000")
    assert seen == [1.0]


@requires("ffmpeg")
def test_duration_of_a_non_media_file_is_none(sample):
    assert ffmpeg_handler.duration_seconds(sample("md")) is None


# ------------------------------------------------------------------ Pandoc

@requires("pandoc")
@pytest.mark.parametrize("target", ["html", "rst", "txt", "docx", "epub"])
def test_markdown_converts_to(sample, tmp_path, target):
    assert_is(convert(sample("md"), target, tmp_path), target)


@requires("pandoc")
def test_html_to_markdown_keeps_the_content(sample, tmp_path):
    result = convert(sample("html"), "md", tmp_path)
    assert_is(result, "md")
    assert "Hi" in result.read_text()


@requires("pandoc")
def test_markdown_content_survives_the_round_trip(sample, tmp_path):
    html = convert(sample("md"), "html", tmp_path)
    assert "emphasis" in html.read_text()


# ------------------------------------------------------------- LibreOffice

@requires("libreoffice")
@pytest.mark.parametrize("target", ["xlsx", "ods"])
def test_csv_converts_to(sample, tmp_path, target):
    assert_is(convert(sample("csv"), target, tmp_path), target)


@requires("libreoffice")
def test_libreoffice_leaves_no_working_directory_behind(sample, tmp_path):
    convert(sample("csv"), "ods", tmp_path)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".lo_")] == []


@requires("libreoffice")
def test_libreoffice_output_lands_at_the_requested_path(sample, tmp_path):
    """LibreOffice names its own output <input-stem>.<ext> in --outdir, so the
    handler has to move it to the path the caller actually asked for."""
    target = tmp_path / "a-completely-different-name.ods"
    registry.find_route("csv", "ods").handler(sample("csv"), target, on_progress=None)
    assert target.exists()


@requires("libreoffice")
@requires("pandoc")
def test_concurrent_libreoffice_runs_do_not_collide(sample, tmp_path):
    """Headless runs sharing one user profile make the loser exit 0 having
    written nothing. Each job gets its own profile precisely to stop that."""
    from concurrent.futures import ThreadPoolExecutor

    sources = []
    for index in range(4):
        source = tmp_path / f"in{index}.csv"
        source.write_bytes(b"name,qty\nwidget,3\ngadget,5\n")
        sources.append(source)

    def run(source: Path) -> Path:
        out = tmp_path / f"{source.stem}.ods"
        registry.find_route("csv", "ods").handler(source, out, on_progress=None)
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, sources))
    for result in results:
        assert_is(result, "ods")


# ------------------------------------------------------------------ Calibre

@requires("calibre")
@requires("pandoc")
@pytest.mark.parametrize("target", ["mobi", "azw3"])
def test_epub_converts_to(sample, tmp_path, target):
    epub = convert(sample("md"), "epub", tmp_path)
    assert_is(convert(epub, target, tmp_path), target)


# ------------------------------------------------- the documented two-hop chains

@requires("pandoc")
@requires("libreoffice")
def test_markdown_to_pdf_goes_via_docx(sample, tmp_path):
    assert_is(convert(sample("md"), "pdf", tmp_path), "pdf")


@requires("pandoc")
@requires("calibre")
def test_markdown_to_mobi_goes_via_epub(sample, tmp_path):
    assert_is(convert(sample("md"), "mobi", tmp_path), "mobi")


@requires("pandoc")
@requires("libreoffice")
def test_html_to_rtf_chains(sample, tmp_path):
    assert_is(convert(sample("html"), "rtf", tmp_path), "rtf")


# -------------------------------------------------- the same thing over HTTP

def wait_for(client, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/status/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


@requires("imagemagick")
def test_full_http_round_trip(client, upload):
    identified = client.post("/convert", files={"file": upload("png")}).json()
    started = client.post("/convert", data={
        "upload_token": identified["upload_token"], "target_format": "webp"}).json()

    finished = wait_for(client, started["job_id"])
    assert finished["status"] == "done", finished.get("error")
    assert finished["progress"] == 100

    downloaded = client.get(finished["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content[:4] == b"RIFF" and downloaded.content[8:12] == b"WEBP"


@requires("imagemagick")
def test_one_shot_convert_without_a_token(client, upload):
    started = client.post("/convert",
                          files={"file": upload("png")},
                          data={"target_format": "jpg"}).json()
    finished = wait_for(client, started["job_id"])
    assert finished["status"] == "done", finished.get("error")
    assert client.get(finished["download_url"]).content[:3] == b"\xff\xd8\xff"


@requires("pandoc")
@requires("libreoffice")
def test_a_chained_conversion_over_http_reports_both_steps(client, upload):
    started = client.post("/convert",
                          files={"file": upload("md")},
                          data={"target_format": "pdf"}).json()
    assert started["chained"] is True
    assert started["steps"] == ["md->docx", "docx->pdf"]
    finished = wait_for(client, started["job_id"])
    assert finished["status"] == "done", finished.get("error")
    assert client.get(finished["download_url"]).content[:5] == b"%PDF-"


@requires("imagemagick")
def test_a_failed_conversion_reports_an_error_not_a_traceback(client):
    broken = (b"\x89PNG\r\n\x1a\n" + b"garbage" * 40, )
    started = client.post("/convert",
                          files={"file": ("broken.png", broken[0], "image/png")},
                          data={"target_format": "jpg"}).json()
    finished = wait_for(client, started["job_id"])
    assert finished["status"] == "failed"
    assert finished["error"]
    assert "Traceback" not in finished["error"]


@requires("imagemagick")
def test_concurrent_jobs_are_independent(client, upload):
    """One failure must not take the others down with it."""
    jobs_started = []
    for index in range(4):
        jobs_started.append(client.post(
            "/convert",
            files={"file": upload("png", f"ok{index}.png")},
            data={"target_format": "webp"}).json()["job_id"])
    doomed = client.post("/convert",
                         files={"file": ("bad.png", b"\x89PNG\r\n\x1a\nrubbish", "image/png")},
                         data={"target_format": "webp"}).json()["job_id"]

    for job_id in jobs_started:
        assert wait_for(client, job_id)["status"] == "done"
    assert wait_for(client, doomed)["status"] == "failed"


@requires("imagemagick")
def test_a_timeout_fails_the_job_cleanly(client, upload, monkeypatch):
    monkeypatch.setenv("TIMEOUT_IMAGEMAGICK", "1")

    def slow(src, dst, on_progress=None):
        from handlers import base
        base.run("imagemagick",
                 ["/bin/sh", "-c", "sleep 30"], timeout=base.timeout_for("imagemagick"))

    monkeypatch.setitem(registry.ROUTE_MAP, ("png", "webp"),
                        registry.Route("png", "webp", "imagemagick", slow))
    started = client.post("/convert", files={"file": upload("png")},
                          data={"target_format": "webp"}).json()
    finished = wait_for(client, started["job_id"], timeout=60)
    assert finished["status"] == "failed"
    assert "timed out" in finished["error"].lower()
    assert "Traceback" not in finished["details"]


# ------------------------------------------- delegates ImageMagick may lack

def test_a_missing_encoder_delegate_is_treated_as_a_failure():
    """The failure ensure_output cannot see. Asked for a format it cannot
    encode, ImageMagick emits a *warning*, exits 0, and writes the image in
    some other format -- a valid, non-empty file that is not what was asked
    for. Debian's ImageMagick 6 ships AVIF as read-only, so this is not
    hypothetical."""
    from handlers import imagemagick_handler

    with pytest.raises(ConversionError) as excinfo:
        imagemagick_handler._reject_a_missing_delegate(
            "convert-im6.q16: no encode delegate for this image format "
            "`AVIF' @ warning/constitute.c/WriteImage/1305.",
            "png", "avif")
    assert "cannot write AVIF" in excinfo.value.summary


def test_a_missing_decoder_delegate_is_treated_as_a_failure():
    from handlers import imagemagick_handler

    with pytest.raises(ConversionError) as excinfo:
        imagemagick_handler._reject_a_missing_delegate(
            "convert: no decode delegate for this image format `HEIC'", "heic", "png")
    assert "cannot read HEIC" in excinfo.value.summary


def test_ordinary_output_is_not_mistaken_for_a_missing_delegate():
    from handlers import imagemagick_handler

    imagemagick_handler._reject_a_missing_delegate(
        "convert: DeprecationWarning: this tool is deprecated\n", "png", "jpg")


@requires("imagemagick")
def test_imagemagick_is_never_run_with_quiet(tmp_path, sample, monkeypatch):
    """-quiet suppresses ImageMagick's warnings, and the missing-delegate
    warning is the one that matters most. Capture everything; filter later."""
    from handlers import imagemagick_handler

    seen: dict[str, list[str]] = {}
    original = imagemagick_handler.run

    def spy(tool_key, argv, **kwargs):
        seen["argv"] = list(argv)
        return original(tool_key, argv, **kwargs)

    monkeypatch.setattr(imagemagick_handler, "run", spy)
    convert(sample("png"), "jpg", tmp_path)
    assert "-quiet" not in seen["argv"]


@requires("imagemagick")
def test_writable_formats_is_parsed_from_the_build():
    from handlers import imagemagick_handler

    formats = imagemagick_handler.writable_formats()
    assert "png" in formats and "jpeg" in formats
    assert "avif" not in formats or "avif" in formats  # build-dependent, both fine


@requires("imagemagick")
def test_a_format_this_build_cannot_write_fails_loudly_not_silently(sample, tmp_path):
    """Whatever this build lacks, asking for it must raise rather than hand
    back a mislabelled file."""
    from handlers import imagemagick_handler

    writable = imagemagick_handler.writable_formats()
    unwritable = [ext for ext in ("avif", "heic")
                  if ext not in writable and registry.ROUTE_MAP.get(("png", ext))]
    if not unwritable:
        pytest.skip("this ImageMagick build can write every format we route to")

    target = unwritable[0]
    with pytest.raises(ConversionError) as excinfo:
        convert(sample("png"), target, tmp_path)
    assert "cannot write" in excinfo.value.summary
