"""LibreOffice headless: office documents, and office-to-PDF."""
from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

import binaries

from .base import ConversionError, finish, run

# Where the extension alone is ambiguous, name the filter. "csv" matches both
# Writer's plain-text filter and Calc's separated-values one, and letting
# LibreOffice guess means a spreadsheet occasionally comes out as prose.
_FILTERS: dict[str, str] = {
    "csv": "csv:Text - txt - csv (StarCalc)",
}

# LibreOffice fetches remote resources a document points at -- an
# <img src="http://..."> in an HTML file is enough -- which would let a tracking
# pixel in a document report back the moment it was converted. base.py aims the
# proxy environment variables at a closed port, but LibreOffice defaults to the
# *system* proxy configuration, which on Windows comes from the OS rather than
# from the environment. So the same block is written into the per-job profile,
# where it holds on every platform: proxy type 1 is "manual", pointed at a port
# nothing listens on, with no host exempted.
_NO_NETWORK_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetProxyType" oor:op="fuse"><value>1</value></prop></item>
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPProxyName" oor:op="fuse"><value>127.0.0.1</value></prop></item>
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPProxyPort" oor:op="fuse"><value>1</value></prop></item>
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPSProxyName" oor:op="fuse"><value>127.0.0.1</value></prop></item>
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetHTTPSProxyPort" oor:op="fuse"><value>1</value></prop></item>
 <item oor:path="/org.openoffice.Inet/Settings"><prop oor:name="ooInetNoProxy" oor:op="fuse"><value></value></prop></item>
</oor:items>
"""


def _seal_profile(profile: Path) -> None:
    """Write the no-network settings into a fresh LibreOffice user profile."""
    user_dir = profile / "user"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "registrymodifications.xcu").write_text(
        _NO_NETWORK_PROFILE, encoding="utf-8")


def convert_office(src: Path, dst: Path,
                   on_progress: Callable[[float], None] | None = None) -> None:
    binary = binaries.require("libreoffice")
    source_ext = src.suffix.lower().lstrip(".")
    target = dst.suffix.lower().lstrip(".")

    # LibreOffice can be installed a module at a time. Without the right one it
    # exits 0 having written nothing, so the check happens here rather than
    # letting ensure_output report an empty result with no idea why.
    blocked = binaries.missing_module_reason(source_ext)
    if blocked is not None:
        raise FileNotFoundError(blocked)

    # LibreOffice cannot be told what to call its output. It writes
    # <input-stem>.<ext> into --outdir and that is the end of the negotiation,
    # so it converts into a private directory and the result is moved to the
    # path the caller actually asked for.
    work = dst.parent / f".lo_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    profile = work / "profile"
    _seal_profile(profile)

    argv = [
        binary,
        # Concurrent headless runs otherwise contend for the single shared user
        # profile, and the loser silently exits 0 having written nothing. A
        # per-job profile is the only reliable fix.
        f"-env:UserInstallation={profile.absolute().as_uri()}",
        "--headless", "--norestore", "--invisible", "--nolockcheck",
        "--nodefault", "--nofirststartwizard",
        "--convert-to", _FILTERS.get(target, target),
        "--outdir", str(work),
        str(src),
    ]

    try:
        code, out, err = run("libreoffice", argv)

        produced = work / f"{src.stem}.{target}"
        if not produced.exists():
            # Fall back to whatever it did write, so a filter that renames the
            # output is reported as a real failure rather than a missing file.
            candidates = [p for p in work.iterdir()
                          if p.is_file() and p.name != "profile"]
            produced = candidates[0] if candidates else produced

        if produced.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(produced), str(dst))

        finish("LibreOffice", code, out, err, dst)
    except ConversionError:
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
