#Requires -Version 5.1
<#
.SYNOPSIS
    One-shot deploy to the production ECS via Alibaba Cloud Workbench CLI.

.DESCRIPTION
    Prerequisites (one time only):
      1) & "C:\Program Files\workbench\workbench.exe" config    # fill in AccessKey
      2) & "C:\Program Files\workbench\workbench.exe" list -o json   # get instance id

    NOTE: `workbench exec` starts an INDEPENDENT shell on every call, so `cd`
    does NOT persist between calls. All steps must be chained with && in ONE
    command. Default exec timeout is 30s; frontend build needs much more,
    hence --timeout 300.

    IMPORTANT: the frontend MUST be built with `npm run build:server`
    (`npm run build` is the GitHub Pages profile: base=/stock-scoring-v2/
    and API pointing at Render -> 404 / CORS on the server).

.EXAMPLE
    .\scripts\deploy_server.ps1 -InstanceId i-xxxxxxxxxxxx
.EXAMPLE
    .\scripts\deploy_server.ps1 -InstanceId i-xxx -DryRun
#>
param(
    [Parameter(Mandatory = $true)][string] $InstanceId,
    [string] $Region = "",
    [int]    $Timeout = 300,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

# PATH only takes effect after re-login, so prefer the absolute path.
$WB = 'C:\Program Files\workbench\workbench.exe'
if (-not (Test-Path $WB)) {
    $c = Get-Command workbench -ErrorAction SilentlyContinue
    if (-not $c) {
        Write-Host 'ERROR: workbench.exe not found (install it first)' -ForegroundColor Red
        exit 1
    }
    $WB = $c.Source
}

$cfg = Join-Path $env:USERPROFILE '.workbench\config.json'
if (-not (Test-Path $cfg)) {
    Write-Host 'ERROR: credentials not configured. Run this first:' -ForegroundColor Red
    Write-Host "   & `"$WB`" config"
    exit 4
}

$regionArgs = @()
if ($Region) { $regionArgs = @('--region', $Region) }

# ★★ The page reads the LOCKED daily snapshot (08:55), not the live endpoint.
#    Restarting the backend alone leaves today's OLD snapshot (English rule ids)
#    in place until tomorrow 08:55 -> the fix would look like "it did not work".
#    So recompute today's snapshot right after the restart.
$refreshSnap = 'cd /www/wwwroot/stock-scoring-v2/backend && ' +
               '.venv/bin/python -c "from app.macro import get_macro_snapshot; ' +
               'from app.flash import store; ' +
               'store.save_macro_daily(get_macro_snapshot()); ' +
               'print(\"snapshot refreshed\")"'

$deploy = 'cd /www/wwwroot/stock-scoring-v2 && git pull && ' +
          'sudo systemctl restart stock-scoring && ' +
          $refreshSnap + ' && ' +
          'cd /www/wwwroot/stock-scoring-v2/frontend && npm run build:server'

if ($DryRun) {
    Write-Host "DRY RUN - would execute on $InstanceId :"
    Write-Host $deploy
    exit 0
}

Write-Host '[1/3] git pull + restart backend + rebuild frontend ...'
& $WB exec --instance-id $InstanceId @regionArgs -c $deploy --timeout $Timeout --output json
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: deploy failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host '[2/3] waiting for /api/health ...'
$ok = $false
for ($i = 1; $i -le 12; $i++) {
    Start-Sleep -Seconds 5
    $h = (& $WB exec --instance-id $InstanceId @regionArgs `
          -c "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health" `
          --timeout 30 --output text 2>$null) -join ''
    if ($h -match '200') { $ok = $true; break }
    Write-Host "   attempt $i ..."
}
if (-not $ok) {
    Write-Host 'ERROR: health check did not pass in 60s' -ForegroundColor Red
    Write-Host "   Inspect: & `"$WB`" exec -i $InstanceId -c `"journalctl -u stock-scoring -n 40 --no-pager`""
    exit 1
}
Write-Host '   health OK'

# NOTE: rules_detail lives in /macro/snapshot (get_macro_snapshot),
#       NOT /market/emotion (that one is the emotion/auction snapshot).
Write-Host '[3/3] verify deployed build (expect Chinese "name" fields) ...'
& $WB exec --instance-id $InstanceId @regionArgs `
    -c 'curl -s http://127.0.0.1:8000/api/macro/snapshot | head -c 600' `
    --timeout 30 --output text

Write-Host ''
Write-Host 'DONE - if the last output contains Chinese "name" fields, the deploy is good.'
Write-Host 'Tip: the page itself uses /macro/daily?date=TODAY (locked snapshot).'
