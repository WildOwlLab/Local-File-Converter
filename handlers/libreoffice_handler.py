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


def convert_office(src: Path, dst: Path,
                   on_progress: Callable[[float], None] | None = None) -> None:
    binary = binaries.require("libreoffice")
    target = dst.suffix.lower().lstrip(".")

    # LibreOffice cannot be told what to call its output. It writes
    # <input-stem>.<ext> into --outdir and that is the end of the negotiation,
    # so it converts into a private directory and the result is moved to the
    # path the caller actually asked for.
    work = dst.parent / f".lo_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    profile = work / "profile"

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
