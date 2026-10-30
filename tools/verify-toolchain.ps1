[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Users\Administrator\AppData\Local\Android\Sdk',
    [switch]$RequireApi28
)

$ErrorActionPreference = 'Stop'
$javaHome = 'C:\Users\Administrator\.gradle\jdks\eclipse_adoptium-21-amd64-windows.2'
$checks = @(
    @{ Name = 'Python'; Path = (Get-Command python -ErrorAction SilentlyContinue).Source },
    @{ Name = 'Java'; Path = (Join-Path $javaHome 'bin\java.exe') },
    @{ Name = 'ADB'; Path = (Join-Path $SdkRoot 'platform-tools\adb.exe') },
    @{ Name = 'Emulator'; Path = (Join-Path $SdkRoot 'emulator\emulator.exe') },
    @{ Name = 'aapt2'; Path = (Join-Path $SdkRoot 'build-tools\37.0.0\aapt2.exe') },
    @{ Name = 'zipalign'; Path = (Join-Path $SdkRoot 'build-tools\37.0.0\zipalign.exe') },
    @{ Name = 'apksigner'; Path = (Join-Path $SdkRoot 'build-tools\37.0.0\apksigner.bat') }
)

$missing = @()
foreach ($check in $checks) {
    if ($check.Path -and (Test-Path -LiteralPath $check.Path)) {
        Write-Output ("{0}: {1}" -f $check.Name, $check.Path)
    } else {
        Write-Output ("{0}: MISSING" -f $check.Name)
        $missing += $check.Name
    }
}

$api28Image = Join-Path $SdkRoot 'system-images\android-28\google_apis\x86\system.img'
if (Test-Path -LiteralPath $api28Image) {
    Write-Output "API28 x86 image: installed"
} else {
    Write-Output "API28 x86 image: not installed"
    if ($RequireApi28) { $missing += 'API28 x86 image' }
}

if ($missing.Count -gt 0) {
    throw ("Toolchain verification failed: " + ($missing -join ', '))
}
Write-Output 'Toolchain verification passed.'
