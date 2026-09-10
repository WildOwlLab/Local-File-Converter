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
    required = {"display", "present", "usable", "problem", "path", "handles",
                "install_hint"}
    for entry in report.values():
        assert required <= set(entry)
        assert isinstance(entry["present"], bool)
        assert isinstance(entry["usable"], bool)
        # A tool cannot be usable without being present.
        assert not (entry["usable"] and not entry["present"])
        # And an unusable one must say why, or the report is not actionable.
        assert entry["usable"] or entry["problem"] or not entry["present"]


# ------------------------------------------- a tool present but half-installed

def test_libreoffice_without_modules_is_reported_unusable(monkeypatch):
    """`libreoffice-core` installs soffice and nothing that can open a
    document. Every conversion then exits 0 having written nothing, so
    reporting the binary as present was reporting it as working."""
    monkeypatch.setattr(binaries, "resolve", lambda key: "/usr/bin/soffice")
    monkeypatch.setattr(binaries, "libreoffice_modules", frozenset)

    reason = binaries.unusable_reason("libreoffice")
    assert reason and "without any document modules" in reason
    assert "libreoffice-writer" in reason      # names the fix


def test_libreoffice_with_modules_is_usable(monkeypatch):
    monkeypatch.setattr(binaries, "resolve", lambda key: "/usr/bin/soffice")
    monkeypatch.setattr(binaries, "libreoffice_modules",
                        lambda: frozenset({"Writer", "Calc", "Impress"}))
    assert binaries.unusable_reason("libreoffice") is None


def test_an_absent_libreoffice_is_missing_not_unusable(monkeypatch):
    """Two different problems with two different fixes."""
    monkeypatch.setattr(binaries, "resolve", lambda key: None)
    assert binaries.unusable_reason("libreoffice") is None


@pytest.mark.parametrize("key", ["imagemagick", "ffmpeg", "pandoc", "calibre"])
def test_single_binary_tools_are_never_reported_half_installed(key):
    """The other four are one executable: present or absent, nothing between."""
    assert binaries.unusable_reason(key) is None


def test_require_refuses_a_present_but_unusable_tool(monkeypatch):
    """Otherwise the failure surfaces as a conversion that produced nothing."""
    monkeypatch.setattr(binaries, "resolve", lambda key: "/usr/bin/soffice")
    monkeypatch.setattr(binaries, "libreoffice_modules", frozenset)
    with pytest.raises(FileNotFoundError, match="document modules"):
        binaries.require("libreoffice")


@pytest.mark.parametrize("ext,module,package", [
    ("xlsx", "Calc", "libreoffice-calc"),
    ("csv", "Calc", "libreoffice-calc"),
    ("docx", "Writer", "libreoffice-writer"),
    ("odt", "Writer", "libreoffice-writer"),
    ("pptx", "Impress", "libreoffice-impress"),
])
def test_a_missing_module_names_the_format_and_the_package(
        monkeypatch, ext, module, package):
    """Installed-but-without-Calc should say so, not fail generically."""
    monkeypatch.setattr(binaries, "IS_WINDOWS", False)
    monkeypatch.setattr(binaries, "IS_MACOS", False)
    monkeypatch.setattr(binaries, "libreoffice_modules",
                        lambda: frozenset({"Writer", "Calc", "Impress"}) - {module})
    reason = binaries.missing_module_reason(ext)
    assert reason and module in reason and ext.upper() in reason
    assert package in reason


def test_a_present_module_blocks_nothing(monkeypatch):
    monkeypatch.setattr(binaries, "libreoffice_modules",
                        lambda: frozenset({"Writer", "Calc", "Impress"}))
    assert binaries.missing_module_reason("xlsx") is None


def test_a_format_libreoffice_never_handles_is_not_blocked(monkeypatch):
    monkeypatch.setattr(binaries, "libreoffice_modules", frozenset)
    assert binaries.missing_module_reason("png") is None


def test_module_detection_reads_the_directory_beside_soffice(tmp_path, monkeypatch):
    """A directory listing, not a subprocess: cheap enough for a health check
    and it notices a module installed while the server is running."""
    program = tmp_path / "program"
    program.mkdir()
    (program / "soffice").write_bytes(b"")
    (program / "libswlo.so").write_bytes(b"")     # Writer only
    monkeypatch.setattr(binaries, "resolve", lambda key: str(program / "soffice"))
    assert binaries.libreoffice_modules() == frozenset({"Writer"})

    (program / "sclo.dll").write_bytes(b"")       # the Windows name for Calc
    assert binaries.libreoffice_modules() == frozenset({"Writer", "Calc"})


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
