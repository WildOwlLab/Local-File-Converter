"""Tool resolution, including the Windows trap that gave this module a reason
to exist."""
from __future__ import annotations

import pytest

import binaries


def test_every_tool_has_an_install_hint_per_platform():
    for tool in binaries.TOOLS:
        assert len(tool.hints) == 3
        assert all(hint.strip() for hint in tool.hints)
        assert tool.install_hint in tool.hints


def test_tools_by_key_matches_tools():
    assert set(binaries.TOOLS_BY_KEY) == {t.key for t in binaries.TOOLS}


def test_health_reports_every_tool():
    report = binaries.health()
    assert set(report) == {t.key for t in binaries.TOOLS}
    for entry in report.values():
        assert set(entry) == {"display", "present", "path", "handles", "install_hint"}
        assert isinstance(entry["present"], bool)


def test_missing_tool_message_names_the_tool_and_the_fix(monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda key: None)
    with pytest.raises(FileNotFoundError) as excinfo:
        binaries.require("ffmpeg")
    message = str(excinfo.value)
    assert "FFmpeg" in message
    assert "install" in message.lower()


def test_require_returns_the_resolved_path(monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda key: "/somewhere/ffmpeg")
    assert binaries.require("ffmpeg") == "/somewhere/ffmpeg"


# ------------------------------------------------------------ the convert trap

@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\convert.exe",
    r"c:\windows\system32\CONVERT.EXE",
    "C:/Windows/System32/convert.exe",
])
def test_system32_convert_is_refused_on_windows(monkeypatch, path):
    """System32's convert.exe turns FAT volumes into NTFS. Handing it an image
    would be both useless and alarming."""
    monkeypatch.setattr(binaries, "IS_WINDOWS", True)
    assert binaries._is_windows_convert_trap(path, "convert") is True


def test_imagemagick_elsewhere_on_windows_is_accepted(monkeypatch):
    monkeypatch.setattr(binaries, "IS_WINDOWS", True)
    path = r"C:\Program Files\ImageMagick-7.1.2\convert.exe"
    assert binaries._is_windows_convert_trap(path, "convert") is False


def test_magick_is_never_treated_as_the_trap(monkeypatch):
    monkeypatch.setattr(binaries, "IS_WINDOWS", True)
    assert binaries._is_windows_convert_trap(
        r"C:\Windows\System32\magick.exe", "magick") is False


def test_convert_is_fine_on_posix(monkeypatch):
    """On Linux `convert` is ImageMagick v6's real name."""
    monkeypatch.setattr(binaries, "IS_WINDOWS", False)
    assert binaries._is_windows_convert_trap("/usr/bin/convert", "convert") is False


def test_magick_is_preferred_over_convert():
    imagemagick = binaries.TOOLS_BY_KEY["imagemagick"]
    assert imagemagick.commands.index("magick") < imagemagick.commands.index("convert")


def test_resolve_skips_the_trap_and_keeps_looking(monkeypatch):
    monkeypatch.setattr(binaries, "IS_WINDOWS", True)
    monkeypatch.setattr(binaries.shutil, "which",
                        lambda cmd: r"C:\Windows\System32\convert.exe"
                        if cmd == "convert" else None)
    monkeypatch.setattr(binaries.glob, "glob", lambda pattern: [])
    assert binaries.resolve("imagemagick") is None
