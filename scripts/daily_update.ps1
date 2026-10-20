# Daily law update, run by Windows Task Scheduler on a Taiwan network
# (GitHub Actions runners cannot reach sendlaw.moj.gov.tw / law.moj.gov.tw).
# Runs in a dedicated clone so it never touches in-progress work in the dev checkout.

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$log  = Join-Path $repo 'daily_update.log'
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }
Start-Transcript -Path $log -Append | Out-Null

$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Task Scheduler may start with a stale environment; read mail settings straight from the user profile.
foreach ($name in 'SMTP_USER', 'SMTP_PASS', 'NOTIFY_EMAIL', 'GEMINI_API_KEY') {
    $val = [Environment]::GetEnvironmentVariable($name, 'User')
    if ($val) { Set-Item -Path "env:$name" -Value $val }
}

Set-Location $repo
$ok = $false
for ($attempt = 1; $attempt -le 2 -and -not $ok; $attempt++) {
    Write-Output "=== attempt $attempt  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
    git fetch origin
    if ($LASTEXITCODE -ne 0) { Write-Output 'git fetch failed'; continue }
    git reset --hard origin/main

    py osh_dashboard.py --fetch-xml -o docs/index.html
    if ($LASTEXITCODE -ne 0) { Write-Output "generator failed (exit $LASTEXITCODE)"; continue }

    git add docs/index.html docs/history osh_xml_records.json osh_law_diffs.json osh_law_snapshots.json
    git diff --staged --quiet
    if ($LASTEXITCODE -eq 0) { Write-Output 'no changes'; $ok = $true; break }

    # Fresh clone has no user identity; pass it per-command instead of editing git config.
    git -c user.name=JeffLein -c user.email=jeff.lein@upyoung.com.tw commit -m "auto(local): law update $(Get-Date -Format 'yyyy-MM-dd')"
    if ($LASTEXITCODE -ne 0) { Write-Output "git commit failed (exit $LASTEXITCODE)"; continue }
    git push
    if ($LASTEXITCODE -eq 0) { $ok = $true } else { Write-Output 'push rejected, retrying from fresh origin/main' }
}

Write-Output "=== done ok=$ok  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Stop-Transcript | Out-Null
if (-not $ok) { exit 1 }
