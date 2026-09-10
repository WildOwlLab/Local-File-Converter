"""Pandoc: markup formats (md, html, rst, txt, docx, epub)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import binaries

from .base import ConversionError, finish, run

# Pandoc's format names are not always the file extension.
_READ_FORMAT = {
    "md": "markdown", "html": "html", "rst": "rst",
    "txt": "markdown",          # plain text is valid markdown; nothing is lost
    "docx": "docx", "epub": "epub",
}
_WRITE_FORMAT = {
    "md": "markdown", "html": "html", "rst": "rst",
    "txt": "plain", "docx": "docx", "epub": "epub",
}
# Formats that are meaningless as a fragment and need a full document wrapper.
_STANDALONE = {"html", "docx", "epub", "rst"}


def convert_markup(src: Path, dst: Path,
                   on_progress: Callable[[float], None] | None = None) -> None:
    binary = binaries.require("pandoc")
    source_ext = src.suffix.lower().lstrip(".")
    target_ext = dst.suffix.lower().lstrip(".")

    argv = [binary]
    read_format = _READ_FORMAT.get(source_ext)
    write_format = _WRITE_FORMAT.get(target_ext)
    if read_format:
        argv += ["--from", read_format]
    if write_format:
        argv += ["--to", write_format]
    if target_ext in _STANDALONE:
        argv += ["--standalone"]
    if target_ext == "epub":
        # An EPUB with no title is technically invalid and some readers refuse
        # it outright. The filename is the only title we have.
        argv += ["--metadata", f"title={src.stem}"]
    argv += ["--output", str(dst), str(src)]

    code, out, err = run("pandoc", argv)

    # Pandoc writes PDF by shelling out to a LaTeX engine, which this app does
    # not require you to install. Its own message tells you to install pdflatex,
    # which is not the answer here: PDF is reached through DOCX and LibreOffice.
    if code != 0 and "pdflatex" in (err + out).lower():
        raise ConversionError(
            "Pandoc cannot write PDF directly without a LaTeX engine. "
            "This app converts to PDF through DOCX and LibreOffice instead.",
            details=(err or out),
        )
    finish("Pandoc", code, out, err, dst)
