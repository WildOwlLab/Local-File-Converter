# Security

## Reporting a problem

Open an issue. If it is something that should not be public until it is fixed,
say so in the issue without the details and a private channel can be arranged.

## What this app is

A web server on `127.0.0.1` that accepts a file, hands it to a conversion tool
installed on the same machine, and hands back the result. It has no accounts, no
authentication, and no network access of its own. It is designed for one person
running it on their own computer.

## The threat model it is built for

- **Files stay on the machine.** No telemetry, no analytics, no update check, no
  external resources in the web page. Conversion subprocesses are run with every
  proxy variable pointed at a closed port, because Pandoc and LibreOffice will
  otherwise fetch remote resources that an input file references. Verified by
  `tests/test_privacy.py`, which converts a document containing a tracking pixel
  aimed at a local listener and fails if anything requests it.
- **Untrusted input.** Files are identified by content, never by extension, so a
  `.txt` renamed `.png` is not handed to an image tool. Arguments are always
  built as a list; `shell=True` appears nowhere. Filenames are stripped of path
  separators and Windows device names before they reach the filesystem.
- **Runaway or hostile conversions.** Every subprocess has a timeout. Children
  are bound to the server's lifetime -- a Windows job object created with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, process groups on POSIX -- so a
  force-killed server does not leave a transcode running. Uploads are capped
  (`MAX_UPLOAD_MB`, default 500) and streamed to disk, so the cap applies before
  a large file is buffered into memory.

## What it does not defend against

- **The conversion tools themselves.** FFmpeg, ImageMagick, Pandoc, LibreOffice
  and Calibre are large third-party programs that parse hostile input formats.
  A vulnerability in one of them is reachable through this app. Keep them
  updated. The proxy backstop is not a sandbox and will not stop a program that
  opens a socket directly.
- **Exposing it deliberately.** Binding to `0.0.0.0`, or putting it behind a
  tunnel or reverse proxy, hands anyone who can reach it the ability to upload
  files and run these tools on your machine. There is no authentication to stop
  them. The server prints a warning at startup if it detects a non-loopback
  bind, but do not rely on that as a control.
- **Other users of the same machine.** Uploads and results are written under
  `temp/` while a job runs and for an hour after it finishes. Anyone who can
  read that directory can read them.
