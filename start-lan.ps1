[CmdletBinding()]
param(
    [string]$Root = "",
    [Parameter(Mandatory = $true)]
    [string]$BindHost,
    [Parameter(Mandatory = $true)]
    [string]$Cert,
    [Parameter(Mandatory = $true)]
    [string]$Key,
    [int]$TlsPort = 19102
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$loopbackHosts = @('127.0.0.1', '::1', 'localhost')
if ($loopbackHosts -contains $BindHost.ToLowerInvariant()) {
    throw "start-lan.ps1 requires a non-loopback BindHost; use start-service.ps1 for loopback HTTP."
}

$certPath = (Resolve-Path -LiteralPath $Cert -ErrorAction Stop).Path
$keyPath = (Resolve-Path -LiteralPath $Key -ErrorAction Stop).Path
$runtimeDir = Join-Path $rootPath 'runtime'
$fixtureDir = Join-Path $runtimeDir 'fixtures'
$stateDir = Join-Path $runtimeDir 'state'
$backupDir = Join-Path $runtimeDir 'backups'
$logPath = Join-Path $runtimeDir 'lan-requests.jsonl'
$pidFile = Join-Path $runtimeDir 'lan-server.pid'
$sourceConfig = Join-Path $rootPath 'server.toml'
$lanConfig = Join-Path $runtimeDir 'server-lan.toml'
$serverFile = Join-Path $rootPath 'server.py'

New-Item -ItemType Directory -Force -Path $runtimeDir, $fixtureDir, $stateDir, $backupDir | Out-Null
$bootstrap = Join-Path $fixtureDir 'pr2new_djwk.json'
if (-not (Test-Path -LiteralPath $bootstrap)) {
    '{"version":"local","entries":[]}' | Set-Content -LiteralPath $bootstrap -Encoding UTF8
}

# Keep server.toml immutable.  The generated runtime config changes only the
# bind address, HTTPS public URL, and TLS port; all account/storage settings
# remain under the reviewed source configuration.
$configText = Get-Content -LiteralPath $sourceConfig -Raw
$configText = [regex]::Replace($configText, '(?m)^\s*bind_host\s*=\s*["''][^"'']*["'']\s*$', "bind_host = `"$BindHost`"")
$configText = [regex]::Replace($configText, '(?m)^\s*public_base_url\s*=\s*["''][^"'']*["'']\s*$', "public_base_url = `"https://$BindHost`:$TlsPort`"")
$configText = [regex]::Replace($configText, '(?m)^\s*tls_port\s*=\s*\d+\s*$', "tls_port = $TlsPort")
$configText = [regex]::Replace($configText, '(?m)^\s*enable_tls\s*=\s*(true|false)\s*$', 'enable_tls = true')
$configText = [regex]::Replace($configText, '(?m)^\s*legacy_tls\s*=\s*(true|false)\s*$', 'legacy_tls = false')
$configText = [regex]::Replace($configText, '(?m)^\s*backup_dir\s*=\s*["''][^"'']*["'']\s*$', 'backup_dir = "backups"')
# Windows PowerShell 5.1's UTF8 encoding includes a BOM by default.  Write
# this generated TOML explicitly as BOM-free UTF-8 for Python 3.11+ TOML.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($lanConfig, $configText, $utf8NoBom)

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
$arguments = @(
    $serverFile, '--config', $lanConfig, '--fixture-root', $fixtureDir,
    '--state-dir', $stateDir, '--unified-store-path', (Join-Path $stateDir 'local.sqlite3'),
    '--backup-dir', $backupDir, '--log', $logPath,
    '--cert', $certPath, '--key', $keyPath, '--tls-only'
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $rootPath -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'lan-server.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'lan-server.stderr.log')
$process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii

# A self-signed development certificate is expected.  The probe deliberately
# skips certificate verification, while the game client must separately trust
# the chosen certificate/CA before using this endpoint.
[Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$probeHost = if (@('0.0.0.0', '::') -contains $BindHost) { '127.0.0.1' } else { $BindHost }
$healthUri = "https://$probeHost`:$TlsPort/__local__/health"
$env:LJJC_HEALTH_URL = $healthUri
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 250
    if ($process.HasExited) { throw "LAN server exited. See $runtimeDir/lan-server.stderr.log" }
    try {
        # Use the bundled Python runtime for the local probe.  Windows
        # PowerShell 5.1 may negotiate an obsolete TLS version even after
        # SecurityProtocol is set, while this probe uses the same Python TLS
        # stack as the server.  Certificate verification is intentionally
        # skipped only for this local readiness check.
        $probeOutput = & $python -c "import json,os,ssl,urllib.request; print(urllib.request.urlopen(os.environ['LJJC_HEALTH_URL'], context=ssl._create_unverified_context(), timeout=2).read().decode('utf-8'))" 2>$null
        $health = $probeOutput | ConvertFrom-Json
        if ($health.status -eq 'ok') {
            Remove-Item Env:LJJC_HEALTH_URL -ErrorAction SilentlyContinue
            Write-Output "Local LJJC TLS-only server PID $($process.Id), $healthUri"
            return
        }
    } catch {
        if ($attempt -eq 19) { throw }
    }
}
Remove-Item Env:LJJC_HEALTH_URL -ErrorAction SilentlyContinue
throw "LAN server did not become healthy. See $runtimeDir/lan-server.stderr.log"
