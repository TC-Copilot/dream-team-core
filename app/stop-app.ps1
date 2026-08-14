# Daily Flow Team - app stopper
# Author: Shervin Shaffie
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root 'app-lifecycle.ps1')
$Port = 8787
$cfgPath = Join-Path $Root 'config.json'
if (Test-Path $cfgPath) {
  try { $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json; if ($cfg.port) { $Port = [int]$cfg.port } } catch {}
}
$result = Stop-DailyFlowAppOnPort -Port $Port -AppRoot $Root
if (-not $result.Ok) { Write-Error $result.Reason; exit 1 }
Write-Host $result.Reason