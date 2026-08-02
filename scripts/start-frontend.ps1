$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue

if ($null -eq $pnpm) {
    throw "pnpm.cmd is not available on PATH. Install pnpm, then follow README.md."
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules"))) {
    throw "Missing frontend dependencies. Run 'pnpm.cmd --dir frontend install' first."
}

& $pnpm.Source --dir $frontendRoot dev
