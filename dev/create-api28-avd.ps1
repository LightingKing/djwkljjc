[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Users\Administrator\AppData\Local\Android\Sdk',
    [string]$AvdName = 'LJJC_Offline_API28',
    [string]$Device = 'pixel_2',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$javaHome = 'C:\Users\Administrator\.gradle\jdks\eclipse_adoptium-21-amd64-windows.2'
if (Test-Path -LiteralPath (Join-Path $javaHome 'bin\java.exe')) {
    $env:JAVA_HOME = $javaHome
}

$sdkManager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\sdkmanager.bat'
$avdManager = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
if (-not (Test-Path -LiteralPath $sdkManager)) { throw "sdkmanager not found: $sdkManager" }
if (-not (Test-Path -LiteralPath $avdManager)) { throw "avdmanager not found: $avdManager" }

$existing = @(& $avdManager list avd 2>$null | Select-String "^\s*Name:\s*$([regex]::Escape($AvdName))$")
if ($existing -and -not $Force) {
    Write-Output "AVD $AvdName already exists; leaving it unchanged."
    exit 0
}

Write-Output 'Installing/checking Android 9 API 28 x86 packages...'
& $sdkManager --sdk_root=$SdkRoot 'platforms;android-28' 'system-images;android-28;google_apis;x86'
if ($LASTEXITCODE -ne 0) { throw "sdkmanager failed with exit code $LASTEXITCODE" }

if ($existing -and $Force) {
    & $avdManager delete avd -n $AvdName
    if ($LASTEXITCODE -ne 0) { throw "avdmanager delete failed with exit code $LASTEXITCODE" }
}

Write-Output "Creating isolated AVD $AvdName (the existing emulator-5560 is not touched)..."
& $avdManager create avd -n $AvdName -k 'system-images;android-28;google_apis;x86' -d $Device --force
if ($LASTEXITCODE -ne 0) { throw "avdmanager create failed with exit code $LASTEXITCODE" }

Write-Output "Created $AvdName. Start it with .\dev\start-api28-avd.ps1."
