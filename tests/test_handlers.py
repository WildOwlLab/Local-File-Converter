"""The shared handler contract, and the traps each rule exists to prevent."""
from __future__ import annotations

import subprocess
import sys

import pytest

from handlers import base
from handlers.base import ConversionError, ensure_output, finish, summarize_stderr


def python(*code: str) -> list[str]:
    return [sys.executable, "-c", "".join(code)]


# ---------------------------------------------------------- reading failures

def test_summarize_skips_the_ffmpeg_banner():
    """Taking the first stderr line reports the version string as the error."""
    stderr = (
        "ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers\n"
        "  built with gcc 13 (Ubuntu)\n"
        "  configuration: --prefix=/usr --enable-gpl\n"
        "  libavutil      58. 29.100\n"
        "movie.mp4: No such file or directory\n"
    )
    assert summarize_stderr(stderr) == "movie.mp4: No such file or directory"


def test_summarize_skips_the_imagemagick_deprecation_warning():
    stderr = ("convert: DeprecationWarning: this tool is deprecated, use magick\n"
              "convert: unable to open image 'nope.png': No such file or directory\n")
    assert "unable to open image" in summarize_stderr(stderr)


def test_summarize_prefers_an_error_line_over_the_last_line():
    stderr = ("Reading input\n"
              "error: unsupported pixel format\n"
              "Cleaning up temporary files\n")
    assert summarize_stderr(stderr) == "error: unsupported pixel format"


def test_summarize_falls_back_to_the_last_meaningful_line():
    """Tools that never say "error" still put the complaint at the end."""
    stderr = "Starting\nSomething odd happened at line 4\n"
    assert summarize_stderr(stderr) == "Something odd happened at line 4"


def test_summarize_strips_the_ffmpeg_component_prefix():
    assert summarize_stderr("[libx264 @ 0x55f0] height not divisible by 2") \
        == "height not divisible by 2"


def test_summarize_of_nothing_is_empty():
    assert summarize_stderr("") == ""
    assert summarize_stderr("\n  \n") == ""


def test_summary_is_capped():
    assert len(summarize_stderr("error: " + "x" * 5000)) <= base.MAX_SUMMARY


def test_summarize_handles_carriage_returns():
    assert summarize_stderr("progress\rerror: broke\r") == "error: broke"


# ------------------------------------------------------- output verification

def test_missing_output_is_a_failure(tmp_path):
    """A zero exit code is not proof of success; several of these tools report
    success and write nothing."""
    with pytest.raises(ConversionError):
        ensure_output(tmp_path / "never-written.png")


def test_empty_output_is_a_failure(tmp_path):
    target = tmp_path / "out.png"
    target.write_bytes(b"")
    with pytest.raises(ConversionError):
        ensure_output(target)
    assert not target.exists()   # the useless file is not left behind


def test_real_output_passes(tmp_path):
    target = tmp_path / "out.png"
    target.write_bytes(b"data")
    ensure_output(target)


def test_ensure_output_uses_the_tool_message_when_it_has_one(tmp_path):
    with pytest.raises(ConversionError) as excinfo:
        ensure_output(tmp_path / "gone.png", stderr="error: disk is full")
    assert "disk is full" in excinfo.value.summary


def test_finish_reports_a_nonzero_exit(tmp_path):
    target = tmp_path / "out.png"
    target.write_bytes(b"data")
    with pytest.raises(ConversionError) as excinfo:
        finish("Pandoc", 1, "", "error: bad input", target)
    assert excinfo.value.summary == "error: bad input"


def test_finish_keeps_full_output_in_details(tmp_path):
    with pytest.raises(ConversionError) as excinfo:
        finish("Pandoc", 1, "stdout line", "line one\nerror: real problem", tmp_path / "x")
    assert "line one" in excinfo.value.details
    assert "stdout line" in excinfo.value.details


def test_finish_accepts_a_good_run(tmp_path):
    target = tmp_path / "out.png"
    target.write_bytes(b"data")
    finish("Pandoc", 0, "", "", target)


# ------------------------------------------------------------------ timeouts

def test_timeout_defaults_are_per_tool():
    assert base.timeout_for("ffmpeg") > base.timeout_for("imagemagick")


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("TIMEOUT_FFMPEG", "42")
    assert base.timeout_for("ffmpeg") == 42


@pytest.mark.parametrize("value", ["0", "-5", "nonsense"])
def test_bad_timeout_override_falls_back_to_the_default(monkeypatch, value):
    monkeypatch.setenv("TIMEOUT_PANDOC", value)
    assert base.timeout_for("pandoc") == base.DEFAULT_TIMEOUTS["pandoc"]


def test_timeout_raises_a_clean_error_not_a_traceback():
    """The streaming path must not call communicate() to clean up. The pump
    threads own these pipes and have closed them, so communicate() raises from
    inside its own reader threads and the user sees an I/O traceback instead of
    "timed out"."""
    with pytest.raises(ConversionError) as excinfo:
        base.run("pandoc", python("import time; time.sleep(30)"), timeout=1)
    assert "timed out" in excinfo.value.summary.lower()


def test_timeout_kills_the_child():
    """A timeout that leaves the process running is not a timeout: the job
    fails while a transcode carries on burning CPU behind it."""
    import os

    started = []
    original = subprocess.Popen

    def spy(*args, **kwargs):
        proc = original(*args, **kwargs)
        started.append(proc)
        return proc

    subprocess.Popen = spy
    try:
        with pytest.raises(ConversionError):
            base.run("pandoc", python("import time; time.sleep(30)"), timeout=1)
    finally:
        subprocess.Popen = original

    assert len(started) == 1
    proc = started[0]
    # wait() in the timeout path reaps it, so returncode is already set. This
    # is the assertion that matters, and it holds on every platform.
    assert proc.returncode is not None

    # Confirming with the OS that the pid is really gone, rather than a zombie
    # we merely stopped waiting on, is POSIX-specific: Windows keeps the handle
    # open for a reaped process, so os.kill(pid, 0) there proves nothing.
    if sys.platform != "win32":
        with pytest.raises(OSError):
            os.kill(proc.pid, 0)


def test_a_timed_out_run_leaves_nothing_registered():
    with pytest.raises(ConversionError):
        base.run("pandoc", python("import time; time.sleep(30)"), timeout=1)
    assert base.terminate_all() == 0


# ---------------------------------------------------------- running the tool

def test_run_captures_both_streams():
    code, out, err = base.run(
        "pandoc",
        python("import sys; print('to stdout'); print('to stderr', file=sys.stderr)"),
    )
    assert code == 0
    assert "to stdout" in out
    assert "to stderr" in err


def test_run_reports_the_exit_code():
    code, _, _ = base.run("pandoc", python("raise SystemExit(3)"))
    assert code == 3


def test_run_streams_lines_as_they_arrive():
    seen = []
    base.run("pandoc",
             python("import sys\n",
                    "for i in range(5): print(f'line{i}'); sys.stdout.flush()"),
             on_stdout_line=seen.append)
    assert seen == [f"line{i}" for i in range(5)]


def test_run_does_not_deadlock_on_a_large_output():
    """Both pipes get their own pump thread precisely so a chatty tool cannot
    fill a pipe buffer and wedge."""
    code, out, err = base.run(
        "pandoc",
        python("import sys\n",
               "for i in range(20000): print('x'*40)\n",
               "for i in range(20000): print('y'*40, file=sys.stderr)"),
        timeout=60,
    )
    assert code == 0
    assert len(out.splitlines()) == 20000
    assert len(err.splitlines()) == 20000


def test_a_missing_binary_is_a_conversion_error():
    with pytest.raises(ConversionError):
        base.run("pandoc", ["/definitely/not/a/real/binary", "--version"])


def test_never_uses_a_shell():
    """shell=True must appear nowhere: a filename is user input."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sources = list(root.glob("*.py")) + list((root / "handlers").glob("*.py"))
    offenders = [p.name for p in sources if "shell=True" in p.read_text()]
    assert offenders == []


def test_summarize_skips_the_libreoffice_javaldx_warning():
    """LibreOffice prefixes healthy headless runs with this, and it contains
    both "failed" and "may not function" -- so it outranks the real error on
    every single failure unless it is filtered out explicitly."""
    stderr = ("Warning: failed to launch javaldx - java may not function correctly\n"
              "Error: source file could not be loaded\n")
    assert summarize_stderr(stderr) == "Error: source file could not be loaded"


def test_javaldx_warning_alone_is_not_reported_as_an_error():
    stderr = "Warning: failed to launch javaldx - java may not function correctly\n"
    assert summarize_stderr(stderr) == ""
