"""Detection tests, weighted towards the signatures that are easy to get wrong."""
from __future__ import annotations

import struct

import pytest
from conftest import FACTORIES, TEXT_SAMPLES, _bmp_bytes

from detect import _root_element, _sniff_bmp, _sniff_mpeg_audio, detect, sniff


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("ext", sorted(FACTORIES))
def test_binary_factories_detect_by_content(tmp_path, ext):
    path = write(tmp_path, f"s.{ext}", FACTORIES[ext]())
    assert detect(path, path.name).ext == ext


@pytest.mark.parametrize("ext", sorted(TEXT_SAMPLES))
def test_text_factories_detect(tmp_path, ext):
    path = write(tmp_path, f"s.{ext}", TEXT_SAMPLES[ext])
    assert detect(path, path.name).ext == ext


@pytest.mark.parametrize("magic,expected", [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "png"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 32, "jpg"),
    (b"GIF89a" + b"\x00" * 32, "gif"),
    (b"II*\x00" + b"\x00" * 32, "tiff"),
    (b"MM\x00*" + b"\x00" * 32, "tiff"),
    (b"%PDF-1.7\n" + b"x" * 32, "pdf"),
    (b"OggS" + b"\x00" * 32, "ogg"),
    (b"fLaC" + b"\x00" * 32, "flac"),
    (b"ID3\x03\x00" + b"\x00" * 32, "mp3"),
    (b"BOOKMOBI" + b"\x00" * 32, "mobi"),
    (b"{\\rtf1\\ansi}", "rtf"),
])
def test_plain_signatures(tmp_path, magic, expected):
    path = write(tmp_path, "f.bin", magic)
    assert sniff(path)[0] == expected


@pytest.mark.parametrize("form,expected", [
    (b"WAVE", "wav"), (b"WEBP", "webp"), (b"AVI ", "avi"),
])
def test_riff_container_is_split_by_form(tmp_path, form, expected):
    data = b"RIFF" + struct.pack("<I", 100) + form + b"\x00" * 64
    path = write(tmp_path, "f.bin", data)
    assert sniff(path)[0] == expected


def _ftyp(major: bytes, compatible: bytes = b"") -> bytes:
    body = major + b"\x00\x00\x00\x00" + compatible
    size = 8 + len(body)
    return struct.pack(">I", size) + b"ftyp" + body + b"\x00" * 64


@pytest.mark.parametrize("major,compatible,expected", [
    (b"isom", b"isomavc1mp41", "mp4"),
    (b"qt  ", b"", "mov"),
    (b"M4A ", b"", "m4a"),
    (b"avif", b"", "avif"),
    (b"heic", b"", "heic"),
    # The case that made a real AVIF get handed to FFmpeg: the major brand is
    # the generic still-image one, and only the compatible list names AVIF.
    (b"mif1", b"mif1avifmiaf", "avif"),
    (b"mif1", b"mif1heicmiaf", "heic"),
    # A still-image container we cannot pin down is still not a video.
    (b"mif1", b"mif1miaf", "heic"),
])
def test_isobmff_uses_compatible_brands(tmp_path, major, compatible, expected):
    path = write(tmp_path, "f.bin", _ftyp(major, compatible))
    assert sniff(path)[0] == expected


@pytest.mark.parametrize("doctype,expected", [(b"webm", "webm"), (b"matroska", "mkv")])
def test_matroska_doctype(tmp_path, doctype, expected):
    data = b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + doctype + b"\x00" * 40
    path = write(tmp_path, "f.bin", data)
    assert sniff(path)[0] == expected


# ------------------------------------------------------ the weak signatures

def test_bmp_accepts_a_real_bmp():
    data = _bmp_bytes()
    assert _sniff_bmp(data, len(data)) == ("bmp", "image/bmp")


def test_bmp_rejects_text_beginning_bm(tmp_path):
    """The bug this validation exists for: a note starting "BMW" went to
    ImageMagick because two bytes were treated as a signature."""
    note = b"BMW service notes\nOil change due at 90,000 miles.\n"
    path = write(tmp_path, "notes.txt", note)
    assert _sniff_bmp(note, len(note)) is None
    assert detect(path, "notes.txt").ext == "txt"


def test_bmp_rejects_wrong_declared_length():
    data = bytearray(_bmp_bytes())
    data[2:6] = struct.pack("<I", 999999)
    assert _sniff_bmp(bytes(data), len(data)) is None


def test_bmp_rejects_nonzero_reserved_bytes():
    data = bytearray(_bmp_bytes())
    data[6] = 0x01
    assert _sniff_bmp(bytes(data), len(data)) is None


def test_bmp_rejects_implausible_pixel_offset():
    data = bytearray(_bmp_bytes())
    data[10:14] = struct.pack("<I", 2)
    assert _sniff_bmp(bytes(data), len(data)) is None


def test_mp3_accepts_a_valid_frame_header():
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz.
    assert _sniff_mpeg_audio(b"\xff\xfb\x90\x00") == ("mp3", "audio/mpeg")


@pytest.mark.parametrize("header", [
    b"\xff\xfe\x00\x00",  # UTF-16 BOM: satisfies the sync bits, is not audio
    b"\xff\xfb\x00\x00",  # bitrate field 0000 ("free")
    b"\xff\xfb\xf0\x00",  # bitrate field 1111 (invalid)
    b"\xff\xfb\x9c\x00",  # sample-rate field 11 (reserved)
    b"\xff\xf9\x90\x00",  # layer field 00 (reserved)
])
def test_mp3_rejects_lookalikes(header):
    assert _sniff_mpeg_audio(header) is None


def test_utf16_bom_file_reads_as_text_not_mp3(tmp_path):
    path = write(tmp_path, "note.txt", "Hello, world.\n".encode("utf-16"))
    assert detect(path, "note.txt").ext == "txt"


def test_utf8_bom_file_reads_as_text(tmp_path):
    path = write(tmp_path, "note.txt", b"\xef\xbb\xbfHello.\n")
    assert detect(path, "note.txt").ext == "txt"


@pytest.mark.parametrize("text,expected", [
    ('<svg xmlns="x"></svg>', "svg"),
    ('<?xml version="1.0"?><svg></svg>', "svg"),
    ("<!-- a comment --><svg></svg>", "svg"),
    ("<!DOCTYPE html><html><body></body></html>", "html"),
    # The reason root-element matching exists: an inline chart in a web page.
    ("<!DOCTYPE html><html><body><svg></svg></body></html>", "html"),
])
def test_root_element_decides_svg_vs_html(text, expected):
    assert _root_element(text) == expected


def test_html_with_inline_svg_is_html(tmp_path):
    page = (b"<!DOCTYPE html>\n<html><body><h1>Report</h1>"
            b'<svg width="10" height="10"><rect/></svg></body></html>')
    path = write(tmp_path, "report.html", page)
    assert detect(path, "report.html").ext == "html"


# -------------------------------------------------------- zip-based formats

def _zip_with(tmp_path, name, entries, mimetype=None):
    import zipfile
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        if mimetype is not None:
            zf.writestr("mimetype", mimetype)
        for entry in entries:
            zf.writestr(entry, b"x")
    return path


@pytest.mark.parametrize("entry,expected", [
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
])
def test_ooxml_is_identified_by_inner_layout(tmp_path, entry, expected):
    path = _zip_with(tmp_path, "f.zip", [entry, "[Content_Types].xml"])
    assert sniff(path)[0] == expected


@pytest.mark.parametrize("mimetype,expected", [
    ("application/epub+zip", "epub"),
    ("application/vnd.oasis.opendocument.text", "odt"),
    ("application/vnd.oasis.opendocument.spreadsheet", "ods"),
    ("application/vnd.oasis.opendocument.presentation", "odp"),
])
def test_declared_mimetype_entry_wins(tmp_path, mimetype, expected):
    path = _zip_with(tmp_path, "f.zip", ["content.xml"], mimetype=mimetype)
    assert sniff(path)[0] == expected


# -------------------------------------------------------- extension handling

def test_renamed_text_file_is_caught(tmp_path):
    """A .txt renamed .png must never reach ImageMagick."""
    path = write(tmp_path, "movie.png", b"This is really just prose.\n")
    result = detect(path, "movie.png")
    assert result.ext == "txt"
    assert result.mismatch is True
    assert result.claimed_ext == "png"


def test_renamed_png_is_caught(tmp_path):
    path = write(tmp_path, "notes.mp4", FACTORIES["png"]())
    result = detect(path, "notes.mp4")
    assert result.ext == "png"
    assert result.mismatch is True


@pytest.mark.parametrize("name", ["photo.jpeg", "photo.jpg"])
def test_jpeg_and_jpg_are_not_a_mismatch(tmp_path, name):
    path = write(tmp_path, name, b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    assert detect(path, name).mismatch is False


@pytest.mark.parametrize("name", ["scan.tif", "scan.tiff"])
def test_tif_and_tiff_are_not_a_mismatch(tmp_path, name):
    path = write(tmp_path, name, b"II*\x00" + b"\x00" * 32)
    assert detect(path, name).mismatch is False


def test_unknown_content_falls_back_to_extension(tmp_path):
    path = write(tmp_path, "thing.xyz", b"\x01\x02\x00\x03" * 8)
    result = detect(path, "thing.xyz")
    assert result.source == "extension"
    assert result.ext == "xyz"


def test_empty_file_sniffs_to_nothing(tmp_path):
    path = write(tmp_path, "empty.png", b"")
    assert sniff(path) is None


def test_category_is_reported(tmp_path):
    path = write(tmp_path, "s.png", FACTORIES["png"]())
    result = detect(path, "s.png")
    assert result.category == "image"
    assert result.description == "PNG (image)"
