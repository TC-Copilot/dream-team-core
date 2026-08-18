$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Root 'app\app-lifecycle.ps1')

function Get-FreePort {
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
  $listener.Stop()
  return $port
}

$python = (Get-Command python.exe -ErrorAction Stop).Source
$tempRoot = Join-Path $env:TEMP ('daily-flow-lifecycle-test-' + [guid]::NewGuid().ToString('N'))
$unrelated = $null
try {
  New-Item -ItemType Directory -Path $tempRoot | Out-Null

  # A process can mimic the health payload; without our PID file or exact app.py path it must survive.
  $mockPort = Get-FreePort
  $mockScript = Join-Path $tempRoot 'mock-health.py'
  @'
import http.server
import json
import sys

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "version": "1.2.3"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass

http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'@ | Set-Content -LiteralPath $mockScript -Encoding ASCII
  $unrelated = Start-Process -FilePath $python -ArgumentList ('"{0}" {1}' -f $mockScript, $mockPort) -PassThru -WindowStyle Hidden
  for ($i = 0; $i -lt 30 -and -not (Get-DailyFlowHealth -Port $mockPort); $i++) { Start-Sleep -Milliseconds 100 }
  $refusal = Stop-DailyFlowAppOnPort -Port $mockPort -AppRoot $tempRoot
  if ($refusal.Ok -or $unrelated.HasExited) { throw 'Lifecycle guard stopped or accepted an unrelated Python health server.' }

  Stop-Process -Id $unrelated.Id -Force
  $unrelated = $null
  if (-not (Wait-DailyFlowPortFree -Port $mockPort)) { throw "Mock port $mockPort did not release." }

  # Exercise the shipped launcher and stopper against an isolated app copy.
  $appRoot = Join-Path $tempRoot 'app'
  Copy-Item -LiteralPath (Join-Path $Root 'app') -Destination $appRoot -Recurse
  Copy-Item -LiteralPath (Join-Path $Root 'preflight.ps1') -Destination (Join-Path $appRoot 'preflight.ps1') -Force
  $appPort = Get-FreePort
  @{ port = $appPort; documentRoot = (Join-Path $tempRoot 'documents') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot 'config.json') -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $appRoot '.installed-version') -Value '9.9.9' -Encoding ASCII
  Set-Content -LiteralPath (Join-Path $appRoot '.installed-build-revision') -Value 'test.1' -Encoding ASCII
  $env:DAILY_FLOW_NO_BROWSER = '1'
  & (Join-Path $appRoot 'start-app.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'start-app.ps1 returned a failure exit code.' }
  $health = Get-DailyFlowHealth -Port $appPort -ExpectedVersion '9.9.9' -ExpectedBuildRevision 'test.1'
  if (-not $health) { throw 'Launcher did not expose the expected health version.' }
  if (Get-DailyFlowHealth -Port $appPort -ExpectedVersion '9.9.9' -ExpectedBuildRevision 'wrong') {
    throw 'Lifecycle health accepted the wrong build revision.'
  }
  $stopped = Stop-DailyFlowAppOnPort -Port $appPort -AppRoot $appRoot
  if (-not $stopped.Ok -or -not $stopped.Stopped) { throw "App stop failed: $($stopped.Reason)" }
  if (-not (Wait-DailyFlowPortFree -Port $appPort)) { throw "App port $appPort did not release." }

  Write-Host '[PASS] app lifecycle ownership, version health, start, stop, and port release'
} finally {
  if ($unrelated -and -not $unrelated.HasExited) { Stop-Process -Id $unrelated.Id -Force -ErrorAction SilentlyContinue }
  $appPidFile = Join-Path $tempRoot 'app\daily-flow-app.pid'
  if (Test-Path $appPidFile) {
    $appPid = 0
    if ([int]::TryParse(([string](Get-Content -LiteralPath $appPidFile -Raw)).Trim(), [ref]$appPid)) {
      Stop-Process -Id $appPid -Force -ErrorAction SilentlyContinue
    }
  }
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
