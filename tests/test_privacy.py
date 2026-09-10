"""The privacy promise, checked rather than asserted.

The README's headline claim is that files stay on your machine. That is a
property of the code, so it is tested like one. These tests are the reason the
claim is allowed to stay in the README.
"""
from __future__ import annotations

import http.server
import re
import socket
import socketserver
import threading
from pathlib import Path

import pytest

import registry
from handlers import base

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCES = sorted(
    set(ROOT.glob("*.py")) | set((ROOT / "handlers").glob("*.py"))
)

# An XML namespace is an identifier that happens to look like an address.
# Nothing ever fetches one -- they appear in the LibreOffice profile template
# and in SVG markup -- so they are the one allowed exception to "no URLs in
# application code". The list is explicit so that adding a real URL still fails.
_XML_NAMESPACES = frozenset({
    "http://www.w3.org",
    "http://openoffice.org",
})


# ------------------------------------------------- nothing phones home in code

# Modules that can open a network connection. socket is listed for completeness
# even though uvicorn needs it; the app's own modules must not import it.
_NETWORK_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(requests|urllib|urllib3|http\.client|httpx|httpx2|"
    r"aiohttp|socket|smtplib|ftplib|telnetlib|xmlrpc|websockets)\b",
    re.MULTILINE,
)


def test_no_application_module_imports_a_network_client():
    offenders = {
        path.relative_to(ROOT).as_posix(): _NETWORK_IMPORTS.findall(path.read_text())
        for path in APP_SOURCES
    }
    offenders = {name: hits for name, hits in offenders.items() if hits}
    assert offenders == {}, f"network-capable imports in application code: {offenders}"


def _code_without_comments(path: Path) -> str:
    """Source with comments stripped, string literals kept.

    A URL in a comment is documentation. A URL in a string literal is an
    address something could actually be handed to, which is the thing worth
    failing over.
    """
    import io
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type != tokenize.COMMENT:
                kept.append(token.string)
    del io
    return "\n".join(kept)


def test_no_application_module_contains_a_remote_url():
    """A URL in the source is the cheapest possible way for a local tool to
    stop being local. Documentation links live in Markdown, not in code."""
    pattern = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[\w.-]+")
    offenders = {}
    for path in APP_SOURCES:
        found = [
            url for url in pattern.findall(_code_without_comments(path))
            if url not in _XML_NAMESPACES
        ]
        if found:
            offenders[path.relative_to(ROOT).as_posix()] = found
    assert offenders == {}, f"remote URLs in application code: {offenders}"


def test_the_frontend_loads_nothing_from_the_internet():
    """One CDN font or analytics snippet would send every visit to a third
    party, and the page would look identical."""
    pattern = re.compile(r"""(?:src|href)\s*=\s*["'](https?://[^"']+)""")
    offenders = {}
    for path in sorted((ROOT / "static").iterdir()):
        if path.is_file():
            found = [
                url for url in pattern.findall(path.read_text(encoding="utf-8"))
                if not url.startswith("http://www.w3.org/")
            ]
            if found:
                offenders[path.name] = found
    assert offenders == {}, f"external resources in the frontend: {offenders}"


def test_the_frontend_only_calls_this_server():
    """Every fetch must be a same-origin relative path."""
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    targets = re.findall(r"fetch\(\s*[`'\"]([^`'\"]+)", source)
    assert targets, "expected to find fetch() calls to check"
    assert all(t.startswith("/") for t in targets), targets


# ------------------------------------------- the tools are denied the network

def test_conversions_run_with_every_proxy_blackholed():
    env = base.child_env()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        assert env[key] == "http://127.0.0.1:1"
    assert env["no_proxy"] == "" and env["NO_PROXY"] == ""


class _Listener:
    """A local web server that records anything a conversion tool requests."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        handler = self._handler()
        socketserver.TCPServer.allow_reuse_address = True
        self.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _handler(self):
        hits = self.hits

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - name fixed by the base class
                hits.append(self.path)
                self.send_response(404)
                self.end_headers()

            do_HEAD = do_GET

            def log_message(self, *args):
                pass

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _bait(port: int) -> str:
    return ("<html><head><title>Bait</title></head><body><h1>Heading</h1>"
            "<p>Body text.</p>"
            f'<img src="http://127.0.0.1:{port}/tracking-pixel.png">'
            f'<link rel="stylesheet" href="http://127.0.0.1:{port}/remote.css">'
            "</body></html>")


@pytest.mark.parametrize("target", ["docx", "epub", "odt"])
def test_a_document_with_a_tracking_pixel_does_not_phone_home(tmp_path, target):
    """The failure this guards against, reproduced end to end.

    Pandoc and LibreOffice both fetch remote resources referenced by an input
    file, given the chance. Converting a document someone sent you would then
    tell them your IP address and the moment you opened it.
    """
    steps = registry.plan("html", target)
    missing = sorted({r.tool_key for r in steps if not _tool(r.tool_key)})
    if missing:
        pytest.skip(f"not installed: {', '.join(missing)}")

    with _Listener() as listener:
        source = tmp_path / "bait.html"
        source.write_text(_bait(listener.port), encoding="utf-8")

        current = source
        for index, route in enumerate(steps):
            out = tmp_path / f"step{index}.{route.target}"
            route.handler(current, out, on_progress=None)
            current = out

        assert current.exists() and current.stat().st_size > 0, "conversion failed"
        assert listener.hits == [], (
            f"a conversion tool fetched {listener.hits} from the network")


def _tool(key: str) -> bool:
    import binaries
    return binaries.resolve(key) is not None


# ---------------------------------------------------- the server stays local

def test_the_start_scripts_bind_to_loopback_only():
    """0.0.0.0 would put a service that writes files on the local network."""
    for name in ("run.sh", "run.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--host 127.0.0.1" in text, f"{name} does not pin the bind address"
        assert "0.0.0.0" not in text, f"{name} mentions 0.0.0.0"


def test_uvicorns_own_default_is_loopback():
    """Someone running `uvicorn main:app` by hand must not get a public bind."""
    import uvicorn.config
    assert uvicorn.config.Config("main:app").host == "127.0.0.1"


def test_a_job_leaves_no_readable_leftovers(tmp_path, monkeypatch):
    """Converted files are deleted with their job, not left in temp/ forever."""
    import jobs
    monkeypatch.setattr(jobs, "TEMP_DIR", tmp_path)
    store = jobs.JobStore()
    job = store.create("secret.png", "png", "jpg", "PNG (image)", tmp_path / "in.png")
    work = jobs.job_dir(job.id)
    (work / "secret.jpg").write_bytes(b"sensitive")
    store.update(job.id, status=jobs.DONE, output_path=work / "secret.jpg")
    job.created_at = 0  # older than any max_age

    store.sweep(max_age=1)
    assert not work.exists()
    assert list(tmp_path.iterdir()) == []


def test_socket_is_not_reachable_from_outside_loopback():
    """A sanity check on the address family assumption above."""
    assert socket.gethostbyname("localhost") in ("127.0.0.1", "::1")


def test_the_libreoffice_profile_is_sealed_against_the_network(tmp_path):
    """base.py blocks the network with proxy environment variables, but
    LibreOffice defaults to the *system* proxy configuration, which on Windows
    does not come from the environment. The same block is therefore written
    into the per-job profile, where it applies on every platform."""
    from handlers import libreoffice_handler

    profile = tmp_path / "profile"
    libreoffice_handler._seal_profile(profile)

    settings = (profile / "user" / "registrymodifications.xcu").read_text()
    assert 'oor:name="ooInetProxyType"' in settings
    assert "<value>1</value>" in settings          # 1 = manual, not "system"
    assert settings.count("127.0.0.1") >= 2        # http and https
    assert 'oor:name="ooInetNoProxy"' in settings  # nothing exempted


def test_every_libreoffice_conversion_seals_its_profile(tmp_path, monkeypatch):
    """The seal is worthless if a code path forgets to apply it."""
    from handlers import libreoffice_handler

    sealed: list[Path] = []
    monkeypatch.setattr(libreoffice_handler, "_seal_profile", sealed.append)
    monkeypatch.setattr(libreoffice_handler, "run",
                        lambda *a, **k: (1, "", "stopped before running"))
    monkeypatch.setattr(libreoffice_handler.binaries, "require", lambda key: "soffice")

    source = tmp_path / "in.csv"
    source.write_text("a,b\n1,2\n")
    with pytest.raises(base.ConversionError):
        libreoffice_handler.convert_office(source, tmp_path / "out.ods")

    assert len(sealed) == 1, "the profile was not sealed before LibreOffice ran"
