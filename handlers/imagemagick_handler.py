"""ImageMagick: raster images, plus SVG and ICO in one direction each."""
from __future__ import annotations

import functools
import re
from collections.abc import Callable
from pathlib import Path

import binaries

from .base import ConversionError, finish, run

# Formats that can hold more than one frame. Asking for a single-frame target
# from one of these makes ImageMagick write out-0.png, out-1.png, ... and no
# file at the path we asked for, so the first frame is selected explicitly.
_MULTI_FRAME = {"gif", "tiff", "webp", "avif", "ico"}
_SINGLE_FRAME_TARGET = {"png", "jpg", "bmp", "ico"}

# Targets with no alpha channel. Without flattening, ImageMagick composites
# transparency onto black, which turns a logo on a transparent background into
# a logo on a black rectangle.
_NO_ALPHA = {"jpg", "bmp"}


def convert_image(src: Path, dst: Path,
                  on_progress: Callable[[float], None] | None = None) -> None:
    binary = binaries.require("imagemagick")
    source_ext = src.suffix.lower().lstrip(".")
    target_ext = dst.suffix.lower().lstrip(".")

    # ImageMagick 7 is invoked as `magick in out`, with no subcommand. The v6
    # compatibility shim, `magick convert in out`, still works but prints a
    # deprecation warning to stderr on every single run -- which then gets
    # picked up as the error message the moment something genuinely fails. On a
    # v6 install the binary is itself named `convert`, so the same form works
    # there and binaries.resolve() has already picked the right one.
    # Note there is no -quiet here, deliberately. It suppresses ImageMagick's
    # warnings, and "no encode delegate for this image format" -- the one that
    # means the output is silently the wrong format -- is a warning. Noise is
    # filtered when the error is summarised, not by throwing it away here.
    argv: list[str] = [binary]

    if source_ext == "svg":
        # Vector input has no inherent pixel size; rasterise at something
        # usable rather than the 96 DPI default, which produces a thumbnail.
        argv += ["-background", "none", "-density", "384"]

    read_spec = str(src)
    if source_ext in _MULTI_FRAME and target_ext in _SINGLE_FRAME_TARGET:
        read_spec = f"{src}[0]"
    argv += [read_spec]

    if target_ext in _NO_ALPHA:
        argv += ["-background", "white", "-alpha", "remove", "-alpha", "off"]

    argv += [str(dst)]

    code, out, err = run("imagemagick", argv)
    _reject_a_missing_delegate(err + "\n" + out, source_ext, target_ext)
    finish("ImageMagick", code, out, err, dst)


# ImageMagick reads and writes most formats through optional delegate
# libraries, and which ones a build has is decided by whoever packaged it.
# Debian's legacy ImageMagick 6, for instance, ships AVIF as "r--": read only.
_MISSING_ENCODER = re.compile(r"no encode delegate for this image format", re.I)
_MISSING_DECODER = re.compile(r"no decode delegate for this image format", re.I)


def _reject_a_missing_delegate(output: str, source_ext: str, target_ext: str) -> None:
    """Fail on a missing delegate, which ImageMagick reports and then ignores.

    This is the one failure `ensure_output` cannot catch. Asked for a format it
    cannot encode, ImageMagick prints a *warning*, exits 0, and writes the image
    in whatever format it fell back to -- so the file exists, is a perfectly
    valid image, is not empty, and is not the format the user asked for. It
    downloads as photo.avif and is a WebP inside.
    """
    if _MISSING_ENCODER.search(output):
        raise ConversionError(
            f"This ImageMagick build cannot write {target_ext.upper()}: it was "
            f"compiled without the encoder for it. Run `magick -list format` to "
            f"see which formats your build supports writing.",
            details=output.strip(),
        )
    if _MISSING_DECODER.search(output):
        raise ConversionError(
            f"This ImageMagick build cannot read {source_ext.upper()}: it was "
            f"compiled without the decoder for it. Run `magick -list format` to "
            f"see which formats your build supports reading.",
            details=output.strip(),
        )


@functools.lru_cache(maxsize=1)
def writable_formats() -> frozenset[str]:
    """Formats this ImageMagick build can actually write, lowercased.

    Parsed from `-list format`, whose mode column reads like "rw+" or "r--".
    Cached: it costs a subprocess and cannot change while the server runs.
    """
    binary = binaries.resolve("imagemagick")
    if binary is None:
        return frozenset()
    try:
        code, out, err = run("imagemagick", [binary, "-list", "format"], timeout=30)
    except ConversionError:
        return frozenset()
    if code != 0:
        return frozenset()
    found = set()
    for line in (out + "\n" + err).splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[2]) == 3 and parts[2][1] == "w":
            found.add(parts[0].rstrip("*").lower())
    return frozenset(found)
