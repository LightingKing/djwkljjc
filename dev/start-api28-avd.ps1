[CmdletBinding()]
param(
    [string]$SdkRoot = 'C:\Users\Administrator\AppData\Local\Android\Sdk',
    [string]$AvdName = 'LJJC_Offline_API28',
    [int]$Port = 5562,
    # Android's normal local adb server is 5037.  5038 is used by the
    # connected handset on this machine and must remain an explicit opt-in;
    # using it as the emulator default makes the wait loop miss a successfully
    # booted AVD registered with the normal server.
    [int]$AdbPort = 5037,
    [switch]$WaitForBoot
)

$ErrorActionPreference = 'Stop'
$emulator = Join-Path $SdkRoot 'emulator\emulator.exe'
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $emulator)) { throw "emulator not found: $emulator" }
if (-not (Test-Path -LiteralPath $adb)) { throw "adb not found: $adb" }

$avds = @(& $emulator -list-avds)
if ($avds -notcontains $AvdName) {
    throw "AVD $AvdName is not registered. Run .\dev\create-api28-avd.ps1 first."
}

$serial = "emulator-$Port"
$state = @()
# A not-yet-started emulator makes adb return a native non-zero status.  In
# Windows PowerShell that can surface as a terminating error even when stderr
# is redirected, so treat it as the expected "not running" state.
try {
    $state = @(& $adb -P $AdbPort -s $serial get-state 2>$null)
} catch {
    $state = @()
}
if ($state -contains 'device') {
    Write-Output "$serial is already running; no restart was performed."
    if (-not $WaitForBoot) { exit 0 }
} else {
    Write-Output "Starting $AvdName on $serial with -no-audio (existing emulator-5560 is not touched)..."
    Start-Process -FilePath $emulator -ArgumentList @(
        '-avd', $AvdName,
        '-port', "$Port",
        '-no-audio',
        '-no-snapshot',
        '-netdelay', 'none',
        '-netspeed', 'full'
    ) | Out-Null
}

if ($WaitForBoot) {
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 2
        $boot = @()
        try {
            $boot = @(& $adb -P $AdbPort -s $serial shell getprop sys.boot_completed 2>$null)
        } catch {
            # The emulator is commonly reported as offline for a few polls
            # while adbd restarts.  Keep waiting instead of aborting the
            # startup script on that transient native exit status.
            $boot = @()
        }
    } while ((Get-Date) -lt $deadline -and ($boot -notcontains '1'))
    if ($boot -notcontains '1') { throw "Timed out waiting for $serial to finish booting." }
    Write-Output "$serial finished booting."
}
