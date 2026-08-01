$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
pnpm.cmd --dir (Join-Path $projectRoot "frontend") dev
