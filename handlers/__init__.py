"""One module per external tool, all following the contract in base.py.

Adding a tool means a module here plus an entry in binaries.TOOLS, so that
/health can report on it and conversions can be refused with an install hint
rather than a traceback when it is absent.
"""
from . import (
    base,
    calibre_handler,
    ffmpeg_handler,
    imagemagick_handler,
    libreoffice_handler,
    pandoc_handler,
)

__all__ = [
    "base",
    "calibre_handler",
    "ffmpeg_handler",
    "imagemagick_handler",
    "libreoffice_handler",
    "pandoc_handler",
]
