# Start the converter on http://127.0.0.1:8000
# Usage:  .\run.ps1  [-Port 8000]
param([int]$Port = 8000)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Fail([string]$Message) {
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    # A script started by double-clicking closes its window the moment it
    # exits, taking the reason with it. Hold it open so the error is readable.
    # UserInteractive is still true under -NonInteractive, where Read-Host
    # throws, so the pause is attempted rather than predicted: a CI run must
    # not end with a prompt error stacked on top of the real message.
    try {
        if ([Environment]::UserInteractive) { Read-Host "Press Enter to close" | Out-Null }
    } catch {
        # No console to pause on. The message above has already been printed.
    }
    exit 1
}

# A ZIP downloaded from a branch that never contained these directories looks
# like a complete project: every top-level file is present. Without this check
# the first sign of trouble is a URL that does not open, because the script
# used to print the link before the server had loaded anything.
foreach ($dir in @("handlers", "static")) {
    if (-not (Test-Path (Join-Path $root $dir))) {
        Fail ("this copy of the project is incomplete: the '$dir' folder is missing.`n" +
              "A ZIP downloaded from a branch without it looks exactly like this.`n" +
              "Clone the repository, or download the ZIP of a branch that includes '$dir'.")
    }
}

# Windows ships a placeholder python.exe in WindowsApps that is not Python: run
# it and it prints "Python was not found" and offers to open the Microsoft
# Store. It sits ahead of a real install on PATH often enough that plain
# "python" is not a safe way to find an interpreter.
function Find-Python {
    foreach ($name in @("py", "python", "python3")) {
        $found = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
                 Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' } |
                 Select-Object -First 1
        if ($found) { return $found.Source }
    }
    return $null
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $bootstrap = Find-Python
    if (-not $bootstrap) {
        Fail ("no usable Python was found.`n`n" +
              "'python' here resolves to the Microsoft Store placeholder, which is not`n" +
              "an interpreter. Install Python 3.11 or newer from`n" +
              "  https://www.python.org/downloads/`n" +
              "ticking 'Add python.exe to PATH', or turn the alias off under`n" +
              "  Settings > Apps > Advanced app settings > App execution aliases.")
    }
    Write-Host "Creating virtual environment using $bootstrap ..."
    & $bootstrap -m venv .venv
    if (-not (Test-Path $python)) {
        Fail ("the virtual environment was created but contains no python.exe.`n" +
              "On this machine that usually means the Python install is split across`n" +
              "two folders. See the Troubleshooting section of README.md.")
    }
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r requirements.txt
}

# Load the app before advertising a URL. A link printed for a server that then
# dies on import is how a one-line error becomes "the page won't open".
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$problem = (& $python -c "import main" 2>&1 | Out-String)
$code = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($code -ne 0) {
    Fail ("the app failed to start. Python said:`n`n$problem")
}

Write-Host "Converter starting on http://127.0.0.1:$Port"
& $python -m uvicorn main:app --host 127.0.0.1 --port $Port
