[CmdletBinding()]
param(
    [string]$SdkRoot = '',
    [string]$JavaHome = '',
    [string]$Python = '',
    [string]$BuildToolsVersion = '',
    [string]$Requirements = '',
    [string]$Output = '',
    [switch]$Install,
    [switch]$RequireApi28,
    [switch]$Strict
)

# This script is intentionally conservative.  Without -Install it only reads
# PATH/environment variables and SDK directory contents; it never invokes pip,
# sdkmanager, avdmanager, or any network-facing installer.  It also never reads
# or writes APKs, game saves, databases, logs, or signing material.
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $PSScriptRoot

function Resolve-ExistingPath {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $resolved = [IO.Path]::GetFullPath($candidate)
        } catch {
            continue
        }
        if (Test-Path -LiteralPath $resolved) { return $resolved }
    }
    return ''
}

function Resolve-CommandPath {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command -and $command.Source) { return [string]$command.Source }
    if ($command -and $command.Path) { return [string]$command.Path }
    return ''
}

function Get-VersionSortKey {
    param([string]$Name)
    $match = [regex]::Match($Name, '\d+(?:\.\d+){0,3}')
    if (-not $match.Success) { return [version]'0.0' }
    try { return [version]$match.Value } catch { return [version]'0.0' }
}

function Find-LatestDirectory {
    param([string]$Parent)
    if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { return '' }
    $directory = Get-ChildItem -LiteralPath $Parent -Directory -ErrorAction SilentlyContinue |
        Sort-Object @{ Expression = { Get-VersionSortKey $_.Name }; Descending = $true } |
        Select-Object -First 1
    if ($directory) { return $directory.FullName }
    return ''
}

function Add-Check {
    param(
        [System.Collections.Generic.List[object]]$List,
        [string]$Name,
        [string]$Path,
        [string]$Kind = 'file',
        [switch]$Required
    )
    $exists = if ($Kind -eq 'directory') {
        -not [string]::IsNullOrWhiteSpace($Path) -and (Test-Path -LiteralPath $Path -PathType Container)
    } else {
        -not [string]::IsNullOrWhiteSpace($Path) -and (Test-Path -LiteralPath $Path -PathType Leaf)
    }
    $List.Add([pscustomobject]@{
        name = $Name
        kind = $Kind
        path = if ($Path) { $Path } else { $null }
        status = if ($exists) { 'present' } elseif ($Required) { 'missing' } else { 'optional_missing' }
        required = [bool]$Required
    })
}

function Get-TextVersion {
    param([string]$Executable, [string[]]$Arguments)
    if ([string]::IsNullOrWhiteSpace($Executable) -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return '' }
    try {
        $output = & $Executable @Arguments 2>&1 | Out-String
        return ($output.Trim() -split "`r?`n" | Select-Object -First 1)
    } catch { return '' }
}

if ([string]::IsNullOrWhiteSpace($Requirements)) {
    $Requirements = Join-Path $scriptRoot 'requirements.txt'
} elseif (-not [IO.Path]::IsPathRooted($Requirements)) {
    $Requirements = Join-Path $scriptRoot $Requirements
}

$sdkCandidates = if (-not [string]::IsNullOrWhiteSpace($SdkRoot)) {
    @($SdkRoot)
} else {
    @(
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
        $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE 'AppData\Local\Android\Sdk' })
    )
}
$resolvedSdk = Resolve-ExistingPath $sdkCandidates
if ([string]::IsNullOrWhiteSpace($resolvedSdk)) {
    # Keep the first explicit/environment candidate in the report even when it
    # does not exist, so a user can see exactly what to create or install.
    $resolvedSdk = ($sdkCandidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
}

$javaHomeSpecified = -not [string]::IsNullOrWhiteSpace($JavaHome) -or -not [string]::IsNullOrWhiteSpace($env:JAVA_HOME)
$javaCandidates = if ($JavaHome) {
    @($JavaHome)
} elseif ($env:JAVA_HOME) {
    @($env:JAVA_HOME)
} else {
    @()
}
$javaHomeResolved = Resolve-ExistingPath $javaCandidates
$javaExe = if ($javaHomeResolved) {
    Join-Path $javaHomeResolved 'bin\java.exe'
} elseif ($javaHomeSpecified) {
    $javaCandidate = $javaCandidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
    if ($javaCandidate) { Join-Path $javaCandidate 'bin\java.exe' } else { '' }
} else {
    Resolve-CommandPath 'java'
}
if (-not $javaHomeResolved -and $javaExe) { $javaHomeResolved = Split-Path (Split-Path $javaExe -Parent) -Parent }

$pythonSpecified = -not [string]::IsNullOrWhiteSpace($Python)
$pythonExe = if ($pythonSpecified) {
    Resolve-ExistingPath @($Python)
} else {
    Resolve-CommandPath 'python'
}
if (-not $pythonExe -and -not $pythonSpecified) { $pythonExe = Resolve-CommandPath 'py' }

$buildToolsRoot = if ($resolvedSdk) { Join-Path $resolvedSdk 'build-tools' } else { '' }
$buildTools = if ($BuildToolsVersion) {
    Resolve-ExistingPath @((Join-Path $buildToolsRoot $BuildToolsVersion))
} else {
    Find-LatestDirectory $buildToolsRoot
}
$buildToolsName = if ($buildTools) { Split-Path $buildTools -Leaf } else { '' }

$cmdlineRoot = if ($resolvedSdk) { Join-Path $resolvedSdk 'cmdline-tools' } else { '' }
$cmdlineCandidates = @()
if ($cmdlineRoot) {
    $cmdlineCandidates += Join-Path $cmdlineRoot 'latest'
    $latestVersioned = Find-LatestDirectory $cmdlineRoot
    if ($latestVersioned) { $cmdlineCandidates += $latestVersioned }
}
$cmdline = Resolve-ExistingPath $cmdlineCandidates

$checks = [System.Collections.Generic.List[object]]::new()
$adbPath = if ($resolvedSdk) { Join-Path $resolvedSdk 'platform-tools\adb.exe' } else { '' }
$emulatorPath = if ($resolvedSdk) { Join-Path $resolvedSdk 'emulator\emulator.exe' } else { '' }
$sdkManagerPath = if ($cmdline) { Join-Path $cmdline 'bin\sdkmanager.bat' } else { '' }
$avdManagerPath = if ($cmdline) { Join-Path $cmdline 'bin\avdmanager.bat' } else { '' }
$aapt2Path = if ($buildTools) { Join-Path $buildTools 'aapt2.exe' } else { '' }
$zipalignPath = if ($buildTools) { Join-Path $buildTools 'zipalign.exe' } else { '' }
$apksignerPath = if ($buildTools) { Join-Path $buildTools 'apksigner.bat' } else { '' }
Add-Check $checks 'Python' $pythonExe -Required
Add-Check $checks 'requirements.txt' $Requirements
Add-Check $checks 'Java' $javaExe -Required
Add-Check $checks 'Android SDK root' $resolvedSdk -Kind directory -Required
Add-Check $checks 'ADB' $adbPath -Required
Add-Check $checks 'Emulator' $emulatorPath -Required
Add-Check $checks 'sdkmanager' $sdkManagerPath
Add-Check $checks 'avdmanager' $avdManagerPath
Add-Check $checks 'Build-tools directory' $buildTools -Kind directory -Required
Add-Check $checks 'aapt2' $aapt2Path -Required
Add-Check $checks 'zipalign' $zipalignPath -Required
Add-Check $checks 'apksigner' $apksignerPath -Required

$api28Image = if ($resolvedSdk) { Join-Path $resolvedSdk 'system-images\android-28\google_apis\x86\system.img' } else { '' }
Add-Check $checks 'API28 x86 system image' $api28Image -Required:$RequireApi28

$missing = @($checks | Where-Object { $_.status -eq 'missing' } | ForEach-Object { $_.name })
$status = if ($missing.Count -eq 0) { 'ready' } else { 'needs_install' }
$installCommands = @()
if ($pythonExe -and (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
    $installCommands += "& `"$pythonExe`" -m pip install -r `"$Requirements`""
}
if ($sdkManagerPath) {
    $installCommands += "& `"$sdkManagerPath`" --sdk_root=`"$resolvedSdk`" 'platform-tools' 'emulator' 'platforms;android-28' 'build-tools;37.0.0' 'system-images;android-28;google_apis;x86'"
}

if ($Install) {
    if (-not $pythonExe -or -not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw 'Install requested but Python or requirements.txt is unavailable.'
    }
    Write-Output 'Install mode enabled explicitly; installing Python requirements.'
    & $pythonExe -m pip install -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
    if (-not $sdkManagerPath -or -not (Test-Path -LiteralPath $sdkManagerPath -PathType Leaf)) {
        throw 'Install requested but sdkmanager is unavailable. Install Android command-line tools first.'
    }
    Write-Output 'Installing/checking Android SDK packages.'
    & $sdkManagerPath --sdk_root=$resolvedSdk 'platform-tools' 'emulator' 'platforms;android-28' 'build-tools;37.0.0' 'system-images;android-28;google_apis;x86'
    if ($LASTEXITCODE -ne 0) { throw "sdkmanager failed with exit code $LASTEXITCODE" }
    $status = 'installed_or_updated'
}

$report = [pscustomobject]@{
    schemaVersion = 1
    mode = if ($Install) { 'install' } else { 'dry-run' }
    status = $status
    missing = @($missing)
    sdkRoot = $resolvedSdk
    javaHome = $javaHomeResolved
    python = $pythonExe
    buildToolsVersion = $buildToolsName
    checks = @($checks)
    installCommands = @($installCommands)
    versions = [pscustomobject]@{
        python = Get-TextVersion $pythonExe @('--version')
        java = Get-TextVersion $javaExe @('-version')
        adb = Get-TextVersion $adbPath @('version')
        emulator = Get-TextVersion $emulatorPath @('-version')
    }
}

if ($Output) {
    $outputPath = if ([IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $scriptRoot $Output }
    $parent = Split-Path -Parent $outputPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding UTF8
}

Write-Output ("Toolchain bootstrap mode={0} status={1}." -f $report.mode, $report.status)
if ($missing.Count -gt 0) { Write-Output ("Missing required components: " + ($missing -join ', ')) }
if ($installCommands.Count -gt 0 -and -not $Install) { Write-Output 'Install commands are suggestions only; pass -Install explicitly to execute them.' }
$report | ConvertTo-Json -Depth 8
if ($Strict -and $missing.Count -gt 0) { exit 1 }
