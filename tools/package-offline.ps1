[CmdletBinding()]
param(
    # Keep the default empty for Windows PowerShell 5.1.  In that host
    # automatic variables such as $PSScriptRoot are not populated while
    # parameter defaults are being evaluated, which made a direct invocation
    # fail before the script body ran.
    [string]$Root = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($Root)) {
    $scriptPath = $MyInvocation.MyCommand.Path
    if ([string]::IsNullOrWhiteSpace($scriptPath) -and -not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $scriptPath = Join-Path $PSScriptRoot "package-offline.ps1"
    }
    # package-offline.ps1 lives in <checkout>\tools, so the package root is
    # the parent of the script directory.
    $scriptDirectory = Split-Path -Parent $scriptPath
    $Root = Split-Path -Parent $scriptDirectory
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$rootUri = [Uri]($rootPath.TrimEnd('\') + '\')

function Get-RelativePath {
    param([Parameter(Mandatory)][string]$FullPath)
    # Windows PowerShell 5.1 does not provide
    # [System.IO.Path]::GetRelativePath.  URI resolution works on both the
    # inbox Windows PowerShell and PowerShell 7 while preserving spaces and
    # non-ASCII filenames.
    $relativeUri = $script:rootUri.MakeRelativeUri([Uri]$FullPath)
    return [Uri]::UnescapeDataString($relativeUri.ToString()).Replace('/', '\')
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $rootPath ("delivery\ljjc-offline-server-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$outputPath = [System.IO.Path]::GetFullPath($Output)
$outputParent = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null

# Stage only source, configuration, tests, and documentation.  Runtime state,
# APKs, private keys, logs, screenshots, and generated databases are excluded
# even if a caller points the script at a checkout containing them.
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("ljjc-package-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
# The protobuf descriptor set is a runtime input for game.py and must travel
# with the server package.  It is a data schema, not a client binary or key.
$allowedExtensions = @(".py", ".ps1", ".cmd", ".toml", ".yaml", ".yml", ".json", ".md", ".txt", ".proto", ".pb")
$excludedSegments = @(
    ".git", ".pytest_cache", "__pycache__", ".venv", "venv", "runtime", "state", "backups",
    "logs", "screenshots", "artifacts", "delivery", "analysis", "tmp"
)
# These entries contain a path separator and therefore cannot be compared to
# one item from ``$segments``. Keep them as normalized relative prefixes and
# test them separately below; otherwise a package built from a populated
# checkout would accidentally include APK decode output and signing material.
$excludedPrefixes = @(
    "client-patch/work", "client-patch/output", "client-patch/keystore",
    "data/golden-traces"
)
$excludedExtensions = @(".apk", ".aab", ".apks", ".jks", ".keystore", ".p12", ".pem", ".key", ".sqlite3", ".db", ".log", ".jsonl", ".png", ".jpg", ".jpeg")
$manifest = [System.Collections.Generic.List[object]]::new()

try {
    $files = Get-ChildItem -LiteralPath $rootPath -Recurse -File -Force | Where-Object {
        $relative = Get-RelativePath -FullPath $_.FullName
        $relativeWindows = $relative.Replace('/', '\')
        $relativeNormalized = $relativeWindows.Replace('\', '/')
        $segments = $relativeWindows -split '[\\/]+'
        $hasExcludedSegment = $false
        foreach ($segment in $segments) {
            if ($excludedSegments -contains $segment) { $hasExcludedSegment = $true; break }
        }
        if ($hasExcludedSegment) { return $false }
        $hasExcludedPrefix = $false
        foreach ($prefix in $excludedPrefixes) {
            if ($relativeNormalized -eq $prefix -or $relativeNormalized.StartsWith($prefix + '/')) {
                $hasExcludedPrefix = $true
                break
            }
        }
        if ($hasExcludedPrefix) { return $false }
        if ($excludedExtensions -contains $_.Extension.ToLowerInvariant()) { return $false }
        if ($allowedExtensions -notcontains $_.Extension.ToLowerInvariant()) { return $false }
        return $true
    }
    foreach ($file in $files) {
        $relative = Get-RelativePath -FullPath $file.FullName
        $destination = Join-Path $stage $relative
        $destinationParent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        $manifest.Add([ordered]@{ path = ($relative -replace '[\\/]+', '/'); sha256 = $hash; bytes = $file.Length })
    }
    $manifestPath = Join-Path $stage "DELIVERY-MANIFEST.json"
    $manifestDocument = [ordered]@{
        format = 1
        generatedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $rootPath
        files = $manifest
        excluded = @("APK and generated client binaries", "private signing keys/certificates", "runtime state/databases/backups", "logs and screenshots", "git metadata and caches")
    }
    $manifestDocument | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $outputPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $outputPath).Hash.ToLowerInvariant()
    [pscustomobject]@{ archive = $outputPath; sha256 = $archiveHash; files = $manifest.Count; excludedSensitiveInputs = $true } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
}
