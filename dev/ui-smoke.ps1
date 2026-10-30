[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Users\Administrator\AppData\Local\Android\Sdk',
    [string]$Serial = 'emulator-5560',
    [string]$PackageName = 'com.tencent.tmgp.fcicv',
    [int]$Runs = 1,
    [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
if ($Runs -lt 1 -or $Runs -gt 10) { throw 'Runs must be between 1 and 10.' }
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb)) { throw "adb not found: $adb" }
if (@(& $adb -s $Serial get-state 2>$null) -notcontains 'device') { throw "$Serial is not an online adb device." }
$outputPath = if ($Output) { [IO.Path]::GetFullPath($Output) } else { Join-Path $PSScriptRoot 'ui-smoke-report.json' }
$artifactRoot = Join-Path ([IO.Path]::GetDirectoryName($outputPath)) ([IO.Path]::GetFileNameWithoutExtension($outputPath))
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
$records = @()

for ($index = 1; $index -le $Runs; $index++) {
    # Force-stop and relaunch only.  No pm clear/uninstall is used.
    & $adb -s $Serial shell am force-stop $PackageName | Out-Null
    & $adb -s $Serial shell monkey -p $PackageName 1 | Out-Null
    Start-Sleep -Seconds 3
    $xmlRemote = "/sdcard/ljjc-ui-$index.xml"
    $xmlLocal = Join-Path $artifactRoot "ui-$index.xml"
    $pngLocal = Join-Path $artifactRoot "screen-$index.png"
    & $adb -s $Serial shell uiautomator dump $xmlRemote | Out-Null
    & $adb -s $Serial pull $xmlRemote $xmlLocal | Out-Null
    & $adb -s $Serial exec-out screencap -p > $pngLocal
    $focused = @(& $adb -s $Serial shell dumpsys activity activities 2>$null | Select-String -Pattern $PackageName | Select-Object -First 1)
    $fatal = @(& $adb -s $Serial logcat -d -t 400 2>$null | Select-String -Pattern 'FATAL EXCEPTION|has died|Process .* crashed')
    $records += [pscustomobject]@{
        run = $index
        focusedPackage = [bool]$focused
        fatalLogLines = $fatal.Count
        uiXml = $xmlLocal
        uiXmlSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $xmlLocal).Hash.ToLowerInvariant()
        screenshot = $pngLocal
        screenshotSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $pngLocal).Hash.ToLowerInvariant()
    }
}

$report = [pscustomobject]@{
    serial = $Serial
    package = $PackageName
    runs = $Runs
    passed = @($records | Where-Object { $_.focusedPackage -and $_.fatalLogLines -eq 0 }).Count -eq $Runs
    records = $records
}
$parent = Split-Path -Parent $outputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outputPath -Encoding UTF8
$report | ConvertTo-Json -Depth 6
if (-not $report.passed) { exit 2 }
