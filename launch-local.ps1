<#!
.SYNOPSIS
    Reproduce the original LJJC local startup path on the dedicated AVD.

This script intentionally has no target-selection parameters.  It accepts
only adb server port 5038 and serial emulator-5560, verifies that the device
is the DJWK_LJJC_API30 emulator, starts that existing dedicated AVD when it is
not already running, starts the local fixture service, applies the
isolated routing setup, launches the original APK Activity, and performs a
late Frida Lua injection to expose the local guest-login button.

It does not create an AVD, use a physical device, change Windows networking,
or modify the installed APK.  If the dedicated emulator is not running, the
script starts the already-registered local AVD and waits for its fixed serial.
#>
[CmdletBinding()]
param(
    # Opt-in only; the default launch path remains unchanged.
    [switch]$FirstBattle
)

$ErrorActionPreference = 'Stop'

$AdbPort = 5038
$Serial = 'emulator-5560'
$ExpectedAvd = 'DJWK_LJJC_API30'
$Package = 'com.tencent.tmgp.fcicv'
$Activity = 'com.actgames.fci.MainActivity'
$LegacyWatchdogGuardPath = '/data/local/tmp/ljjc-watchdog-guard.sh'
$FridaGuestPath = '/data/local/tmp/ljjc-frida-server'
$FridaHostPort = 27052
$FridaGuestPort = 27042
$EmulatorPort = 5560
$EmulatorBootTimeoutSeconds = 180
$GuestAttempts = 5
$InstrumentationDuration = 5
$UiSettleSeconds = 15
$FirstBattleLoginTimeoutSeconds = 90
$FirstBattleReadyTimeoutSeconds = 180

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$analysisDir = Join-Path $workspace 'analysis/ljjc-local'
$serverScript = Join-Path $PSScriptRoot 'start-service.ps1'
$prepareScript = Join-Path $PSScriptRoot 'prepare-avd.ps1'
$instrumentScript = Join-Path $analysisDir 'run_instrumentation.py'
$fridaScript = Join-Path $analysisDir 'evaluate_client_lua.js'
$luaQueryFile = Join-Path $PSScriptRoot 'client/local-guest-entry.lua'
$firstBattleGuestQueryFile = Join-Path $PSScriptRoot 'client/first-battle-guest.lua'
$firstBattleQueryFile = Join-Path $PSScriptRoot 'client/first-battle.lua'
$logDir = Join-Path $analysisDir 'launch-local'
$avdDir = Join-Path $workspace 'runtime/DJWK_LJJC_API30.avd'
$avdIni = Join-Path $env:USERPROFILE '.android/avd/DJWK_LJJC_API30.ini'

if ($args.Count -ne 0) {
    throw 'Unexpected positional arguments. Use -FirstBattle for the optional first-battle flow; target remains adb port 5038 and serial emulator-5560 only.'
}

$adbCandidates = @(
    'C:\Users\Administrator\AppData\Local\Android\Sdk\platform-tools\adb.exe'
)
$adb = $adbCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $adb) {
    $adbCommand = Get-Command adb -CommandType Application -ErrorAction Stop
    $adb = $adbCommand.Source
}
$pythonCommand = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
$python = $pythonCommand.Source
$emulatorCandidates = @(
    'C:\Users\Administrator\AppData\Local\Android\Sdk\emulator\emulator.exe'
)
$emulator = $emulatorCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $emulator) {
    throw 'The official Android emulator.exe was not found at the configured SDK path.'
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
    return Invoke-Adb -Arguments (@('-P', "$AdbPort", '-s', $Serial, 'shell') + $Command)
}

function Test-Guest {
    param([Parameter(Mandatory)][string[]]$Command)
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'shell') + $Command
    # A missing/offline fixed emulator is an expected probe result.  With
    # $ErrorActionPreference='Stop', merging native stderr into the pipeline
    # turns that probe into an exception and prevents Start-FixedEmulator.
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

function Stop-LegacyWatchdogGuard {
    # Older launcher revisions could leave this exact shell loop orphaned in
    # the AVD.  It kills TPNS and makes the client look unresponsive.  The
    # current launcher relies on TPNS's official compatibility setting, so
    # clean only this repository-owned path.  Never match generic shells,
    # xg_service_v3, or any other watchdog process.
    $rows = @(Invoke-Guest -Command @('ps', '-A', '-o', 'PID,ARGS'))
    foreach ($row in $rows) {
        $text = ([string]$row).Trim()
        $match = [regex]::Match($text, '^\s*(\d+)\s+.*' + [regex]::Escape($LegacyWatchdogGuardPath) + '(?:\s|$)')
        if ($match.Success) {
            try {
                Invoke-Guest -Command @('kill', '-9', $match.Groups[1].Value) | Out-Null
            } catch {
                # The loop may exit between ps and kill; in that case the
                # desired state (no legacy guard) is already true.
            }
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
    $packagePath = (Invoke-Guest -Command @('pm', 'path', $Package)) -join "`n"
    if ($packagePath -notmatch "(?m)^package:") {
        throw "Original package $Package is not installed on the fixed target."
    }
}

function Ensure-FridaServer {
    if (-not (Test-Guest -Command @('test', '-x', $FridaGuestPath))) {
        throw "Missing executable guest Frida server: $FridaGuestPath"
    }

    $running = Test-Guest -Command @('pidof', 'ljjc-frida-server')
    if (-not $running) {
        # Start only the fixed, pre-installed server inside the fixed emulator.
        Invoke-Guest -Command @('sh', '-c', "nohup $FridaGuestPath -l 127.0.0.1:$FridaGuestPort >/dev/null 2>&1 &") | Out-Null
        for ($attempt = 0; $attempt -lt 10; $attempt++) {
            Start-Sleep -Milliseconds 300
            $running = Test-Guest -Command @('pidof', 'ljjc-frida-server')
            if ($running) { break }
        }
    }
    if (-not $running) {
        throw "Fixed guest Frida server did not become ready at $FridaGuestPath"
    }

    $forward = (Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'forward', '--list')) -join "`n"
    if ($forward -notmatch "(?m)\b(?:$Serial\s+)?tcp:$FridaHostPort\s+tcp:$FridaGuestPort\b") {
        Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'forward', "tcp:$FridaHostPort", "tcp:$FridaGuestPort") | Out-Null
    }
}

function Ensure-LocalReverse {
    # The local fixture service binds on the host.  The client uses
    # 127.0.0.1 inside the dedicated emulator, so these ADB reverse rules are
    # part of the local-service contract.  Some emulator restarts silently
    # drop reverse rules; verify them immediately before launching the APK
    # instead of leaving the client in an endless DirAccount timeout loop.
    $mappings = @(
        @{ Local = 19080; Remote = 19080 },
        @{ Local = 9102; Remote = 19102 }
    )
    $reverse = (Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--list')) -join "`n"
    foreach ($mapping in $mappings) {
        $pattern = "(?m)(?:^|\s)tcp:$($mapping.Local)\s+tcp:$($mapping.Remote)(?:\s|$)"
        if ($reverse -notmatch $pattern) {
            Invoke-Adb -Arguments @(
                '-P', "$AdbPort", '-s', $Serial, 'reverse',
                "tcp:$($mapping.Local)", "tcp:$($mapping.Remote)"
            ) | Out-Null
        }
    }
    $verified = (Invoke-Adb -Arguments @('-P', "$AdbPort", '-s', $Serial, 'reverse', '--list')) -join "`n"
    foreach ($mapping in $mappings) {
        $pattern = "(?m)(?:^|\s)tcp:$($mapping.Local)\s+tcp:$($mapping.Remote)(?:\s|$)"
        if ($verified -notmatch $pattern) {
            throw "ADB reverse mapping missing for local service: tcp:$($mapping.Local) -> tcp:$($mapping.Remote). Reply: $verified"
        }
    }
    Write-Output 'Verified local-service ADB reverse mappings: 19080->19080 and 9102->19102'
}

function Ensure-TpnsWatchdogCompatibility {
    # Tencent TPNS ships a newer watchdog implementation that starts
    # xg_watchdog through libxguardian.  On this dedicated Android 11 x86
    # AVD the old native close() calls are incompatible with bionic fdsan and
    # can take down the client before the local service is usable.  TPNS
    # exposes an official compatibility setting for this case; keep it in
    # the app's own settings provider instead of modifying the APK or
    # disabling unrelated security checks.
    $settingsPath = "/data/user/0/$Package/shared_prefs/.tpns.settings.xml.xml"
    # A fresh install may not have created the TPNS preferences file yet;
    # grep's normal "not found" exit code must not abort the launcher before
    # the provider has a chance to create it.
    $existing = ''
    if (Test-Guest -Command @('grep', '-q', 'enableNewWd', $settingsPath)) {
        $existing = ((Invoke-Guest -Command @('grep', 'enableNewWd', $settingsPath)) -join ' ').Trim()
    }
    if ($existing -notmatch 'name="com.tencent.tpus.enableNewWd"\s+value="0"') {
        Invoke-Guest -Command @(
            'content', 'insert',
            '--uri', 'content://com.tencent.tmgp.fcicv.TPUSH_PROVIDER/com.tencent.tpus.enableNewWd/integer',
            '--bind', 'com.tencent.tpus.enableNewWd:i:0'
        ) | Out-Null
    }

    # Verify the persisted value before starting the Activity.  The setting
    # is deliberately narrow: it only selects TPNS's legacy-compatible
    # watchdog path; guest auth, gameplay and result settlement remain the
    # original client/server flows.
    $verified = ''
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        if (Test-Guest -Command @('grep', '-q', 'enableNewWd', $settingsPath)) {
            $verified = ((Invoke-Guest -Command @('grep', 'enableNewWd', $settingsPath)) -join ' ').Trim()
            if ($verified -match 'name="com.tencent.tpus.enableNewWd"\s+value="0"') {
                break
            }
        }
        Start-Sleep -Milliseconds 100
    }
    if ($verified -notmatch 'name="com.tencent.tpus.enableNewWd"\s+value="0"') {
        throw "TPNS compatibility setting was not persisted at $settingsPath; got: $verified"
    }
    Write-Host 'TPNS new watchdog compatibility setting verified: com.tencent.tpus.enableNewWd=0'
}

function Get-ClientPid {
    # `pidof` also returns the TPNS provider/service processes.  Attaching
    # instrumentation to one of those leaves the real Activity unobserved,
    # so select only the exact main process name from a process table.
    $rows = @(Invoke-Guest -Command @('ps', '-A', '-o', 'PID,ARGS'))
    foreach ($row in $rows) {
        $match = [regex]::Match(([string]$row), '^\s*(\d+)\s+' + [regex]::Escape($Package) + '\s*$')
        if ($match.Success) { return [int]$match.Groups[1].Value }
    }
    return $null
}

function Assert-ClientAliveAndForeground {
    param([Parameter(Mandatory)][int]$ExpectedPid)
    $currentPid = Get-ClientPid
    if (-not $currentPid) {
        throw "Original client process exited while waiting for the first-battle flow (expected PID $ExpectedPid)."
    }
    if ($currentPid -ne $ExpectedPid) {
        throw "Original client PID changed while waiting for the first-battle flow (expected $ExpectedPid, got $currentPid)."
    }
    $activityState = (Invoke-Guest -Command @('dumpsys', 'activity', 'activities')) -join "`n"
    if ($activityState -notmatch 'mResumedActivity:.*com\.tencent\.tmgp\.fcicv/com\.actgames\.fci\.MainActivity') {
        throw "Original MainActivity is no longer foreground while waiting for the first-battle flow."
    }
}

function Test-SerialConnected {
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'get-state')
    # Treat "device not found" as false so the existing dedicated AVD can be
    # started below.  Do not relax error handling for commands that mutate or
    # validate the selected target.
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

function Get-SerialAvdName {
    $arguments = @('-P', "$AdbPort", '-s', $Serial, 'emu', 'avd', 'name')
    $reply = @(& $adb @arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($reply -join "`n").Trim()
}

function Start-FixedEmulator {
    if (-not (Test-Path -LiteralPath $avdDir -PathType Container)) {
        throw "Missing dedicated AVD directory: $avdDir"
    }
    if (-not (Test-Path -LiteralPath $avdIni -PathType Leaf)) {
        throw "Missing registered AVD .ini; refusing to create or select another AVD: $avdIni"
    }
    $iniText = Get-Content -LiteralPath $avdIni -Raw
    $resolvedAvdDir = (Resolve-Path -LiteralPath $avdDir).Path.TrimEnd('\')
    $iniPathMatch = [regex]::Match($iniText, '(?m)^path=(.+?)\s*$')
    if (-not $iniPathMatch.Success) {
        throw "AVD .ini has no path entry: $avdIni"
    }
    $iniAvdDir = [System.IO.Path]::GetFullPath($iniPathMatch.Groups[1].Value.Trim()).TrimEnd('\')
    if (-not [string]::Equals($iniAvdDir, $resolvedAvdDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Registered AVD .ini does not point to the dedicated local AVD. Expected $resolvedAvdDir; got $iniAvdDir"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $avdDir 'config.ini') -PathType Leaf)) {
        throw "Dedicated AVD is missing config.ini: $avdDir"
    }

    # ProcessStartInfo gives only this emulator process the custom ADB port;
    # no global SDK/ADB environment variable is changed.  CreateNoWindow hides
    # the helper console, while the emulator GUI remains visible because
    # -no-window is intentionally not supplied.
    $emulatorArgs = @(
        '-avd', $ExpectedAvd,
        '-port', "$EmulatorPort",
        '-gpu', 'host',
        '-no-snapshot',
        '-no-boot-anim',
        '-camera-back', 'none',
        '-camera-front', 'none'
    )
    $quotedArgs = $emulatorArgs | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\"') + '"'
    }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $emulator
    $startInfo.Arguments = $quotedArgs -join ' '
    $startInfo.WorkingDirectory = $workspace
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables['ANDROID_ADB_SERVER_PORT'] = "$AdbPort"
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if (-not $process) {
        throw "Failed to start the official emulator for $ExpectedAvd"
    }
    Write-Host "Started existing $ExpectedAvd on emulator-$EmulatorPort; emulator window remains visible."
    return $process
}

function Wait-FixedTarget {
    param([Parameter(Mandatory)][int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-SerialConnected) {
            $avdReply = Get-SerialAvdName
            if ($avdReply -and $avdReply -notmatch "(?m)^\s*(?:name:\s*)?$([regex]::Escape($ExpectedAvd))\s*$") {
                throw "Unexpected emulator AVD name; expected exactly $ExpectedAvd. Reply: $avdReply"
            }
            try {
                Assert-FixedTarget
                return
            } catch {
                $lastError = $_.Exception.Message
            }
        }
        Start-Sleep -Seconds 1
    }
    if ($lastError) {
        throw "Timed out waiting for fixed target $Serial ($ExpectedAvd): $lastError"
    }
    throw "Timed out waiting for fixed target $Serial ($ExpectedAvd) to connect to adb server port $AdbPort"
}

function Wait-GuestButton {
    param([Parameter(Mandatory)][int]$ClientPid)
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    for ($attempt = 1; $attempt -le $GuestAttempts; $attempt++) {
        $logPath = Join-Path $logDir "guest-attempt-$attempt.jsonl"
        $stderrPath = Join-Path $logDir "guest-attempt-$attempt.stderr.log"
        Remove-Item -LiteralPath $logPath, $stderrPath -Force -ErrorAction SilentlyContinue
        Write-Host "Waiting for Lua/UI readiness; late guest-button injection attempt $attempt/$GuestAttempts..."

        $instrumentArgs = @(
            $instrumentScript,
            '--pid', "$ClientPid",
            '--script', $fridaScript,
            '--log', $logPath,
            '--duration', "$InstrumentationDuration",
            '--lua-query-file', $luaQueryFile,
            '--quiet'
        )
        $worker = Start-Process -FilePath $python -ArgumentList $instrumentArgs -WindowStyle Hidden -PassThru `
            -RedirectStandardError $stderrPath

        $deadline = [DateTime]::UtcNow.AddSeconds($InstrumentationDuration + 5)
        $enabled = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            if (Test-Path -LiteralPath $logPath) {
                $content = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
                # Keep accepting the original compatibility message while
                # recognizing the persistent-account wording used by the
                # current local guest entry.  The Lua query is the source of
                # truth here; both messages mean the Login window has been
                # patched and the guest button is active.
                if ($content -match 'Local guest button enabled; original guest protocol will use localhost' -or
                    $content -match 'Persistent local account button enabled; guest-compatible protocol uses localhost') {
                    $enabled = $true
                    break
                }
                if ($content -match '"kind":\s*"lua_error"') {
                    break
                }
            }
            if ($worker.HasExited -and (Test-Path -LiteralPath $logPath)) { break }
            Start-Sleep -Milliseconds 500
        }

        if (-not $worker.HasExited) {
            # A normal five-second exit lets run_instrumentation.py detach cleanly.
            # Only reap a hung worker after its bounded grace period.
            Wait-Process -Id $worker.Id -Timeout 3 -ErrorAction SilentlyContinue
        }
        if (-not $worker.HasExited) {
            Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
        }
        if ($enabled) {
            Write-Host "Guest button enabled by original Lua runtime; evidence: $logPath"
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Invoke-BoundedLuaQuery {
    param(
        [Parameter(Mandatory)][int]$ClientPid,
        [Parameter(Mandatory)][string]$QueryPath,
        [Parameter(Mandatory)][string]$LogStem
    )
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logPath = Join-Path $logDir "$LogStem.jsonl"
    $stderrPath = Join-Path $logDir "$LogStem.stderr.log"
    Remove-Item -LiteralPath $logPath, $stderrPath -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $QueryPath -PathType Leaf)) {
        throw "Missing Lua query file: $QueryPath"
    }

    $instrumentArgs = @(
        $instrumentScript,
        '--pid', "$ClientPid",
        '--script', $fridaScript,
        '--log', $logPath,
        '--duration', "$InstrumentationDuration",
        '--lua-query-file', $QueryPath,
        '--quiet'
    )
    $worker = Start-Process -FilePath $python -ArgumentList $instrumentArgs -WindowStyle Hidden -PassThru `
        -RedirectStandardError $stderrPath
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds($InstrumentationDuration + 5)
        while ([DateTime]::UtcNow -lt $deadline) {
            if ($worker.HasExited) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $worker.HasExited) {
            Wait-Process -Id $worker.Id -Timeout 3 -ErrorAction SilentlyContinue | Out-Null
        }
        if (-not $worker.HasExited) {
            Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
        }
    } finally {
        if (-not $worker.HasExited) {
            Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $logPath -PathType Leaf) {
        $result = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
        if ($result) { return $result }
    }
    # Do not silently turn a Frida attach failure into an endless readiness
    # loop.  The stderr text is local diagnostic data; surfacing it lets the
    # caller distinguish an unavailable Lua hook from a server response that
    # is merely waiting for the next scene.
    if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
        $stderr = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        if ($stderr) { return "__lua_query_error__ $stderr" }
    }
    return ''
}

function Wait-FirstBattleGuestAuth {
    param([Parameter(Mandatory)][int]$ClientPid)
    $deadline = [DateTime]::UtcNow.AddSeconds($FirstBattleLoginTimeoutSeconds)
    $attempt = 0
    while ([DateTime]::UtcNow -lt $deadline) {
        $attempt++
        Assert-ClientAliveAndForeground -ExpectedPid $ClientPid
        Write-Host "First-battle guest auth query $attempt (bounded ${InstrumentationDuration}s)..."
        $content = Invoke-BoundedLuaQuery -ClientPid $ClientPid -QueryPath $firstBattleGuestQueryFile -LogStem "first-battle-guest-$attempt"
        if ($content -match '__lua_query_error__') {
            throw "The original client Lua query failed; see $logDir"
        }
        if ($content -match 'FirstBattle local guest auth requested' -or
            $content -match 'FirstBattle guest login already complete') {
            Write-Host 'Original guest-auth flow was requested or is already complete.'
            return
        }
        if ($content -match 'FirstBattle requires accepted license') {
            throw 'The original client has not accepted its license; -FirstBattle will not auto-accept it.'
        }
        if ($content -match '"kind":\s*"lua_error"') {
            Write-Host "Guest-auth Lua query did not run yet; see $logDir"
        } else {
            Write-Host 'Guest-auth flow is not ready yet; waiting for the original Login conditions.'
        }
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for the original guest-auth flow; see $logDir"
}

function Wait-FirstBattleReady {
    param([Parameter(Mandatory)][int]$ClientPid)
    $deadline = [DateTime]::UtcNow.AddSeconds($FirstBattleReadyTimeoutSeconds)
    $attempt = 0
    while ([DateTime]::UtcNow -lt $deadline) {
        $attempt++
        Assert-ClientAliveAndForeground -ExpectedPid $ClientPid
        Write-Host "First-battle readiness query $attempt (bounded ${InstrumentationDuration}s)..."
        $content = Invoke-BoundedLuaQuery -ClientPid $ClientPid -QueryPath $firstBattleQueryFile -LogStem "first-battle-ready-$attempt"
        if ($content -match '__lua_query_error__') {
            throw "The original client Lua readiness query failed; see $logDir"
        }
        if ($content -match 'Requested original story dungeon 2012') {
            Write-Host 'Requested original story dungeon 2012 after SceneCity and chapter data became available.'
            return
        }
        if ($content -match 'FirstBattle waiting scene=') {
            Write-Host 'Waiting for SceneCity after local guest login...'
        } elseif ($content -match 'FirstBattle waiting chapter data') {
            Write-Host 'Waiting for local chapter data...'
        } elseif ($content -match '"kind":\s*"lua_error"') {
            Write-Host "First-battle readiness query failed; see $logDir"
        }
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for SceneCity/chapter data for first battle; see $logDir"
}

Write-Output "Ensuring the dedicated adb server on port $AdbPort is available..."
Invoke-Adb -Arguments @('-P', "$AdbPort", 'start-server') | Out-Null

if (Test-SerialConnected) {
    Write-Output "The fixed target $Serial is already connected; validating it without restarting."
} else {
    Write-Output "The fixed target is not running; starting the existing $ExpectedAvd AVD only..."
    $emulatorProcess = Start-FixedEmulator
}
Wait-FixedTarget -TimeoutSeconds $EmulatorBootTimeoutSeconds
Stop-LegacyWatchdogGuard

if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) { throw "Missing service script: $serverScript" }
if (-not (Test-Path -LiteralPath $prepareScript -PathType Leaf)) { throw "Missing prepare script: $prepareScript" }
if (-not (Test-Path -LiteralPath $instrumentScript -PathType Leaf)) { throw "Missing instrumentation script: $instrumentScript" }
if (-not (Test-Path -LiteralPath $fridaScript -PathType Leaf)) { throw "Missing Frida script: $fridaScript" }
if (-not (Test-Path -LiteralPath $luaQueryFile -PathType Leaf)) { throw "Missing Lua query file: $luaQueryFile" }
if ($FirstBattle) {
    if (-not (Test-Path -LiteralPath $firstBattleGuestQueryFile -PathType Leaf)) { throw "Missing first-battle guest Lua query: $firstBattleGuestQueryFile" }
    if (-not (Test-Path -LiteralPath $firstBattleQueryFile -PathType Leaf)) { throw "Missing first-battle Lua query: $firstBattleQueryFile" }
}

Write-Output 'Starting the local fixture service...'
& (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $serverScript
if ($LASTEXITCODE -ne 0) { throw "start-service.ps1 failed with exit code $LASTEXITCODE" }

Write-Output 'Applying isolated AVD routing (hosts, reverse, and LJJC_LOCAL firewall chains)...'
& (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $prepareScript
if ($LASTEXITCODE -ne 0) { throw "prepare-avd.ps1 failed with exit code $LASTEXITCODE" }

Ensure-LocalReverse
Ensure-FridaServer
Ensure-LocalReverse
Ensure-TpnsWatchdogCompatibility
# The official TPNS compatibility setting above prevents the incompatible
# native child from being started.  Do not run the old kill-loop guard here:
# killing that child after it starts can make TPNS terminate the Unity client.

Write-Output "Launching original Activity $Package/$Activity..."
Invoke-Guest -Command @('am', 'force-stop', $Package) | Out-Null
Invoke-Guest -Command @('am', 'start', '-n', "$Package/$Activity") | Out-Null

$clientPid = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    $clientPid = Get-ClientPid
    if ($clientPid) { break }
}
if (-not $clientPid) { throw "Original client did not expose a process after launching $Package/$Activity" }
Write-Output "Original client PID $clientPid is running; allowing its Lua/UI to settle before late attach."
Start-Sleep -Seconds $UiSettleSeconds

if (-not (Wait-GuestButton -ClientPid $clientPid)) {
    throw "Lua/UI did not expose the local guest button after $GuestAttempts attempts. See $logDir"
}

if ($FirstBattle) {
    Write-Output 'First-battle mode enabled; using the original Login Lua flow without changing license state...'
    Wait-FirstBattleGuestAuth -ClientPid $clientPid
    Wait-FirstBattleReady -ClientPid $clientPid
    Write-Output 'First-battle request submitted. Continue observing the original client for the verified battle and its normal result.'
}

Write-Output 'Local startup completed. The original client and local guest-auth seam are ready; current real functionality and limitations are documented in README.md, and full playability is not guaranteed.'
