"""The exhaustive sweep: every route in the registry, run for real.

Excluded from the default run because it takes minutes and needs all five
tools. Opt in with `pytest -m matrix`.

This is the test the previous handoff said had been lost. It is worth having
in the repo rather than in a scratch directory: it is the only thing that
proves the matrix the README advertises is actually deliverable, and it caught
routes that were reachable on paper but broken in practice.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import FACTORIES, TEXT_SAMPLES

import binaries
import registry
from detect import detect

pytestmark = pytest.mark.matrix


# Formats we can build directly, and the route used to derive each of the rest.
# Nothing installed here can *write* HEIC, so heic sources are skipped rather
# than faked -- see the README's note on generating one out of band.
_DERIVE_FROM = {
    "jpg": ("png", "jpg"), "webp": ("png", "webp"), "gif": ("png", "gif"),
    "tiff": ("png", "tiff"), "avif": ("png", "avif"), "ico": ("png", "ico"),
    "mp3": ("wav", "mp3"), "flac": ("wav", "flac"), "ogg": ("wav", "ogg"),
    "docx": ("md", "docx"), "epub": ("md", "epub"),
    "odt": ("docx", "odt"), "rtf": ("docx", "rtf"),
    "xlsx": ("csv", "xlsx"), "ods": ("csv", "ods"),
    "mobi": ("epub", "mobi"), "azw3": ("epub", "azw3"), "fb2": ("epub", "fb2"),
}

_UNBUILDABLE = {
    "heic": "nothing installed can write HEIC",
    "m4a": "needs a video source; covered by the video fixtures",
    "pptx": "no tool here creates a presentation from scratch",
    "odp": "no tool here creates a presentation from scratch",
}


def _build_video(tmp: Path, name: str, args: list[str]) -> Path:
    import subprocess
    path = tmp / name
    subprocess.run(
        [binaries.require("ffmpeg"), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=128x96:rate=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         *args, "-shortest", str(path)],
        check=True, capture_output=True, timeout=180)
    return path


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """One sample file per source format, built once for the whole sweep."""
    if any(binaries.resolve(tool.key) is None for tool in binaries.TOOLS):
        pytest.skip("the matrix sweep needs all five tools installed")

    tmp = tmp_path_factory.mktemp("matrix")
    built: dict[str, Path] = {}

    for ext, factory in FACTORIES.items():
        path = tmp / f"src.{ext}"
        path.write_bytes(factory())
        built[ext] = path
    for ext, data in TEXT_SAMPLES.items():
        if ext == "svg":
            path = tmp / f"src.{ext}"
            path.write_bytes(data)
            built[ext] = path
        else:
            path = tmp / f"src.{ext}"
            path.write_bytes(data)
            built[ext] = path

    built["mp4"] = _build_video(tmp, "src.mp4",
                                ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    built["mov"] = _build_video(tmp, "src.mov",
                                ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    built["mkv"] = _build_video(tmp, "src.mkv",
                                ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"])
    built["avi"] = _build_video(tmp, "src.avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame"])
    built["webm"] = _build_video(tmp, "src.webm",
                                 ["-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus"])
    built["m4a"] = _build_video(tmp, "src.m4a", ["-vn", "-c:a", "aac"])

    # Everything else is derived with the app's own routes, in dependency order.
    for ext, (source_ext, target) in _DERIVE_FROM.items():
        if source_ext not in built:
            continue
        out = tmp / f"src.{target}"
        try:
            registry.find_route(source_ext, target).handler(
                built[source_ext], out, on_progress=None)
            built[ext] = out
        except Exception:
            pass  # reported as a skip by the test that needed it

    return built


def _alias(ext: str) -> str:
    """ImageMagick names a couple of formats differently from the extension."""
    return {"jpg": "jpeg", "tiff": "tif"}.get(ext, ext)


def _running_as_root() -> bool:
    import os
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _routes():
    return sorted({(r.source, r.target) for r in registry.ROUTES})


@pytest.mark.parametrize("source,target", _routes(),
                         ids=[f"{s}->{t}" for s, t in _routes()])
def test_every_route_produces_the_format_it_claims(fixtures, tmp_path, source, target):
    if source in _UNBUILDABLE and source not in fixtures:
        pytest.skip(_UNBUILDABLE[source])
    if source not in fixtures:
        pytest.skip(f"no fixture could be built for .{source}")

    route = registry.find_route(source, target)

    # A route can be correct while the local build cannot run it. Which image
    # formats ImageMagick can write is decided by whoever compiled it, so a
    # missing delegate is an environment limit, not a broken route -- the
    # handler is separately tested to fail loudly rather than mislabel a file.
    if route.tool_key == "imagemagick":
        from handlers import imagemagick_handler
        writable = imagemagick_handler.writable_formats()
        if writable and target not in writable and _alias(target) not in writable:
            pytest.skip(f"this ImageMagick build cannot write {target.upper()}")

    # Calibre renders PDF through a headless Chromium, which refuses to run as
    # root without --no-sandbox. Weakening that for everyone to suit a container
    # would be the wrong trade, so the route is skipped here instead.
    if route.tool_key == "calibre" and target == "pdf" and _running_as_root():
        pytest.skip("Calibre's PDF output needs a non-root user (Chromium sandbox)")

    out = tmp_path / f"{source}_to.{target}"
    registry.find_route(source, target).handler(
        fixtures[source], out, on_progress=None)

    assert out.exists() and out.stat().st_size > 0
    detected = detect(out, out.name)
    # A handful of targets share a container with their siblings, so accept the
    # family rather than demanding a name the bytes cannot distinguish.
    equivalent = {
        "mkv": {"mkv", "webm"}, "webm": {"webm", "mkv"},
        "mov": {"mov", "mp4"}, "mp4": {"mp4", "mov"},
        "m4a": {"m4a", "mp4"}, "azw3": {"azw3", "mobi"}, "mobi": {"mobi", "azw3"},
        "fb2": {"fb2", "txt", "xml"},
        "avif": {"avif", "heic"},
        "txt": {"txt", "md", "csv", "rst"}, "md": {"md", "txt", "csv", "rst"},
        "rst": {"rst", "txt", "md"}, "csv": {"csv", "txt", "md"},
    }.get(target, {target})
    assert detected.ext in equivalent, (
        f"{source}->{target} produced {detected.ext}")


def test_the_matrix_runs_concurrently(fixtures, tmp_path):
    """Four jobs at once must not interfere: shared temp names, a shared
    LibreOffice profile or a shared job object would all show up here."""
    work = [("png", "jpg"), ("png", "webp"), ("md", "html"), ("csv", "ods")]

    def run(index_pair):
        index, (source, target) = index_pair
        out = tmp_path / f"c{index}.{target}"
        registry.find_route(source, target).handler(
            fixtures[source], out, on_progress=None)
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, enumerate(work)))
    assert all(p.exists() and p.stat().st_size > 0 for p in results)
