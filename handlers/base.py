"""The contract every external tool invocation follows.

One place for the rules that apply to all five tools, because each of them has
at some point broken one of them:

* arguments are a list, never a shell string;
* the output path is explicit;
* stdout and stderr are captured;
* every call has a timeout;
* a zero exit code is not taken as proof that anything was written.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import process_group

# Per-tool defaults, in seconds. A video transcode is legitimately slower than
# a PNG resize, so one global timeout would either kill real work or let a
# wedged process sit there for ten minutes.
DEFAULT_TIMEOUTS: dict[str, int] = {
    "imagemagick": 120,
    "ffmpeg": 600,
    "pandoc": 180,
    "libreoffice": 300,
    "calibre": 300,
}


def timeout_for(tool_key: str) -> int:
    """The timeout for a tool, overridable with TIMEOUT_<TOOL>."""
    default = DEFAULT_TIMEOUTS.get(tool_key, 300)
    raw = os.environ.get(f"TIMEOUT_{tool_key.upper()}")
    if raw is None:
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return value if value > 0 else default


class ConversionError(Exception):
    """A conversion that failed for a reason worth showing the user.

    `summary` is one line for the UI. `details` is everything the tool said,
    for the collapsible debug section -- useful when the summary guessed wrong.
    """

    def __init__(self, summary: str, details: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.details = details


# ------------------------------------------------------- reading tool output

# Lines that are never the reason a conversion failed. These tools all lead
# with banners, build configuration and progress chatter, and FFmpeg in
# particular writes its entire normal operation to stderr.
_NOISE = re.compile(
    r"^(?:"
    r"ffmpeg version|ffprobe version|built with|configuration:|"
    r"lib(?:av|sw|postproc)\w*\s|"
    r"input #|output #|stream mapping|stream #|metadata:|duration:|"
    r"encoder\b|frame=|size=|video:|audio:|subtitle:|other streams:|"
    r"global headers:|muxing overhead|press \[q\]|"
    r"\[[\w/]+ @ 0x[0-9a-f]+\]\s*$|"
    # LibreOffice opens headless runs with this even on a completely healthy
    # conversion, and it matches every "errorish" word we look for. Left in, it
    # is reported as the cause of every LibreOffice failure and the real line
    # underneath it -- "Error: source file could not be loaded" -- is never seen.
    r"(?:warning:\s*)?failed to launch javaldx|"
    r"javaldx|dictionary|overwriting|last message repeated|"
    r"\s*$"
    r")",
    re.IGNORECASE,
)

# A line that names a problem. Preferred over merely being the last thing said.
_ERRORISH = re.compile(
    r"\b(?:error|fatal|cannot|can't|could not|couldn't|unable|no such|"
    r"not found|not exist|invalid|unsupported|unrecognized|failed|failure|"
    r"denied|refused|missing|no space|too large|corrupt|malformed)\b",
    re.IGNORECASE,
)

MAX_SUMMARY = 300


def summarize_stderr(text: str) -> str:
    """One line naming what went wrong, from a tool's output.

    Deliberately *not* the first line. Every one of these tools opens with a
    banner or a warning, so taking line one reports the version string as the
    error. This skips the known noise and prefers a line that actually names a
    problem, falling back to the last thing the tool said, which is where these
    tools usually put the complaint.
    """
    lines = [ln.strip() for ln in (text or "").replace("\r", "\n").splitlines()]
    candidates = [ln for ln in lines if ln and not _NOISE.match(ln)]
    if not candidates:
        # Output that is nothing but banners and warnings carries no reason for
        # the failure. Returning one of those lines anyway presents a routine
        # warning as the cause; the caller has a truthful fallback, and the raw
        # text is kept in `details` either way.
        return ""
    chosen = next((ln for ln in candidates if _ERRORISH.search(ln)), candidates[-1])
    # Strip FFmpeg's "[libx264 @ 0x55f0] " style prefix; it is not information.
    chosen = re.sub(r"^\[[^\]]+\]\s*", "", chosen).strip()
    if len(chosen) > MAX_SUMMARY:
        chosen = chosen[:MAX_SUMMARY - 1].rstrip() + "…"
    return chosen


def _details(stdout: str, stderr: str) -> str:
    parts = []
    if stderr.strip():
        parts.append(stderr.strip())
    if stdout.strip():
        parts.append(stdout.strip())
    return "\n".join(parts)


# --------------------------------------------------- running the tool itself

# Local conversion is supposed to mean local, and two of these tools break that
# on their own initiative: given an HTML or EPUB file containing
# <img src="http://...">, Pandoc and LibreOffice both fetch it. A tracking pixel
# in a document would therefore phone home the moment you converted it, telling
# the other end your IP address, that you have the document, and when you opened
# it. Nothing is uploaded, but something certainly leaves.
#
# Pointing every proxy variable at a closed port makes any HTTP attempt fail
# instantly. Both tools then finish the conversion without the remote resource
# instead of failing, which is the behaviour a local tool should have had all
# along. This is a backstop, not a sandbox: it cannot stop a program that
# ignores proxy settings and opens a socket directly. tests/test_privacy.py
# checks the tools we actually ship against a real listener.
_BLACKHOLE = "http://127.0.0.1:1"
NO_NETWORK_ENV: dict[str, str] = {
    "http_proxy": _BLACKHOLE, "HTTP_PROXY": _BLACKHOLE,
    "https_proxy": _BLACKHOLE, "HTTPS_PROXY": _BLACKHOLE,
    "ftp_proxy": _BLACKHOLE, "FTP_PROXY": _BLACKHOLE,
    "all_proxy": _BLACKHOLE, "ALL_PROXY": _BLACKHOLE,
    # Empty, so nothing is exempted from the above.
    "no_proxy": "", "NO_PROXY": "",
}


def child_env() -> dict[str, str]:
    """The environment every conversion subprocess runs in."""
    env = dict(os.environ)
    env.update(NO_NETWORK_ENV)
    return env


# Children currently running, so a shutdown can take them down with it.
_live: set[subprocess.Popen] = set()
_live_lock = threading.Lock()


def terminate_all() -> int:
    """Kill every conversion still running. Returns how many were stopped."""
    with _live_lock:
        running = list(_live)
    for proc in running:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                process_group.kill_tree(proc)
    return len(running)


def _pump(stream, sink: list[str], on_line: Callable[[str], None] | None) -> None:
    """Drain one pipe into a list, so neither pipe can fill and deadlock."""
    try:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            sink.append(line)
            if on_line is not None:
                with contextlib.suppress(Exception):
                    on_line(line)
    except (ValueError, OSError):
        pass  # pipe closed underneath us by a kill; nothing left to read
    finally:
        with contextlib.suppress(Exception):
            stream.close()


def run(tool_key: str,
        argv: Sequence[str],
        *,
        on_stdout_line: Callable[[str], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None) -> tuple[int, str, str]:
    """Run a tool to completion. Returns (returncode, stdout, stderr).

    Both pipes are drained by their own thread. That is what makes live
    progress possible, and it is also why the timeout path below cannot use
    the obvious `communicate()`.
    """
    limit = timeout if timeout is not None else timeout_for(tool_key)
    argv = [str(a) for a in argv]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            env=child_env(),
            **process_group.spawn_kwargs(),
        )
    except OSError as exc:
        raise ConversionError(f"Could not start {Path(argv[0]).name}: {exc}",
                              details=repr(exc)) from exc

    process_group.adopt(proc)
    with _live_lock:
        _live.add(proc)

    out_lines: list[str] = []
    err_lines: list[str] = []
    pumps = [
        threading.Thread(target=_pump, args=(proc.stdout, out_lines, on_stdout_line),
                         daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_lines, on_stderr_line),
                         daemon=True),
    ]
    for thread in pumps:
        thread.start()

    try:
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            process_group.kill_tree(proc)
            # Just wait(). NOT communicate(): the pump threads own these pipes
            # and have already closed them, so communicate() tries to read
            # closed streams and raises from inside its own reader threads --
            # a traceback about I/O on a closed file, in place of the timeout.
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
            for thread in pumps:
                thread.join(timeout=2)
            raise ConversionError(
                f"Conversion timed out after {limit} seconds.",
                details=_details("\n".join(out_lines), "\n".join(err_lines)),
            ) from None
    finally:
        with _live_lock:
            _live.discard(proc)

    for thread in pumps:
        thread.join(timeout=5)
    return proc.returncode, "\n".join(out_lines), "\n".join(err_lines)


def ensure_output(path: Path, stdout: str = "", stderr: str = "") -> None:
    """Confirm the tool actually produced something.

    A zero exit code is not proof of success. LibreOffice in particular will
    report success and write nothing at all when its user profile is contended,
    and an empty file downloads just as happily as a real one.
    """
    if not path.exists():
        raise ConversionError(
            summarize_stderr(stderr) or summarize_stderr(stdout)
            or "The converter reported success but wrote no output file.",
            details=_details(stdout, stderr),
        )
    if path.stat().st_size == 0:
        with contextlib.suppress(OSError):
            path.unlink()
        raise ConversionError(
            summarize_stderr(stderr) or summarize_stderr(stdout)
            or "The converter produced an empty file.",
            details=_details(stdout, stderr),
        )


def finish(tool_display: str, code: int, stdout: str, stderr: str,
           output_path: Path) -> None:
    """The tail of every handler: check the exit code, then check the file."""
    if code != 0:
        raise ConversionError(
            summarize_stderr(stderr) or summarize_stderr(stdout)
            or f"{tool_display} exited with code {code}.",
            details=_details(stdout, stderr),
        )
    ensure_output(output_path, stdout, stderr)
