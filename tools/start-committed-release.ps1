[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CodeDirectory,
    [switch]$CheckOnly,
    [switch]$PlanOnly,
    [string]$ConfigFile,
    [string]$BackupDirectory,
    [string]$CandidateDirectory = 'I:/workspace/mobilegame/runtime/remediation-20261031/candidate'
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'PowerShell 7 (pwsh) is required. Run this script with pwsh; Windows PowerShell 5.1 is unsupported.'
}
Set-StrictMode -Version Latest

$releaseRoot = 'I:/workspace/mobilegame/runtime/remediation-20261031'
$codeDirectory = (Resolve-Path -LiteralPath $CodeDirectory).Path
$codeCommit = (& git -C $codeDirectory rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'CodeDirectory must be a committed Git checkout.' }
$dirty = @(& git -C $codeDirectory status --porcelain)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) { throw 'Refusing release from a dirty checkout.' }
$pythonExecutable = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe'
$releaseTools = Join-Path $releaseRoot 'release-tools/release_tools.py'
if ($PlanOnly -and $CheckOnly) { throw 'Use either -PlanOnly or -CheckOnly.' }
if (-not $ConfigFile) {
    # The old launcher did not load server.toml. Preserve its network defaults
    # explicitly; do not import unrelated storage/backup/clock overrides from
    # the repository's general-purpose configuration into the live service.
    $ConfigFile = Join-Path $codeDirectory 'tools/config/release-loopback.toml'
}
$configPath = (Resolve-Path -LiteralPath $ConfigFile).Path
$validateNetwork = @'
import ipaddress, json, sys, tomllib
from urllib.parse import urlsplit
with open(sys.argv[1], "rb") as stream:
    document = tomllib.loads(stream.read().decode("utf-8-sig"))
allowed = {"bind_host", "public_base_url", "http_port", "tls_port", "enable_tls", "legacy_tls"}
config = document.get("server", {})
if set(document) != {"server"} or not isinstance(config, dict) or set(config) - allowed:
    raise SystemExit("Release configuration must contain only [server] network keys; storage, backup and clock overrides are forbidden")
config = {"bind_host": "127.0.0.1", "public_base_url": "http://127.0.0.1:19080",
          "http_port": 19080, "tls_port": 19102, "enable_tls": True, "legacy_tls": True, **config}
if config["http_port"] != 19080 or config["tls_port"] != 19102 or config["enable_tls"] is not True:
    raise SystemExit("Committed releases retain HTTP 19080 and TLS 19102; isolated ports use a separate server.py invocation")
if type(config["legacy_tls"]) is not bool:
    raise SystemExit("legacy_tls must be a boolean")
def local(host, *, advertised=False):
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return (address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified) and (not advertised or not address.is_unspecified)
    except ValueError:
        return False
target = urlsplit(config["public_base_url"])
if not local(config["bind_host"]) or not local(target.hostname or "", advertised=True):
    raise SystemExit("Release bind/public URL must use a local or LAN address, with a concrete advertised host")
expected_port = {"http": 19080, "https": 19102}.get(target.scheme)
if (expected_port is None or target.port != expected_port or target.username or target.password
        or target.query or target.fragment or target.path not in ("", "/")):
    raise SystemExit("public_base_url must be the HTTP 19080 or HTTPS 19102 origin, without credentials, path, query or fragment")
print(json.dumps(config))
'@
$networkJson = & $pythonExecutable -B -c $validateNetwork $configPath
if ($LASTEXITCODE -ne 0) { throw 'Release network configuration validation failed.' }
$network = $networkJson | ConvertFrom-Json
$configSha256 = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
$serverArguments = @(
    (Join-Path $codeDirectory 'server.py'),
    '--config', $configPath,
    '--fixture-root', 'I:/workspace/mobilegame/analysis/ljjc-local/fixtures',
    '--state-dir', 'I:/workspace/mobilegame/analysis/ljjc-local/state',
    '--unified-store-path', 'I:/workspace/mobilegame/analysis/ljjc-local/state/local.sqlite3',
    '--log', 'I:/workspace/mobilegame/runtime/force-patch-requests.jsonl',
    '--cert', 'I:/workspace/mobilegame/analysis/ljjc-local/certs/server.pem',
    '--key', 'I:/workspace/mobilegame/analysis/ljjc-local/certs/server-key.pem',
    '--http-port', '19080', '--tls-port', '19102',
    '--force-patch', 'I:/workspace/mobilegame/runtime/force-patch.json'
)
if ($network.legacy_tls) {
    $serverArguments += '--legacy-tls'
}

$plan = [ordered]@{
    executable = $pythonExecutable
    codeCommit = $codeCommit
    workingDirectory = $codeDirectory
    arguments = $serverArguments
    configFile = $configPath
    configSha256 = $configSha256
    network = $network
    planOnly = [bool]$PlanOnly
    checkOnly = [bool]$CheckOnly
}
if ($PlanOnly) {
    # Pure parameter review: no server, production files, artifact staging,
    # port lookup, backups, or runtime records are touched.
    $plan | ConvertTo-Json -Depth 6
    return
}
foreach ($required in @($pythonExecutable, $serverArguments[0], $releaseTools,
        'I:/workspace/mobilegame/analysis/ljjc-local/state/local.sqlite3',
        'I:/workspace/mobilegame/analysis/ljjc-local/certs/server.pem',
        'I:/workspace/mobilegame/analysis/ljjc-local/certs/server-key.pem')) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required file missing: $required" }
}
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(19080, 19102) })
$plan['occupiedPorts'] = @($listeners | Select-Object LocalAddress, LocalPort, OwningProcess)
if ($CheckOnly) {
    & $pythonExecutable $releaseTools --code-directory $codeDirectory --candidate-directory $CandidateDirectory verify-artifacts
    if ($LASTEXITCODE -ne 0) { throw 'Candidate artifact verification failed.' }
    $plan | ConvertTo-Json -Depth 6
    return
}
if ($listeners.Count -gt 0) {
    throw ('Refusing to start: port 19080 or 19102 already has a listener: ' +
        (($listeners | ForEach-Object { "$($_.LocalPort)/PID=$($_.OwningProcess)" }) -join ', '))
}
if (-not $BackupDirectory) { throw 'Provide -BackupDirectory from release_tools.py backup-state.' }
& $pythonExecutable $releaseTools --code-directory $codeDirectory --candidate-directory $CandidateDirectory verify-backup $BackupDirectory
if ($LASTEXITCODE -ne 0) { throw 'Final state backup verification failed.' }
& $pythonExecutable $releaseTools --code-directory $codeDirectory --candidate-directory $CandidateDirectory verify-artifacts --staged
if ($LASTEXITCODE -ne 0) { throw 'Stage the verified candidate ZIP and manifest before starting.' }

# Fixed arguments have no quotes or trailing backslashes. Quote every argument
# because Start-Process joins ArgumentList into a Windows command line.
foreach ($argument in $serverArguments) {
    if ($argument.Contains('"') -or $argument.EndsWith('\')) { throw 'Unsupported argument quoting.' }
}
$argumentLine = ($serverArguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
$recordDirectory = Join-Path $releaseRoot ('release-starts/' + $stamp)
New-Item -ItemType Directory -Path $recordDirectory -ErrorAction Stop | Out-Null
$stdout = Join-Path $recordDirectory 'stdout.log'
$stderr = Join-Path $recordDirectory 'stderr.log'
$plan['backupDirectory'] = (Resolve-Path -LiteralPath $BackupDirectory).Path
$plan['startedAtUtc'] = [DateTime]::UtcNow.ToString('o')
$plan['sourceSha256'] = (Get-FileHash -LiteralPath $serverArguments[0] -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $configSha256) {
    throw 'Release configuration changed after validation; review the launch plan again.'
}
$plan['manifestSha256'] = (Get-FileHash -LiteralPath 'I:/workspace/mobilegame/runtime/force-patch.json' -Algorithm SHA256).Hash.ToLowerInvariant()
$plan['stdout'] = $stdout
$plan['stderr'] = $stderr
$process = Start-Process -FilePath $pythonExecutable -ArgumentList $argumentLine `
    -WorkingDirectory $codeDirectory -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$plan['pid'] = $process.Id
$plan['status'] = 'launched-listeners-pending'
$recordPath = Join-Path $recordDirectory 'process.json'
$plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordPath -Encoding UTF8

$deadline = [DateTime]::UtcNow.AddSeconds(30)
$ready = $false
while ([DateTime]::UtcNow -lt $deadline) {
    $process.Refresh()
    if ($process.HasExited) { break }
    $owned = @(Get-NetTCPConnection -State Listen | Where-Object {
        $_.LocalPort -in @(19080, 19102) -and $_.OwningProcess -eq $process.Id
    })
    if (@($owned | Select-Object -ExpandProperty LocalPort | Sort-Object -Unique).Count -eq 2) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}
$plan['status'] = if ($ready) { 'both-listeners-ready' } else { 'startup-not-ready' }
$plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordPath -Encoding UTF8
if (-not $ready) { throw "Startup not ready; inspect $recordPath and stderr. No process was stopped automatically." }
Write-Output "Started PID $($process.Id); record: $recordPath"
