# Local File Converter

Drag a file into a browser tab, get it back in another format. Everything runs
on `localhost`, and nothing you drop on it is uploaded anywhere.

This is a **router, not a converter**. It works out what a file actually is,
picks the right tool for the job, runs it, and hands back the result. The
conversion work itself is done by FFmpeg, ImageMagick, Pandoc, LibreOffice and
Calibre — battle-tested tools that already do this better than any hand-rolled
code would.

**192 conversion routes** across images, video, audio, documents, spreadsheets,
slides and ebooks.

---

## Contents

- [Quick start](#quick-start)
- [Installing the conversion tools](#installing-the-conversion-tools)
- [Supported conversions](#supported-conversions)
- [Configuration](#configuration)
- [API](#api)
- [How it works](#how-it-works)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Quick start

```bash
git clone https://github.com/0xphoenixlabs/Local-File-Converter.git
cd Local-File-Converter
```

**Windows** — from Command Prompt, or by double-clicking it:

```bat
run.bat
```

From PowerShell, either that or the script it wraps:

```powershell
.\run.ps1
```

`run.bat` exists because Command Prompt cannot run a `.ps1` file: typing
`.\run.ps1` there does nothing at all, with no error. It also sidesteps the
execution policy that blocks scripts which arrived inside a downloaded ZIP.

**macOS / Linux**

```bash
./run.sh
```

Any of these creates a virtualenv, installs dependencies, checks that the app
loads, and starts the server. Open <http://127.0.0.1:8000> and drop a file on
the page.

By hand, if you prefer. **Windows:**

```bat
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn main:app --port 8000
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --port 8000
```

Requires **Python 3.11+**. On Windows use `py`, not `python`: a stock Windows
has a placeholder `python.exe` that only prints "Python was not found" and
offers to open the Microsoft Store. The start scripts skip it automatically.

## Installing the conversion tools

None of these are bundled. The app checks for them at startup and at
`GET /health`, and the page shows a banner naming anything missing. Nothing
crashes when a tool is absent — conversions that need it are refused with an
explanation and an install command, and everything else keeps working. Install
only the ones you need.

| Tool | Handles | Windows | macOS | Debian / Ubuntu |
|---|---|---|---|---|
| ImageMagick | images | `winget install ImageMagick.ImageMagick` | `brew install imagemagick` | `sudo apt install imagemagick` |
| FFmpeg | video, audio | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| Pandoc | markup | `winget install JohnMacFarlane.Pandoc` | `brew install pandoc` | `sudo apt install pandoc` |
| LibreOffice | office documents | `winget install TheDocumentFoundation.LibreOffice` | `brew install --cask libreoffice` | `sudo apt install libreoffice` |
| Calibre | ebooks | `winget install calibre.calibre` | `brew install --cask calibre` | `sudo apt install calibre` |

Tools that install outside `PATH` are still found: versioned ImageMagick
directories on Windows, and the `.app` bundles LibreOffice and Calibre use on
macOS.

## Supported conversions

`GET /supported` returns the authoritative matrix. This is the shape of it:

| Family | Sources | Targets |
|---|---|---|
| Images | png, jpg, webp, bmp, tiff, gif, avif, heic\*, svg\*, ico | png, jpg, webp, bmp, tiff, gif, avif, ico, pdf |
| Video | mp4, mov, webm, avi, mkv | mp4, mov, webm, mkv, gif, plus any audio target |
| Audio | mp3, wav, flac, ogg, m4a | mp3, wav, flac, ogg |
| Markup | md, html, rst, txt, docx, epub | md, html, rst, txt, docx, epub |
| Office | docx, odt, rtf, xlsx, ods, csv, pptx, odp | the rest of their own family, plus pdf |
| Ebooks | epub, mobi, azw3, fb2 | epub, mobi, azw3, fb2, pdf |

\* input only.

Pairs with no single tool behind them are still reachable through one
intermediate format — Markdown to PDF, EPUB to ODT, a video frame to PNG. The
format picker labels those as **two-step**, so it is always visible when a
conversion is passing through something on the way.

## Configuration

All optional, all environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | `500` | Uploads above this are rejected before any process starts |
| `TEMP_MAX_AGE_SECONDS` | `3600` | How long finished work is kept in `temp/` |
| `TIMEOUT_IMAGEMAGICK` | `120` | Per-tool subprocess timeout, in seconds |
| `TIMEOUT_FFMPEG` | `600` | |
| `TIMEOUT_PANDOC` | `180` | |
| `TIMEOUT_LIBREOFFICE` | `300` | |
| `TIMEOUT_CALIBRE` | `300` | |

Port is a script argument: `.\run.ps1 -Port 9000` or `./run.sh 9000`.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Which tools are installed, which are missing, and the upload cap |
| `GET /supported` | The full conversion matrix, split into direct and two-step routes |
| `POST /convert` | Multipart `file`, optional `target_format`, optional `upload_token` |
| `GET /status/{job_id}` | `queued` / `running` / `done` / `failed`, plus progress and error text |
| `GET /download/{job_id}` | The converted file, once the job is `done` |

`POST /convert` works two ways. Send a file and a target format together and it
starts a job. Send a file with no target and it only identifies the file,
reporting what it can become plus an `upload_token` — the bytes stay on the
server, so the follow-up request sends the token instead of uploading again.

```bash
# one shot
curl -F "file=@photo.png" -F "target_format=webp" http://127.0.0.1:8000/convert
curl http://127.0.0.1:8000/status/<job_id>
curl -o out.webp http://127.0.0.1:8000/download/<job_id>

# two steps: ask what it is first, then convert without resending the bytes
curl -F "file=@photo.png" http://127.0.0.1:8000/convert          # -> upload_token
curl -F "upload_token=<token>" -F "target_format=webp" http://127.0.0.1:8000/convert
```

A token is claimed once and swept with everything else after an hour. If it has
gone, the endpoint answers `409` with `reason: expired_token`, and the web UI
quietly re-sends the file rather than showing an error.

## How it works

```
Browser (drag & drop)
   |
   |  POST /convert
   v
FastAPI
   |-- 1. stream the upload to temp/, enforcing the size cap
   |-- 2. identify the file from its content
   |-- 3. look up (source, target) in the registry
   |-- 4. if no direct route exists, find a two-step chain
   |-- 5. run it in a background task -> subprocess
   |-- 6. frontend polls /status/{job_id}
   v
Download link appears when the job is done
```

### Identifying files

`detect.py` reads the leading bytes and identifies a file from its actual
content. The extension is only a fallback for formats that have no signature at
all (Markdown, CSV, plain text). Renaming `notes.txt` to `movie.mp4` does not
fool it, and the UI says so rather than handing a text file to FFmpeg.

Formats that share a magic number are separated properly: `RIFF` splits into
WAV / WEBP / AVI, an ISO-BMFF `ftyp` box into MP4 / MOV / M4A / HEIC / AVIF,
EBML into MKV / WEBM, and a ZIP is opened to tell DOCX from XLSX from PPTX from
EPUB from ODT.

Weak signatures get validated rather than trusted, because a two-byte match is
not evidence:

- **BMP** is more than `BM` — the header must also declare the file's real
  length, keep its four reserved bytes zero, and point at a sane pixel offset.
  Matching `BM` alone sends any note beginning "BMW…" to ImageMagick.
- **MP3 without an ID3 tag** is more than the 11 sync bits — the version,
  layer, bitrate and sample-rate fields must all hold values a real frame can
  use. The sync bits alone are satisfied by a UTF-16 byte-order mark.
- **SVG** is decided by the document's root element, not by finding `<svg`
  somewhere in it, so an HTML page with an inline chart stays HTML.
- Byte-order marks are recognised up front, so UTF-16 and UTF-8-BOM text is
  read as text instead of being claimed by a binary heuristic.

`python-magic` is supported but optional, and deliberately not the primary
detector. It binds to libmagic, and on Windows without a libmagic DLL
`import magic` does not raise `ImportError` — it aborts the interpreter at the
DLL loader, which no `try`/`except` can catch, taking the server down at
startup. So the signature table is built in, and libmagic is consulted only
after a subprocess probe proves the import is safe. Install `python-magic`
alongside a real libmagic and it is picked up automatically as a fallback.

### Routing

`registry.py` holds a declarative table of `(source, target) -> handler`.
Adding a format pair means adding it to a list; no routing code changes.

- **No silent duplicates.** If two tools claimed the same pair, which one ran
  would depend on table order. Building the route map raises instead.
- **Chaining, capped at two hops.** Markdown to PDF goes `md → docx` (Pandoc)
  then `docx → pdf` (LibreOffice). Where several intermediates would work, the
  router prefers the one that loses least — PNG or TIFF over JPEG for images,
  WAV or FLAC over MP3 for audio. Anything needing three hops is refused with a
  clear message rather than quietly producing something degraded.

### Running the tools

Every handler in `handlers/` follows the same contract:

- Arguments are built as a list. `shell=True` appears nowhere in the codebase.
- The output path is always explicit.
- stdout and stderr are captured; a non-zero exit becomes a `ConversionError`
  with a one-line summary for the UI and the full output behind a details
  toggle.
- Every subprocess call has a timeout, tuned per tool — a video transcode is
  legitimately slower than a PNG resize.
- A zero exit code is not taken as proof of success: the output file must exist
  and be non-empty. Several of these tools will happily report success and
  write nothing.

Children are tied to the server's lifetime. A clean shutdown kills whatever is
still running. For the unclean case, every child goes into a Windows job object
created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`process_group.py`): the
job's only handle belongs to the server, so when that process dies — including
under `taskkill /F`, where no application code runs at all — the kernel kills
everything inside. On POSIX children get their own process group, which covers
clean shutdown but cannot survive a `kill -9` of the parent.

Two tool-specific details worth knowing:

- **LibreOffice** cannot be told what to name its output; it writes
  `<input-stem>.<ext>` into `--outdir`. The handler converts into a private
  directory and moves the result to the exact requested path. It also passes
  `-env:UserInstallation=<per-job profile>`, because concurrent headless
  invocations otherwise collide over the shared user profile and one of them
  silently produces nothing.
- **ImageMagick 7** is invoked as `magick in out`. The v6 compatibility shim
  (`magick convert`) still works but prints a deprecation warning to stderr on
  every run, which then gets mistaken for the error message when something
  actually fails.

### A Windows trap

Windows ships its own `convert.exe` in `System32` — it converts FAT volumes to
NTFS and has nothing to do with ImageMagick. Resolving ImageMagick by the name
`convert`, which is the usual advice on Linux, finds that instead.
`binaries.py` prefers `magick` and explicitly refuses any `convert` resolving
inside `System32`. On other platforms `convert` is still accepted as the
ImageMagick v6 name.

### Jobs and temp files

Jobs live in an in-memory dict behind a lock — right-sized for a single-user
local tool, and deliberately not durable. Each job gets its own directory under
`temp/`, so concurrent jobs cannot collide over intermediate filenames.

Because files outlive the process and the job store does not, `temp/` is swept
on startup as well as every ten minutes; anything older than an hour goes.
Restarting mid-job is safe: the job is gone, its files are cleaned up on the way
back in, and nothing is left half-written.

## Development

```bash
pip install -r requirements-dev.txt
pytest              # the default suite
pytest -m matrix    # every route, for real -- minutes, needs all five tools
ruff check .        # lint
```

The tests need no external tools — anything that shells out to one is skipped
automatically when that tool is missing, so the suite is meaningful on a bare
checkout and more thorough on a fully-equipped machine. Sample files are
generated at runtime rather than committed.

```
├── main.py                  FastAPI app and routes
├── registry.py              conversion table, chaining, route lookup
├── detect.py                content-based file type detection
├── binaries.py              locating the five external tools
├── process_group.py         tying child processes to the server's lifetime
├── jobs.py                  job store and temp-file lifecycle
├── handlers/
│   ├── base.py              shared subprocess rules
│   ├── imagemagick_handler.py
│   ├── ffmpeg_handler.py
│   ├── pandoc_handler.py
│   ├── libreoffice_handler.py
│   └── calibre_handler.py
├── static/                  index.html, app.js, style.css
├── tests/
│   ├── conftest.py          fixtures, generated at runtime
│   ├── test_detect.py       signatures, and the weak ones in particular
│   ├── test_registry.py     routes, duplicates, chain planning
│   ├── test_handlers.py     the subprocess contract
│   ├── test_process_group.py  children dying with the server
│   ├── test_conversions.py  real conversions, skipped when a tool is absent
│   ├── test_api.py          the HTTP surface
│   └── test_matrix.py       every route (opt in with `-m matrix`)
├── .github/workflows/ci.yml
└── temp/                    uploads and outputs, swept hourly
```

### Adding a conversion

Add the pair to the relevant list in `registry.py`. If an existing handler
already covers the tool, that is the whole change:

```python
RASTER = ["png", "jpg", "webp", "bmp", "tiff", "gif", "avif"]
```

A new tool needs a module in `handlers/` following the contract above, plus an
entry in `binaries.TOOLS` so `/health` can report on it.

## Troubleshooting

**The start script printed a link, but the browser will not open it.** The
server exited immediately after the script printed the URL. Run the script from
a terminal rather than by double-clicking it, so the window stays open and the
error above the link is readable.

The usual cause is an incomplete copy of the project. A ZIP downloaded from a
branch that has no `handlers/` or `static/` directory still looks complete,
because every top-level file is there, and the app then fails to import. Both
start scripts check for this before anything else and name the missing folder.
`git clone` gets you the whole repository; GitHub's "Download ZIP" button gives
you whichever branch you happen to be looking at, which is not necessarily the
one you want.

**A conversion fails with "not installed."** That tool is missing. The message
names it and gives the install command; `/health` lists everything at once.

**`did not find executable at '...\python.exe'`** when running anything in the
virtualenv. A venv records an absolute path to the Python that created it, so
moving or reinstalling that interpreter breaks it. Delete `.venv` and re-run the
start script, or point `.venv/pyvenv.cfg` at the new location.

**`Could not find platform independent libraries <prefix>`** before every
command. The interpreter cannot find its own standard library at its install
prefix — usually because `python.exe` was moved without the `Lib` folder beside
it. Python may still run (it can locate the stdlib through the Windows
registry) but `sys.prefix` is wrong, and any venv created from it will be too.
Repair or reinstall Python so `Lib\`, `DLLs\` and `python.exe` sit in the same
directory.

**A conversion produced the wrong format, or refuses a format the table
lists.** ImageMagick reads and writes most formats through optional delegate
libraries, and which ones are compiled in is decided by whoever packaged your
build. Debian and Ubuntu's ImageMagick 6, for example, ships AVIF and HEIC as
read-only. Asked to write a format it has no encoder for, ImageMagick prints a
*warning*, exits 0, and writes the image in some other format -- so you get a
valid, non-empty file that is not what you asked for. The handler treats that
warning as a failure rather than handing back a mislabelled file. Run
`magick -list format` (or `convert -list format` on v6) to see what your build
can actually write; the `rw-` column is the one that matters.

**PDF only converts one way.** Converting *to* PDF works from images, office
documents and ebooks. Converting *from* PDF is not offered: rasterising a PDF
needs Ghostscript, which this app does not assume you have.

**Progress sits at an indeterminate bar.** Only FFmpeg reports real progress,
read from `ffmpeg -progress` against the duration `ffprobe` returns. The other
four tools expose nothing comparable, so their jobs show stage transitions
rather than a number that would be invented. The same applies to a video whose
duration cannot be read — a stream-recorded WEBM often carries no duration in
its header.

## Contributing

Issues and pull requests are welcome. Please run `pytest` and `ruff check .`
before opening a PR; CI runs both on Linux and Windows across Python 3.11–3.13.

If you are adding a format, a test that proves the output is really that format
(magic bytes, not just a non-empty file) is worth more than one that only checks
the job succeeded.

## License

MIT — see [LICENSE](LICENSE).
