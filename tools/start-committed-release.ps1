[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$CodeDirectory,
    [switch]$CheckOnly,
    [string]$BackupDirectory,
    [string]$CandidateDirectory = 'I:/workspace/mobilegame/runtime/remediation-20261031/candidate'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$releaseRoot = 'I:/workspace/mobilegame/runtime/remediation-20261031'
$codeDirectory = (Resolve-Path -LiteralPath $CodeDirectory).Path
$codeCommit = (& git -C $codeDirectory rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'CodeDirectory must be a committed Git checkout.' }
$dirty = @(& git -C $codeDirectory status --porcelain)
if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) { throw 'Refusing release from a dirty checkout.' }
$pythonExecutable = 'C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe'
$releaseTools = Join-Path $releaseRoot 'release-tools/release_tools.py'
$serverArguments = @(
    (Join-Path $codeDirectory 'server.py'),
    '--fixture-root', 'I:/workspace/mobilegame/analysis/ljjc-local/fixtures',
    '--state-dir', 'I:/workspace/mobilegame/analysis/ljjc-local/state',
    '--log', 'I:/workspace/mobilegame/runtime/force-patch-requests.jsonl',
    '--cert', 'I:/workspace/mobilegame/analysis/ljjc-local/certs/server.pem',
    '--key', 'I:/workspace/mobilegame/analysis/ljjc-local/certs/server-key.pem',
    '--legacy-tls', '--http-port', '19080', '--tls-port', '19102',
    '--force-patch', 'I:/workspace/mobilegame/runtime/force-patch.json'
)

foreach ($required in @($pythonExecutable, $serverArguments[0], $releaseTools,
        'I:/workspace/mobilegame/analysis/ljjc-local/state/local.sqlite3',
        'I:/workspace/mobilegame/analysis/ljjc-local/certs/server.pem',
        'I:/workspace/mobilegame/analysis/ljjc-local/certs/server-key.pem')) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required file missing: $required" }
}
$listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(19080, 19102) })
$plan = [ordered]@{
    executable = $pythonExecutable
    codeCommit = $codeCommit
    workingDirectory = $codeDirectory
    arguments = $serverArguments
    occupiedPorts = @($listeners | Select-Object LocalAddress, LocalPort, OwningProcess)
    checkOnly = [bool]$CheckOnly
}
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
$plan['configSha256'] = (Get-FileHash -LiteralPath (Join-Path $codeDirectory 'server.toml') -Algorithm SHA256).Hash.ToLowerInvariant()
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
