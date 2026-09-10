"""Locating the external conversion tools.

Every handler resolves its binary through here so that "is the tool present?"
has exactly one answer, shared by /health and by the handlers themselves.
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# Extra places to look when a tool installs itself without putting the binary
# on PATH, which is the norm for the Windows installers of these projects.
# Entries are glob patterns: these installers bake the version into the
# directory name, so matching a literal path would break on every update.
_WINDOWS_HINTS: dict[str, tuple[str, ...]] = {
    "magick": (
        r"C:\Program Files\ImageMagick-*\magick.exe",
        r"C:\Program Files (x86)\ImageMagick-*\magick.exe",
    ),
    "soffice": (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ),
    "ebook-convert": (
        r"C:\Program Files\Calibre*\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre*\ebook-convert.exe",
    ),
    "pandoc": (
        r"C:\Program Files\Pandoc\pandoc.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Pandoc\pandoc.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\pandoc.exe"),
    ),
    "ffmpeg": (
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
    ),
}


# macOS app bundles keep their command-line tools inside the .app, and the
# installers do not add them to PATH.
_MACOS_HINTS: dict[str, tuple[str, ...]] = {
    "soffice": (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ),
    "ebook-convert": (
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    ),
}


@dataclass(frozen=True)
class Tool:
    key: str            # internal name used by handlers, e.g. "imagemagick"
    display: str        # human name for the UI
    commands: tuple[str, ...]  # candidate executables, in preference order
    handles: str        # what breaks without it, for the missing-tool banner
    hints: tuple[str, str, str]  # install command: windows, macos, linux

    @property
    def install_hint(self) -> str:
        """The install command for the platform actually running this."""
        windows, macos, linux = self.hints
        return windows if IS_WINDOWS else macos if IS_MACOS else linux


TOOLS: tuple[Tool, ...] = (
    Tool("imagemagick", "ImageMagick", ("magick", "convert"),
         "image conversions (png, jpg, webp, bmp, tiff)",
         ("winget install ImageMagick.ImageMagick",
          "brew install imagemagick",
          "sudo apt install imagemagick")),
    Tool("ffmpeg", "FFmpeg", ("ffmpeg",),
         "video and audio conversions (mp4, mov, webm, mp3, wav, gif)",
         ("winget install Gyan.FFmpeg",
          "brew install ffmpeg",
          "sudo apt install ffmpeg")),
    Tool("pandoc", "Pandoc", ("pandoc",),
         "markup conversions (md, html, docx, epub)",
         ("winget install JohnMacFarlane.Pandoc",
          "brew install pandoc",
          "sudo apt install pandoc")),
    Tool("libreoffice", "LibreOffice", ("soffice",),
         "office document conversions (docx, xlsx, pptx, odt, pdf)",
         ("winget install TheDocumentFoundation.LibreOffice",
          "brew install --cask libreoffice",
          "sudo apt install libreoffice")),
    Tool("calibre", "Calibre", ("ebook-convert",),
         "ebook conversions (epub, mobi, azw3)",
         ("winget install calibre.calibre",
          "brew install --cask calibre",
          "sudo apt install calibre")),
)

TOOLS_BY_KEY = {t.key: t for t in TOOLS}


def _is_windows_convert_trap(path: str, command: str) -> bool:
    """Windows ships its own convert.exe (the NTFS volume converter) in
    System32. It is not ImageMagick, and running it on a file would be both
    useless and alarming, so never accept it as an ImageMagick binary."""
    if not IS_WINDOWS or command != "convert":
        return False
    return "system32" in path.lower().replace("/", "\\")


# LibreOffice is a shell without its document modules. `libreoffice-core` on its
# own installs soffice and nothing that can open a document: every conversion
# exits 0 having written nothing, with "Error: source file could not be loaded"
# buried under a javaldx warning. Resolving the binary is therefore not evidence
# that a conversion will work, which is exactly what /health used to imply.
#
# Each module ships one library next to soffice, so this is a directory listing
# rather than a subprocess -- cheap enough for a health check, and it notices a
# module installed while the server is running.
_LO_MODULE_LIBRARIES: dict[str, tuple[str, ...]] = {
    "Writer": ("libswlo.so", "swlo.dll", "libswlo.dylib"),
    "Calc": ("libsclo.so", "sclo.dll", "libsclo.dylib"),
    "Impress": ("libsdlo.so", "sdlo.dll", "libsdlo.dylib"),
}

# Which module has to be present to open a given format.
MODULE_FOR_FORMAT: dict[str, str] = {
    "docx": "Writer", "odt": "Writer", "rtf": "Writer",
    "html": "Writer", "txt": "Writer", "md": "Writer",
    "xlsx": "Calc", "ods": "Calc", "csv": "Calc",
    "pptx": "Impress", "odp": "Impress",
}

_LO_INSTALL_HINT = {
    "Writer": "libreoffice-writer", "Calc": "libreoffice-calc",
    "Impress": "libreoffice-impress",
}


def libreoffice_modules() -> frozenset[str]:
    """Which LibreOffice document modules are actually installed."""
    binary = resolve("libreoffice")
    if binary is None:
        return frozenset()
    program_dir = os.path.dirname(os.path.realpath(binary))
    try:
        present = set(os.listdir(program_dir))
    except OSError:
        return frozenset()
    return frozenset(
        module for module, names in _LO_MODULE_LIBRARIES.items()
        if present & set(names)
    )


def unusable_reason(key: str) -> str | None:
    """Why a tool that resolves still cannot do its job, or None if it can.

    Only LibreOffice can be half-installed in a way the binary does not reveal;
    the other four are a single executable that either works or is absent.
    """
    if key != "libreoffice" or resolve(key) is None:
        return None
    modules = libreoffice_modules()
    if modules:
        return None
    return ("LibreOffice is installed without any document modules, so it "
            "cannot open a file at all. Install libreoffice-writer, "
            "libreoffice-calc and libreoffice-impress (or the full "
            "libreoffice package).")


def missing_module_reason(source_ext: str) -> str | None:
    """Why this particular format cannot be opened, or None."""
    module = MODULE_FOR_FORMAT.get(source_ext)
    if module is None or module in libreoffice_modules():
        return None
    package = _LO_INSTALL_HINT[module]
    return (f"LibreOffice is installed but without {module}, which is what "
            f"opens {source_ext.upper()} files. Install it with: "
            f"{'sudo apt install ' + package if not IS_WINDOWS and not IS_MACOS else 'the full LibreOffice package'}")


def resolve(key: str) -> str | None:
    """Absolute path to the executable for a tool key, or None if not installed."""
    tool = TOOLS_BY_KEY[key]
    for command in tool.commands:
        found = shutil.which(command)
        if found and not _is_windows_convert_trap(found, command):
            return found
    hints = (_WINDOWS_HINTS if IS_WINDOWS
             else _MACOS_HINTS if IS_MACOS else {})
    for pattern in hints.get(tool.commands[0], ()):
        # Newest match last alphabetically is the best guess for versioned
        # install directories (ImageMagick-7.1.2-29 beats -7.1.1-0).
        matches = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
        if matches:
            return matches[-1]
    return None


def require(key: str) -> str:
    """Resolve a usable tool, or raise a message aimed at the user.

    Refuses a tool that is present but cannot work, so the failure names the
    missing piece instead of surfacing as a conversion that silently produced
    nothing.
    """
    found = resolve(key)
    if found is None:
        tool = TOOLS_BY_KEY[key]
        raise FileNotFoundError(
            f"{tool.display} is not installed, so {tool.handles} are unavailable. "
            f"Install it with: {tool.install_hint}"
        )
    reason = unusable_reason(key)
    if reason is not None:
        raise FileNotFoundError(reason)
    return found


def health() -> dict[str, dict]:
    """Presence *and usability* of every tool, for GET /health.

    "present" means the executable was found. "usable" means it can actually
    convert something -- which is not the same question, and reporting only the
    first is how a half-installed LibreOffice came to be advertised as working.
    """
    report = {}
    for tool in TOOLS:
        path = resolve(tool.key)
        reason = unusable_reason(tool.key) if path else None
        report[tool.key] = {
            "display": tool.display,
            "present": path is not None,
            "usable": path is not None and reason is None,
            "problem": reason,
            "path": path,
            "handles": tool.handles,
            "install_hint": tool.install_hint,
        }
    if report.get("libreoffice", {}).get("present"):
        report["libreoffice"]["modules"] = sorted(libreoffice_modules())
    return report
