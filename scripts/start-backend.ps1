$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Follow the setup steps in README.md first."
}

& $python -m uvicorn tend_eval.main:app --app-dir (Join-Path $projectRoot "backend") --host 127.0.0.1 --port 8000 --reload

