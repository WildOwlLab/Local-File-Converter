"""Calibre's ebook-convert: epub, mobi, azw3, fb2 and ebook-to-PDF."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import binaries

from .base import finish, run


def convert_ebook(src: Path, dst: Path,
                  on_progress: Callable[[float], None] | None = None) -> None:
    binary = binaries.require("calibre")
    # ebook-convert takes input and output as plain paths and picks its
    # conversion pipeline from the extensions, so there is nothing to map.
    argv = [binary, str(src), str(dst)]
    code, out, err = run("calibre", argv)
    finish("Calibre", code, out, err, dst)
