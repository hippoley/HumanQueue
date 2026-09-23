$ErrorActionPreference = "Stop"

$homeDir = if ($env:HUMAN_QUEUE_HOME) { $env:HUMAN_QUEUE_HOME } else { Join-Path $HOME ".human-queue" }
$venv = Join-Path $homeDir "runtime"

python --version | Out-Null
New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
python -m venv $venv

$pip = Join-Path $venv "Scripts\pip.exe"
$humanq = Join-Path $venv "Scripts\humanq.exe"

& $pip install --upgrade pip
& $pip install "git+https://github.com/hippoley/human-queue.git"

Write-Host ""
Write-Host "Installed human://"
Write-Host "CLI: $humanq"
Write-Host ""
& $humanq onboard
