[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Users\Administrator\AppData\Local\Android\Sdk',
    [string]$Serial = 'emulator-5562',
    [string]$PackageName = 'com.tencent.tmgp.fcicv',
    [int]$Runs = 5,
    [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
if ($Runs -lt 1 -or $Runs -gt 20) { throw 'Runs must be between 1 and 20.' }
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb)) { throw "adb not found: $adb" }
$outputPath = if ($Output) { [IO.Path]::GetFullPath($Output) } else { Join-Path $PSScriptRoot 'cold-start-report.json' }
$state = @()
try {
    $state = @(& $adb -s $Serial get-state 2>$null)
} catch {
    $state = @()
}
if ($state -notcontains 'device') {
    # Keep the historical throw/exit behavior, but leave a machine-readable
    # explanation so an offline or unsupported AVD is not confused with an
    # application crash.
    $unavailable = [pscustomobject]@{
        schemaVersion = 2
        serial = $Serial
        package = $PackageName
        runs = $Runs
        status = 'unavailable'
        reason = @('adb_device_offline')
        passed = $false
        records = @()
    }
    $parent = Split-Path -Parent $outputPath
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $unavailable | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $outputPath -Encoding UTF8
    throw "$Serial is not an online adb device. Report: $outputPath"
}

function Get-AdbProperty {
    param([Parameter(Mandatory = $true)][string]$Name)
    try {
        $value = & $adb -s $Serial shell getprop $Name 2>$null | Out-String
        return $value.Trim()
    } catch {
        return ''
    }
}

# Capture the execution environment before launching the package.  These
# values are diagnostic metadata only; the check does not change ABI, install
# packages, or modify device settings.  In particular, an empty
# ro.dalvik.vm.native.bridge is useful evidence when a 32-bit APK is being
# tested on a 64-bit-only device.
$deviceAbiList = Get-AdbProperty 'ro.product.cpu.abilist'
$deviceAbiList32 = Get-AdbProperty 'ro.product.cpu.abilist32'
$deviceAbi = Get-AdbProperty 'ro.product.cpu.abi'
$nativeBridge = Get-AdbProperty 'ro.dalvik.vm.native.bridge'
$apiLevelText = Get-AdbProperty 'ro.build.version.sdk'
$apiLevel = 0
[void][int]::TryParse($apiLevelText, [ref]$apiLevel)
$bootCompleted = (Get-AdbProperty 'sys.boot_completed') -eq '1'
$hostArchitecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
$environmentReasons = @()
$environmentStatus = 'ready'
if (-not $bootCompleted) {
    $environmentStatus = 'degraded'
    $environmentReasons += 'device_boot_not_completed'
}
$known32BitAbi = @('x86', 'armeabi-v7a', 'armeabi')
$has32BitAbi = @($deviceAbiList32 -split ',' | Where-Object { $known32BitAbi -contains $_.Trim() }).Count -gt 0
if (-not $has32BitAbi -and [string]::IsNullOrWhiteSpace($nativeBridge)) {
    $environmentStatus = 'degraded'
    $environmentReasons += 'no_32bit_abi_or_native_bridge'
}
$records = @()

for ($index = 1; $index -le $Runs; $index++) {
    $started = Get-Date
    # Force-stop only the target package. No pm clear/uninstall is used, so
    # this check preserves the local save while testing repeated launches.
    # Clear the emulator log before each run.  Without this, a death from an
    # earlier run remains in ``logcat -d`` and is incorrectly attributed to
    # every later launch.
    & $adb -s $Serial logcat -c | Out-Null
    & $adb -s $Serial shell am force-stop $PackageName | Out-Null
    & $adb -s $Serial shell monkey -p $PackageName 1 | Out-Null
    Start-Sleep -Seconds 3
    $focusedLine = & $adb -s $Serial shell dumpsys activity activities 2>$null | Select-String -Pattern $PackageName | Select-Object -First 1
    $focused = if ($focusedLine) { $focusedLine.ToString() } else { '' }
    $logLines = @(& $adb -s $Serial logcat -d -t 400 2>$null)
    $fatal = @($logLines | Select-String -Pattern 'FATAL EXCEPTION|has died|Process .* crashed')
    $cleanExit = @($logLines | Select-String -Pattern 'clean exit|exited with code|process .* has died')
    $pidText = (& $adb -s $Serial shell pidof $PackageName 2>$null | Out-String).Trim()
    $records += [pscustomobject]@{
        run = $index
        startedAt = $started.ToUniversalTime().ToString('o')
        focusedPackage = ($focused -match [regex]::Escape($PackageName))
        fatalLogLines = $fatal.Count
        processAlive = -not [string]::IsNullOrWhiteSpace($pidText)
        cleanExitLogLines = $cleanExit.Count
    }
}

$passed = @($records | Where-Object { $_.focusedPackage -and $_.fatalLogLines -eq 0 }).Count -eq $Runs
$reportReason = @($environmentReasons)
if (-not $passed) { $reportReason = @('cold_start_failed') + $reportReason }
$report = [pscustomobject]@{
    schemaVersion = 2
    serial = $Serial
    package = $PackageName
    runs = $Runs
    status = if ($passed) { 'passed' } else { 'failed' }
    reason = $reportReason
    environment = [pscustomobject]@{
        status = $environmentStatus
        reason = @($environmentReasons)
        hostArchitecture = $hostArchitecture
        hostIs64Bit = [Environment]::Is64BitOperatingSystem
        apiLevel = $apiLevel
        deviceAbi = $deviceAbi
        deviceAbiList = @($deviceAbiList -split ',' | Where-Object { $_ })
        deviceAbiList32 = @($deviceAbiList32 -split ',' | Where-Object { $_ })
        nativeBridge = $nativeBridge
        bootCompleted = $bootCompleted
    }
    passed = $passed
    records = $records
}
$parent = Split-Path -Parent $outputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$report | ConvertTo-Json -Depth 5
if (-not $report.passed) { exit 2 }
