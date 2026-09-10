"""Routing table, duplicate detection and chain planning."""
from __future__ import annotations

import pytest

import registry
from registry import UnsupportedConversion


def test_route_table_has_no_duplicate_pairs():
    """The import-time guard only fires on conflicting handlers, so check the
    stronger property here: one entry per pair, full stop."""
    pairs = [(r.source, r.target) for r in registry.ROUTES]
    assert len(pairs) == len(set(pairs))


def test_duplicate_route_raises_at_build_time():
    """Two tools claiming one pair must fail loudly. Left to table order, the
    wrong tool would quietly win and nobody would notice for months."""
    def handler_a(*a, **k): ...
    def handler_b(*a, **k): ...

    routes = [
        registry.Route("png", "jpg", "imagemagick", handler_a),
        registry.Route("png", "jpg", "ffmpeg", handler_b),
    ]
    with pytest.raises(RuntimeError, match="Duplicate route"):
        registry._build_route_map(routes)


def test_identical_handler_twice_is_not_a_conflict():
    def handler(*a, **k): ...
    routes = [registry.Route("png", "jpg", "imagemagick", handler)] * 2
    assert len(registry._build_route_map(routes)) == 1


def test_no_route_converts_a_format_to_itself():
    assert all(r.source != r.target for r in registry.ROUTES)


def test_every_route_names_a_known_tool():
    import binaries
    assert all(r.tool_key in binaries.TOOLS_BY_KEY for r in registry.ROUTES)


@pytest.mark.parametrize("source,target,tool", [
    ("png", "jpg", "imagemagick"),
    ("heic", "png", "imagemagick"),
    ("svg", "png", "imagemagick"),
    ("png", "pdf", "imagemagick"),
    ("mp4", "webm", "ffmpeg"),
    ("mp4", "gif", "ffmpeg"),
    ("mp4", "mp3", "ffmpeg"),
    ("wav", "mp3", "ffmpeg"),
    ("md", "html", "pandoc"),
    ("md", "docx", "pandoc"),
    ("docx", "odt", "libreoffice"),
    ("xlsx", "csv", "libreoffice"),
    ("docx", "pdf", "libreoffice"),
    ("epub", "mobi", "calibre"),
    ("epub", "pdf", "calibre"),
])
def test_expected_pairs_route_to_the_expected_tool(source, target, tool):
    assert registry.find_route(source, target).tool_key == tool


def test_pdf_is_write_only():
    """Reading PDF needs Ghostscript, which the app does not assume."""
    assert registry.direct_targets("pdf") == []
    assert registry.targets_for("pdf") == []


def test_heic_is_input_only():
    """ImageMagick reads HEIC via libheif but cannot write it."""
    assert registry.direct_targets("heic")
    assert all(r.target != "heic" for r in registry.ROUTES)


def test_svg_is_input_only():
    assert registry.direct_targets("svg")
    assert all(r.target != "svg" for r in registry.ROUTES)


# ------------------------------------------------------------------ chaining

def test_direct_pair_plans_a_single_step():
    assert len(registry.plan("png", "jpg")) == 1


@pytest.mark.parametrize("source,target", [
    ("md", "pdf"), ("md", "mobi"), ("epub", "odt"), ("docx", "azw3"),
    ("heic", "gif"),
])
def test_documented_chains_resolve_in_two_hops(source, target):
    steps = registry.plan(source, target)
    assert len(steps) == 2
    assert steps[0].source == source
    assert steps[1].target == target
    assert steps[0].target == steps[1].source


def test_md_to_pdf_goes_through_docx():
    steps = registry.plan("md", "pdf")
    assert [s.target for s in steps] == ["docx", "pdf"]
    assert [s.tool_key for s in steps] == ["pandoc", "libreoffice"]


def test_chain_prefers_a_lossless_image_hub():
    """A photo routed via BMP keeps its pixels; via JPEG it picks up artefacts
    it can never shed. The hub preference exists to stop that."""
    steps = registry.find_chain("heic", "gif")
    assert steps[0].target in ("png", "tiff")


def test_chained_targets_exclude_direct_ones():
    direct = set(registry.direct_targets("md"))
    assert not (direct & set(registry.chained_targets("md")))


def test_targets_for_is_direct_plus_chained():
    for source in ("png", "md", "mp4", "epub"):
        expected = set(registry.direct_targets(source)) | set(
            registry.chained_targets(source))
        assert set(registry.targets_for(source)) == expected


def test_a_source_never_chains_back_to_itself():
    for source in {r.source for r in registry.ROUTES}:
        assert source not in registry.chained_targets(source)


def test_chains_are_capped_at_two_hops():
    for source in {r.source for r in registry.ROUTES}:
        for target in registry.targets_for(source):
            assert len(registry.plan(source, target)) <= 2


# ----------------------------------------------------------- the refusal path

def test_unsupported_pair_raises_with_alternatives():
    with pytest.raises(UnsupportedConversion) as excinfo:
        registry.plan("png", "mp3")
    error = excinfo.value
    assert "PNG" in error.message and "MP3" in error.message
    assert "JPG" in error.message   # the alternatives are spelled out
    assert error.alternatives == registry.targets_for("png")


def test_unknown_source_says_nothing_is_available():
    with pytest.raises(UnsupportedConversion) as excinfo:
        registry.plan("xyz", "png")
    assert "No conversions are available" in excinfo.value.message


def test_find_route_does_not_silently_chain():
    """find_route is the direct lookup; chaining is plan()'s job."""
    with pytest.raises(UnsupportedConversion):
        registry.find_route("md", "pdf")


# -------------------------------------------------------------- the matrix

def test_matrix_covers_every_source():
    matrix = registry.matrix()
    assert set(matrix) == {r.source for r in registry.ROUTES}


def test_matrix_entries_are_well_formed():
    for source, entry in registry.matrix().items():
        assert entry["category"] != "" and source not in entry["targets"]
        assert set(entry["direct"]) <= set(entry["targets"])
        assert set(entry["chained"]) <= set(entry["targets"])
        assert entry["tools"]


def test_route_count_is_stable():
    """Not a vanity number: a change here means the supported matrix moved,
    which is exactly the kind of thing a diff should have to justify."""
    assert len(registry.ROUTE_MAP) == 192
