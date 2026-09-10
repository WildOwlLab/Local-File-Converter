"""FFmpeg: video and audio, and the only tool here that reports real progress."""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import binaries

from .base import finish, run, timeout_for

# Codec choices per container. A container is not free to hold any stream:
# H.264/AAC is illegal in WebM, and copying streams across families produces a
# file that plays nowhere.
_VIDEO_ARGS: dict[str, list[str]] = {
    "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"],
    "mkv": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"],
    "webm": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33",
             "-row-mt", "1", "-c:a", "libopus"],
}

_AUDIO_ARGS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
}

# One pass, but still a real palette: generating and applying it in a single
# filtergraph avoids the 256-colour default that turns gradients into mud.
_GIF_FILTER = ("fps=12,scale=480:-1:flags=lanczos,split[s0][s1];"
               "[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer")


def _ffprobe() -> str | None:
    """ffprobe ships beside ffmpeg, but is not itself in binaries.TOOLS: it is
    used for progress only, and a missing one is not a reason to refuse a job."""
    ffmpeg = binaries.resolve("ffmpeg")
    if ffmpeg:
        sibling = Path(ffmpeg).with_name(
            "ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def duration_seconds(src: Path) -> float | None:
    """Length of a media file, or None when the container does not say.

    A stream-recorded WEBM frequently carries no duration at all, which is why
    the caller has to cope with None rather than treating it as an error.
    """
    probe = _ffprobe()
    if probe is None:
        return None
    argv = [probe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(src)]
    try:
        code, out, _ = run("ffmpeg", argv, timeout=min(30, timeout_for("ffmpeg")))
    except Exception:
        return None
    if code != 0:
        return None
    try:
        value = float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    return value if value > 0 else None


def _progress_reader(total: float | None,
                     on_progress: Callable[[float], None] | None):
    """Parse `-progress` key=value lines into a 0..1 fraction."""
    if total is None or on_progress is None:
        return None

    def handle(line: str) -> None:
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        seconds: float | None = None
        if key == "out_time_ms" or key == "out_time_us":
            # Named "ms", reported in MICROseconds. Dividing by 1,000 puts the
            # bar at 100% about a thousandth of the way through the file.
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                return
        elif key == "out_time":
            parts = value.split(":")
            try:
                if len(parts) == 3:
                    seconds = (int(parts[0]) * 3600 + int(parts[1]) * 60
                               + float(parts[2]))
            except ValueError:
                return
        if seconds is not None and seconds >= 0:
            on_progress(min(1.0, seconds / total))

    return handle


def _run_ffmpeg(src: Path, dst: Path, middle: list[str],
                on_progress: Callable[[float], None] | None) -> None:
    binary = binaries.require("ffmpeg")
    total = duration_seconds(src) if on_progress else None
    argv = [binary, "-hide_banner", "-nostdin", "-y", "-i", str(src)]
    argv += middle
    if total is not None:
        # Machine-readable progress on stdout; the human-readable stats on
        # stderr would otherwise have to be scraped out of the error text.
        argv += ["-progress", "pipe:1", "-nostats"]
    argv += [str(dst)]

    code, out, err = run("ffmpeg", argv,
                         on_stdout_line=_progress_reader(total, on_progress))
    finish("FFmpeg", code, out, err, dst)


def convert_media(src: Path, dst: Path,
                  on_progress: Callable[[float], None] | None = None) -> None:
    """Video to video, re-encoding into whatever the container accepts."""
    target = dst.suffix.lower().lstrip(".")
    _run_ffmpeg(src, dst, _VIDEO_ARGS.get(target, []), on_progress)


def to_gif(src: Path, dst: Path,
           on_progress: Callable[[float], None] | None = None) -> None:
    _run_ffmpeg(src, dst, ["-vf", _GIF_FILTER, "-loop", "0"], on_progress)


def extract_audio(src: Path, dst: Path,
                  on_progress: Callable[[float], None] | None = None) -> None:
    """Audio out of video, or audio to audio.

    `-vn` drops the video stream rather than trying to encode it into an audio
    container, which is both slower and usually an error.
    """
    target = dst.suffix.lower().lstrip(".")
    _run_ffmpeg(src, dst, ["-vn"] + _AUDIO_ARGS.get(target, []), on_progress)
