<#!
.SYNOPSIS
    Remove only the local-routing state created for DJWK_LJJC_API30.

The target is fixed to adb server port 5038 and serial emulator-5560.  The
script validates that target before force-stopping the original game.  It does
not touch Windows hosts/firewall state, another emulator, or another UID.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$AdbPort = 5038
$Serial = 'emulator-5560'
$ExpectedAvd = 'DJWK_LJJC_API30'
$Package = 'com.tencent.tmgp.fcicv'
$Chain = 'LJJC_LOCAL'
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
    $result = @(& $adb @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
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
    $null = & $adb @arguments 2>&1
    return $LASTEXITCODE -eq 0
}

function Invoke-GuestChecked {
    param([Parameter(Mandatory)][string[]]$Command)
    return Invoke-Guest -Command $Command
}

function Stop-LegacyWatchdogGuard {
    # Older launcher revisions left this shell loop orphaned inside the AVD.
    # It repeatedly killed TPNS and made the client appear unresponsive.  The
    # current launcher uses the official TPNS setting instead, so cleanup may
    # remove only this exact, repository-owned guest path.  Do not match a
    # generic shell or any unrelated watchdog process.
    $guardPath = '/data/local/tmp/ljjc-watchdog-guard.sh'
    $rows = @(Invoke-Guest -Command @('ps', '-A', '-o', 'PID,ARGS'))
    foreach ($row in $rows) {
        $text = ([string]$row).Trim()
        $match = [regex]::Match($text, '^\s*(\d+)\s+.*' + [regex]::Escape($guardPath) + '(?:\s|$)')
        if ($match.Success) {
            Invoke-GuestChecked -Command @('kill', '-9', $match.Groups[1].Value) | Out-Null
        }
    }
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

function Remove-PrivateChain {
    param([Parameter(Mandatory)][string]$Binary)
    if (-not (Test-Guest -Command @($Binary, '-w', '-t', 'filter', '-S', $Chain))) {
        return
    }
    $rules = @(Invoke-Guest -Command @($Binary, '-w', '-t', 'filter', '-S', $Chain))
    $unexpected = @($rules | Where-Object {
            $_ -notmatch "^-N $Chain$" -and -not ($_ -match "^-A $Chain " -and $_ -match "--uid-owner $script:PackageUid(?:\s|$)" -and $_ -match '-j (?:ACCEPT|REJECT)(?:\s|$)')
        })
    if ($unexpected.Count -ne 0) {
        throw "Refusing to remove existing $Binary chain $Chain with unrecognized rules."
    }
    if (Test-Guest -Command @($Binary, '-w', '-t', 'filter', '-C', 'OUTPUT', '-j', $Chain)) {
        Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-D', 'OUTPUT', '-j', $Chain) | Out-Null
    }
    Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-F', $Chain) | Out-Null
    Invoke-GuestChecked -Command @($Binary, '-w', '-t', 'filter', '-X', $Chain) | Out-Null
}

function Remove-LegacyDirectRules {
    param([Parameter(Mandatory)][string]$Binary)
    # These are only the old direct rules from this local test: package UID,
    # local diagnostic ports, and the matching package-wide REJECT.  Rules for
    # other UIDs and all LJJC_LOCAL chain rules are intentionally excluded.
    while ($true) {
        $lines = @(Invoke-Guest -Command @($Binary, '-w', '-t', 'filter', '-S', 'OUTPUT'))
        $removed = $false
        foreach ($line in $lines) {
            $text = ([string]$line).Trim()
            if ($text -notmatch "^-A OUTPUT\s+.*--uid-owner\s+$([regex]::Escape([string]$script:PackageUid))(?:\s|$)") {
                continue
            }
            if ($text -match '--comment LJJC_LOCAL' -or $text -match '-j LJJC_LOCAL(?:\s|$)') {
                continue
            }
            $isLocalAccept = $text -match '-j ACCEPT(?:\s|$)' -and
                $text -match '(?:-d 127\.0\.0\.1(?:/32)?(?:\s|$)|-d ::1(?:/128)?(?:\s|$))' -and
                $text -match '(?:--dports 19080,9102|--dport 19080|--dport 9102)(?:\s|$)'
            $isPackageReject = $text -match '--uid-owner\s+' + [regex]::Escape([string]$script:PackageUid) +
                '(?:\s|$)' -and $text -match '-j REJECT(?:\s|$)' -and
                $text -match '^-A OUTPUT\s+-m owner\s+--uid-owner\s+' + [regex]::Escape([string]$script:PackageUid) +
                '\s+(?:-m comment\s+--comment [^ ]+\s+)?-j REJECT(?: --reject-with (?:icmp-port-unreachable|icmp6-port-unreachable))?\s*$'
            if (-not ($isLocalAccept -or $isPackageReject)) {
                continue
            }
            $tokens = @($text -split '\s+')
            $tokens[0] = '-D'
            $deleteRule = @($Binary, '-w', '-t', 'filter') + $tokens
            Invoke-GuestChecked -Command $deleteRule | Out-Null
            $removed = $true
            break
        }
        if (-not $removed) {
            break
        }
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
if (((Invoke-Guest -Command @('id')) -join ' ') -notmatch 'uid=0\b') {
    throw 'adbd is not root; refusing to mutate guest routing state.'
}

$packageReply = Invoke-Guest -Command @('cmd', 'package', 'list', 'packages', '-U', $Package)
$uidMatches = @($packageReply | ForEach-Object {
        if ($_ -match "^package:$([regex]::Escape($Package))\s+uid:(\d+)\s*$") { $Matches[1] }
    })
if ($uidMatches.Count -ne 1) {
    throw "Could not resolve exactly one UID for $Package. Reply: $($packageReply -join ' ')"
}
$script:PackageUid = [int]$uidMatches[0]

# Stop only the original game, after target identity checks and before cleanup.
Invoke-GuestChecked -Command @('am', 'force-stop', $Package) | Out-Null
Stop-LegacyWatchdogGuard

$reverse = @(Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--list'))
if ($reverse -match '\btcp:19080\s+tcp:19080\b') {
    Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--remove', 'tcp:19080') | Out-Null
}
if ($reverse -match '\btcp:9102\s+tcp:19102\b') {
    Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--remove', 'tcp:9102') | Out-Null
}

$mounts = @(Invoke-Guest -Command @('cat', '/proc/self/mountinfo'))
$ownedMount = $mounts | Where-Object {
    $fields = ([string]$_).Trim() -split '\s+'
    $fields.Count -ge 5 -and $GuestHostsRoots -contains $fields[3] -and $fields[4] -eq $GuestHostsTarget
}
if ($ownedMount) {
    Invoke-GuestChecked -Command @('umount', $GuestHostsTarget) | Out-Null
    # Remove the fixed temporary source only after the exact bind mount above
    # was found and detached; an unrelated file at that path is preserved.
    Invoke-GuestChecked -Command @('rm', '-f', $GuestHosts) | Out-Null
}

Remove-LegacyDirectRules -Binary 'iptables'
Remove-LegacyDirectRules -Binary 'ip6tables'
Remove-PrivateChain -Binary 'iptables'
Remove-PrivateChain -Binary 'ip6tables'

Write-Output "Removed local routing state for $Package on $ExpectedAvd; other UIDs and host configuration were not touched."
