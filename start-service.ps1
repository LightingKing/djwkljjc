$ErrorActionPreference = 'Stop'

# Keep runtime data beside this checkout. Nothing is read from the old
# analysis/ljjc-local tree, so a fresh clone starts predictably.
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$runtimeDir = Join-Path $root 'runtime'
$fixtureDir = Join-Path $runtimeDir 'fixtures'
$stateDir = Join-Path $runtimeDir 'state'
$backupDir = Join-Path $runtimeDir 'backups'
$logPath = Join-Path $runtimeDir 'requests.jsonl'
$pidFile = Join-Path $runtimeDir 'server.pid'
$serverFile = Join-Path $root 'server.py'
$configFile = Join-Path $root 'server.toml'

# This convenience launcher deliberately starts HTTP without a certificate.
# Refuse a LAN/any-address configuration here instead of silently exposing
# account tokens and game traffic in plaintext.  LAN testing must invoke
# server.py explicitly with a certificate/key and without --no-tls.
$configText = Get-Content -LiteralPath $configFile -Raw
$bindMatch = [regex]::Match($configText, '(?m)^\s*bind_host\s*=\s*["'']([^"'']+)["'']')
$configuredBindHost = if ($bindMatch.Success) { $bindMatch.Groups[1].Value.Trim() } else { '127.0.0.1' }
$loopbackHosts = @('127.0.0.1', '::1', 'localhost')
if ($loopbackHosts -notcontains $configuredBindHost.ToLowerInvariant()) {
    throw "start-service.ps1 is loopback-only because it uses --no-tls; bind_host='$configuredBindHost' requires an explicit TLS server.py launch."
}

New-Item -ItemType Directory -Force -Path $runtimeDir, $fixtureDir, $stateDir, $backupDir | Out-Null
$bootstrap = Join-Path $fixtureDir 'pr2new_djwk.json'
if (-not (Test-Path -LiteralPath $bootstrap)) {
    '{"version":"local","entries":[]}' | Set-Content -LiteralPath $bootstrap -Encoding UTF8
}

if (Test-Path -LiteralPath $pidFile) {
    $oldPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid"
        if ($oldProcess) {
            if (-not $oldProcess.CommandLine.Contains($serverFile)) {
                throw "PID $oldPid does not belong to this server; leaving it running."
            }
            Stop-Process -Id $oldPid -Force
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

$python = (Get-Command python).Source
$arguments = @($serverFile, '--config', $configFile, '--fixture-root', $fixtureDir,
    '--state-dir', $stateDir, '--unified-store-path', 'local.sqlite3',
    '--backup-dir', $backupDir, '--log', $logPath, '--no-tls')
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'server.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'server.stderr.log')
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 250
    if ($process.HasExited) { throw "Local server exited. See $runtimeDir/server.stderr.log" }
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:19080/__local__/health' -TimeoutSec 2
        if ($health.status -eq 'ok') {
            Write-Output "Local LJJC server PID $($process.Id), http://127.0.0.1:19080"
            return
        }
    } catch {
        if ($attempt -eq 19) { throw }
    }
}
