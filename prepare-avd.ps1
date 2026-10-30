<#!
.SYNOPSIS
    Prepare only the dedicated DJWK_LJJC_API30 emulator for local routing.

This script is intentionally fixed to adb server port 5038 and serial
emulator-5560.  It does not start an emulator, an application, Frida, or a
local server.  It changes only this emulator's reverse mappings, a bind-mounted
hosts file, and rules in the private LJJC_LOCAL chains.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$AdbPort = 5038
$Serial = 'emulator-5560'
$ExpectedAvd = 'DJWK_LJJC_API30'
$Package = 'com.tencent.tmgp.fcicv'
$Chain = 'LJJC_LOCAL'
$HostsSource = Join-Path $PSScriptRoot '../../analysis/ljjc-local/hosts'
$GuestHosts = '/data/local/tmp/ljjc-hosts'
$GuestHostsRoots = @('/data/local/tmp/ljjc-hosts', '/local/tmp/ljjc-hosts')
$GuestHostsTarget = '/system/etc/hosts'

if ($args.Count -ne 0) {
    throw 'This script has no variable target: use adb server port 5038 and serial emulator-5560 only.'
}

$adbCandidates = @(
    'C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe'
)
$adb = $adbCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $adb) {
    $adbCommand = Get-Command adb -CommandType Application -ErrorAction Stop
    $adb = $adbCommand.Source
}

function Invoke-Adb {
    param([Parameter(Mandatory)][string[]]$Arguments)
    # adb writes progress (notably `push`) to stderr even when it succeeds.
    # Capture that stream for diagnostics without letting the script-wide
    # Stop preference turn successful commands into terminating errors.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $result = @(& $adb @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($exitCode -ne 0) {
        throw "adb $($Arguments -join ' ') failed with exit code ${exitCode}: $($result -join ' ')"
    }
    return $result | ForEach-Object { [string]$_ }
}

function Invoke-Guest {
    param([Parameter(Mandatory)][string[]]$Command)
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'shell') + $Command
    return Invoke-Adb -Arguments $arguments
}

function Test-Guest {
    param([Parameter(Mandatory)][string[]]$Command)
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'shell') + $Command
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        $null = & $adb @arguments 2>$null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return $exitCode -eq 0
}

function Invoke-GuestChecked {
    param([Parameter(Mandatory)][string[]]$Command)
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'shell') + $Command
    return Invoke-Adb -Arguments $arguments
}

function Assert-FixedTarget {
    $null = Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'get-state')
    $avdReply = (Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'emu', 'avd', 'name')) -join "`n"
    if ($avdReply -notmatch "(?m)^\s*(?:name:\s*)?$([regex]::Escape($ExpectedAvd))\s*$") {
        throw "Unexpected emulator AVD name; expected exactly $ExpectedAvd. Reply: $avdReply"
    }
    $qemu = ((Invoke-Guest -Command @('getprop', 'ro.kernel.qemu')) -join '').Trim()
    if ($qemu -ne '1') {
        throw "Refusing non-emulator target: ro.kernel.qemu=$qemu"
    }
}

function Ensure-PrivateChain {
    param([Parameter(Mandatory)][string]$Binary)

    $existing = @()
    if (Test-Guest -Command @($Binary, '-w', '-t', 'filter', '-S', $Chain)) {
        $existing = @(Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-S', $Chain))
        $unexpected = @($existing | Where-Object {
                $_ -notmatch "^-N $Chain$" -and -not ($_ -match "^-A $Chain " -and $_ -match "--uid-owner $script:PackageUid(?:\s|$)" -and $_ -match '-j (?:ACCEPT|REJECT)(?:\s|$)')
            })
        if ($unexpected.Count -ne 0) {
            throw "Refusing to modify existing $Binary chain $Chain with unrecognized rules."
        }
        Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-F', $Chain) | Out-Null
    } else {
        Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-N', $Chain) | Out-Null
    }

    $owner = @('-m', 'owner', '--uid-owner', $script:PackageUid)
    if ($Binary -eq 'iptables') {
        $local = @('-d', '127.0.0.1')
    } else {
        $local = @('-d', '::1')
    }
    foreach ($port in @(19080,9102)) {
        $allowRule = @($Binary, '-w', '-t', 'filter', '-A', $Chain) +
            $owner + $local + @('-p', 'tcp', '--dport', "$port", '-j', 'ACCEPT')
        Invoke-GuestChecked -Command $allowRule | Out-Null
    }
    $rejectRule = @($Binary, '-w', '-t', 'filter', '-A', $Chain) +
        $owner + @('-j', 'REJECT')
    Invoke-GuestChecked -Command $rejectRule | Out-Null

    if (-not (Test-Guest -Command @($Binary, '-w', '-t', 'filter', '-C', 'OUTPUT', '-j', $Chain))) {
        Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-I', 'OUTPUT', '1', '-j', $Chain) | Out-Null
    }
}

Write-Output "Validating fixed target $Serial on adb server port $AdbPort..."
Assert-FixedTarget

$rootReply = (Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'root')) -join ' '
if ($rootReply -match 'cannot run as root') {
    throw "adbd root was refused: $rootReply"
}
Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'wait-for-device') | Out-Null
Assert-FixedTarget
$identity = (Invoke-Guest -Command @('id')) -join ' '
if ($identity -notmatch 'uid=0\b') {
    throw "adbd is not root after adb root: $identity"
}

$packageReply = Invoke-Guest -Command @('cmd', 'package', 'list', 'packages', '-U', $Package)
$uidMatches = @($packageReply | ForEach-Object {
        if ($_ -match "^package:$([regex]::Escape($Package))\s+uid:(\d+)\s*$") { $Matches[1] }
    })
if ($uidMatches.Count -ne 1) {
    throw "Could not resolve exactly one UID for $Package. Reply: $($packageReply -join ' ')"
}
$script:PackageUid = [int]$uidMatches[0]
if ($script:PackageUid -le 0) {
    throw "Invalid package UID $script:PackageUid"
}

if (-not (Test-Path -LiteralPath $HostsSource -PathType Leaf)) {
    throw "Missing hosts fixture: $HostsSource"
}
$hostsText = Get-Content -LiteralPath $HostsSource -Raw
if ($hostsText -notmatch '(?m)^127\.0\.0\.1\s+(?:\S+\s+)*prds\.djwk\.qq\.com(?:\s|$)') {
    throw 'Hosts fixture does not contain the required prds.djwk.qq.com mapping.'
}

# Reverse maps are explicit and local; no Windows hosts or firewall is touched.
$reverse = @(Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--list'))
if ($reverse -notmatch '\btcp:19080\s+tcp:19080\b') {
    Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', 'tcp:19080', 'tcp:19080') | Out-Null
}
if ($reverse -notmatch '\btcp:9102\s+tcp:19102\b') {
    Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', 'tcp:9102', 'tcp:19102') | Out-Null
}

$hostsMounts = @(Invoke-Guest -Command @('cat', '/proc/self/mountinfo'))
$ownedMount = $hostsMounts | Where-Object {
    $fields = ([string]$_).Trim() -split '\s+'
    # mountinfo fields: root is index 3 and mount point is index 4.
    $fields.Count -ge 5 -and $GuestHostsRoots -contains $fields[3] -and $fields[4] -eq $GuestHostsTarget
}
if (-not $ownedMount) {
    Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'push', $HostsSource, $GuestHosts) | Out-Null
    Invoke-GuestChecked -Command @('chmod', '0644', $GuestHosts) | Out-Null
    Invoke-GuestChecked -Command @('chcon', 'u:object_r:system_file:s0', $GuestHosts) | Out-Null
    Invoke-GuestChecked -Command @('mount', '--bind', $GuestHosts, $GuestHostsTarget) | Out-Null
}
$mountedText = (Invoke-Guest -Command @('cat', $GuestHostsTarget)) -join "`n"
if ($mountedText -notmatch '(?m)^127\.0\.0\.1\s+(?:\S+\s+)*prds\.djwk\.qq\.com(?:\s|$)') {
    throw "Mounted guest hosts file lacks prds.djwk.qq.com mapping."
}

Ensure-PrivateChain -Binary 'iptables'
Ensure-PrivateChain -Binary 'ip6tables'

Write-Output "Prepared $Package (UID $script:PackageUid) on $ExpectedAvd; no application was started."
