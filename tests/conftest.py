"""Shared fixtures.

Sample files are built at runtime rather than committed: binaries in a repo go
stale, bloat the clone and tell you nothing about why they are shaped the way
they are. Everything here is small enough to be generated in milliseconds.
"""
from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

# Sweep aggressively during tests, and keep the app's own temp directory out of
# the way. Both are read at import time in jobs.py, so they must be set before
# anything imports it.
os.environ.setdefault("TEMP_MAX_AGE_SECONDS", "3600")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import binaries  # noqa: E402
import jobs  # noqa: E402
import main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose temp directory is the test's own tmp_path.

    Without this the suite writes into the repo's temp/ and tests can see each
    other's leftovers.
    """
    from starlette.testclient import TestClient

    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path / "temp")
    jobs.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    jobs.store._jobs.clear()
    jobs.pending._items.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    jobs.store._jobs.clear()
    jobs.pending._items.clear()


def requires(tool_key: str):
    """Skip a test when the external tool it needs is not installed.

    Keeps the suite meaningful on a bare checkout and more thorough on a
    machine with the tools, instead of failing on something the code does not
    control.
    """
    return pytest.mark.skipif(
        binaries.resolve(tool_key) is None,
        reason=f"{tool_key} is not installed",
    )


# ------------------------------------------------------------ file factories

def _png_bytes(width: int = 8, height: int = 8) -> bytes:
    """A real, decodable PNG. Handwritten so the tests need no image library."""
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    rows = b""
    for y in range(height):
        row = bytearray(b"\x00")  # per-row filter byte: none
        for x in range(width):
            row += bytes(((x * 7 + y * 13) % 256, (x * 31) % 256, 200))
        rows += bytes(row)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _bmp_bytes(width: int = 4, height: int = 4) -> bytes:
    """A real 24-bit BMP, with a header that survives detect._sniff_bmp."""
    row_bytes = width * 3
    padding = (4 - row_bytes % 4) % 4
    pixels = b"".join(bytes([20, 120, 220] * width) + b"\x00" * padding
                      for _ in range(height))
    offset = 14 + 40
    size = offset + len(pixels)
    header = b"BM" + struct.pack("<IHHI", size, 0, 0, offset)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0,
                       len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def _wav_bytes(seconds: float = 0.25, rate: int = 8000) -> bytes:
    """A silent but structurally valid PCM WAV."""
    frames = int(rate * seconds)
    data = b"\x00\x00" * frames
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


FACTORIES = {
    "png": _png_bytes,
    "bmp": _bmp_bytes,
    "wav": _wav_bytes,
}

TEXT_SAMPLES = {
    "md": b"# Title\n\nA paragraph with *emphasis* and a [link](https://example.com).\n",
    "txt": b"Just some plain text.\nA second line.\n",
    "html": b"<!DOCTYPE html>\n<html><head><title>T</title></head>"
            b"<body><h1>Hi</h1><p>Body</p></body></html>\n",
    "rst": b"Title\n=====\n\nA paragraph.\n",
    "csv": b"name,qty\nwidget,3\ngadget,5\n",
    "svg": b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
           b'width="16" height="16"><rect width="16" height="16" fill="red"/></svg>\n',
}


@pytest.fixture
def sample(tmp_path):
    """Build a sample file of a given extension, returning its path."""
    def build(ext: str, name: str | None = None) -> Path:
        path = tmp_path / (name or f"sample.{ext}")
        if ext in FACTORIES:
            path.write_bytes(FACTORIES[ext]())
        elif ext in TEXT_SAMPLES:
            path.write_bytes(TEXT_SAMPLES[ext])
        else:
            raise ValueError(f"no factory for .{ext}")
        return path
    return build


@pytest.fixture
def upload(sample):
    """A (filename, bytes, mimetype) tuple ready for TestClient's files=."""
    def build(ext: str, name: str | None = None) -> tuple[str, bytes, str]:
        path = sample(ext, name)
        return (path.name, path.read_bytes(), "application/octet-stream")
    return build
